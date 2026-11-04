"""Timelines: what each fact identity said, and when, across the crawls.

For every snapshot the truth rules (`rules.truth_key`) pick one winning row per fact identity
from every live row of that identity in the site as it stood then; the winner never depends on
which rows a question happened to pull up. Consecutive snapshots whose winners state the same
figures form one *span* of the fact's timeline:

    Span(figures, first snapshot, last snapshot, valid_from, winning row)

`valid_from` is the date the winning page prints ("Rates are effective as of 06/02/2026") when
it has one, else the crawl date that first showed the figures; a span ends where the next one
starts, or is still current. "What changed since May?", "what did it pay before it went up?"
and the contradiction report are lookups on these spans.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .rules import is_live, truth_key

RATE_METRICS = ("rate", "dividend rate", "APY", "APR", "fee")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def figures(row) -> tuple:
    """The figures a row states, as sorted (metric, numbers) pairs over the rate and fee metrics."""
    out = []
    for m, v in sorted(row.values):
        if m in RATE_METRICS and ("%" in v or "$" in v):
            out.append((m, tuple(float(n.replace(",", "")) for n in _NUMBER.findall(v))))
    return tuple(out)


def disagree(a, b) -> bool:
    """Do two rows of one fact contradict each other? Only on a metric both state, and not when one
    figure list just repeats part of the other (a '15.50%' copy of '15.50% - 21.00%')."""
    fa, fb = dict(figures(a)), dict(figures(b))
    for m in fa.keys() & fb.keys():
        x, y = fa[m], fb[m]
        if x != y and not (set(x) <= set(y) or set(y) <= set(x)):
            return True
    return False


_DASH = re.compile(r"\s*[\u2013\u2014-]\s*")


def value_text(row, metric: Optional[str] = None, full: bool = False) -> str:
    """A row's figures as one value: the asked metric, else the APY for deposits (the dividend rate
    follows from it), else the rate and APR together, else the fee. `full` states every rate metric
    the row prints ('3.86% dividend rate / 3.90% APY'), as the contradiction report does."""
    vals = {m: _DASH.sub(" - ", v) for m, v in row.values if "%" in v or "$" in v}
    if full:
        return " / ".join(f"{vals[m]} {m}" for m in ("rate", "dividend rate", "APY", "APR", "fee") if m in vals)
    groups = (((metric,),) if metric and metric != "rate" else ()) + (("APY",), ("rate", "APR"), ("APR",), ("dividend rate",), ("fee",))
    for group in groups:
        got = [vals[m] if re.search(r"\b(apr|apy)\b", vals[m], re.I) else f"{vals[m]} {m}" for m in group if m in vals]
        if got:
            return " / ".join(got)
    return ""


@dataclass
class Span:
    figures: tuple
    first: int            # first snapshot index with these figures
    last: int             # last snapshot index with these figures
    valid_from: str       # printed effective date, else first crawl date
    row: object           # the winning row (newest snapshot of the span)


class Timeline:
    def __init__(self, ledger, ids: dict) -> None:
        self.ledger = ledger
        self.ids = ids
        self._winners: dict[tuple, dict] = {}
        self._members: dict[tuple, dict] = {}
        self._spans: Optional[dict] = None

    def key(self, row):
        return self.ids.get(row.rid)

    # ---- per snapshot --------------------------------------------------------------------
    def members(self, k: int, on_date: Optional[str] = None) -> dict:
        """FactKey -> live table rows of that fact in the site as it stood at snapshot k."""
        on_date = on_date or self.ledger.snapshots[k].taken_on
        if (k, on_date) not in self._members:
            out: dict = defaultdict(list)
            for r in self.ledger.view(k):
                key = self.ids.get(r.rid)
                if key is not None and figures(r) and is_live(r, on_date):
                    out[key].append(r)
            self._members[(k, on_date)] = dict(out)
        return self._members[(k, on_date)]

    def winners(self, k: int, on_date: Optional[str] = None) -> dict:
        """FactKey -> the row the truth rules pick at snapshot k."""
        on_date = on_date or self.ledger.snapshots[k].taken_on
        if (k, on_date) not in self._winners:
            self._winners[(k, on_date)] = {key: max(rows, key=truth_key) for key, rows in self.members(k, on_date).items()}
        return self._winners[(k, on_date)]

    def winner(self, key, k: int, on_date: Optional[str] = None):
        return self.winners(k, on_date).get(key)

    # ---- across snapshots ----------------------------------------------------------------
    def spans(self, key) -> list[Span]:
        if self._spans is None:
            self._spans = self._build_spans()
        return self._spans.get(key, [])

    def keys(self) -> list:
        if self._spans is None:
            self._spans = self._build_spans()
        return list(self._spans)

    def _build_spans(self) -> dict:
        out: dict = defaultdict(list)
        for k, snap in enumerate(self.ledger.snapshots):
            for key, row in self.winners(k).items():
                spans = out[key]
                figs = figures(row)
                if spans and spans[-1].last == k - 1 and not disagree(spans[-1].row, row):
                    spans[-1].last, spans[-1].row, spans[-1].figures = k, row, figs
                    continue
                start = row.effective if row.effective and row.effective <= snap.taken_on else snap.taken_on
                if spans and start <= spans[-1].valid_from:
                    start = snap.taken_on
                spans.append(Span(figs, k, k, start, row))
        return dict(out)

    def warm(self) -> None:
        """Pick every snapshot's winners and build the spans now (at load time, which is untimed)."""
        for k in range(len(self.ledger.snapshots)):
            self.winners(k)
        self.keys()

    def current(self, key) -> bool:
        """Is the fact still stated in the newest snapshot?"""
        spans = self.spans(key)
        return bool(spans) and spans[-1].last == len(self.ledger.snapshots) - 1
