"""conflicts(): every place the site contradicts itself, the value believed right, and why.

Two kinds of contradiction are reported:

* rate-table facts: the same product and tier (same term, model-year range, balance or loan
  amount, variant) printed with different values, either on two pages at once or on the same
  page across crawls. One conflict is reported per product and term, listing every value seen;
  the resolution is what the truth rules pick for the newest snapshot.
* repeated statements: one sentence template (typically press-release boilerplate such as
  "It has over 600 employees and N branch office locations") printed with different numbers.
  The newest dated copy wins.

Tiers are never conflicts: a $10,000 tier and a $25,000 tier of the same account, or a rate and
an APY of the same row, are different facts.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .rules import explain, is_live, product_names, same_fact, truth_key
from .vocab import STOPWORDS, stem, tokens

_RATE_METRICS = ("rate", "dividend rate", "APY", "APR", "fee")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def find_conflicts(ledger, contract) -> list:
    if not ledger.snapshots:
        return []
    ranked = _table_conflicts(ledger, contract) + _statement_conflicts(ledger, contract)
    return _merge_duplicates(sorted(ranked, key=lambda item: (item[0], item[2].topic)))


def _merge_duplicates(ranked: list) -> list:
    """The same change shown under several product names (a special and its IRA twin: same term
    and tier, same values) is one contradiction; keep the plainest product's copy (fewest variant
    words)."""
    seen, out = set(), []
    for _, tier, c in ranked:
        key = (tier, frozenset(c.values))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return sorted(out, key=lambda c: c.topic)


# ---- rate tables ---------------------------------------------------------------------------
def _numbers(row) -> tuple:
    """The rate figures a row states as (metric, number) pairs, the first percentage of each value."""
    out = []
    for m, v in sorted(row.values):
        if m in _RATE_METRICS and "%" in v:
            n = _NUMBER.search(v)
            out.append((m, float(n.group(0).replace(",", ""))))
    return tuple(out)


def _value_text(row) -> list[str]:
    return [f"{_DASH.sub(' - ', v)} {m}" for m, v in row.values if m in _RATE_METRICS and "%" in v]


_DASH = re.compile(r"\s*[\u2013\u2014-]\s*")


def _clusters(rows: list, names: dict) -> list[list]:
    """Group rows of one product and term: same term, variant and product name."""
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if names[r.rid] is not None:
            groups[(r.quals.terms, r.quals.variants, names[r.rid])].append(r)
    return list(groups.values())


def _disagree(a, b, names: dict) -> bool:
    """Two rows state the same fact with a different figure for a metric both state."""
    if not same_fact(a, b, names):
        return False
    na, nb = defaultdict(list), defaultdict(list)
    for m, n in _numbers(a):
        na[m].append(n)
    for m, n in _numbers(b):
        nb[m].append(n)
    return any(na[m] != nb[m] for m in na.keys() & nb.keys())


def _table_conflicts(ledger, c) -> list:
    latest = len(ledger.snapshots) - 1
    seen, rows = set(), []
    for k in range(len(ledger.snapshots)):
        for row in ledger.view(k):
            if row.kind == "table" and _numbers(row) and row.rid not in seen:
                seen.add(row.rid)
                rows.append(row)
    current_ids = {r.rid for r in ledger.view(latest)}
    on_date = ledger.snapshots[latest].taken_on
    names = product_names(rows)
    out = []
    for cluster in _clusters(rows, names):
        cluster.sort(key=lambda r: r.rid)
        disagreements = [(a, b) for i, a in enumerate(cluster) for b in cluster[i + 1:] if _disagree(a, b, names)]
        if not disagreements:
            continue
        live = [r for r in cluster if r.rid in current_ids and is_live(r, on_date)]
        if not live:
            continue
        winner = max(live, key=truth_key)
        winners = sorted({r.rid: r for r in live if r.page.vid == winner.page.vid and r.caption == winner.caption}.values(),
                         key=lambda r: r.pos)
        win_ids = {r.rid for r in winners}
        losers = [r for pair in disagreements for r in pair if r.rid not in win_ids]
        if not losers:
            continue
        loser = max(losers, key=truth_key)
        values, sources, seen_vals = [], [], set()
        for r in sorted(cluster, key=lambda r: (r.rid not in win_ids, -r.page.first_snapshot, r.page.url, r.pos)):
            for v in _value_text(r):
                if v not in seen_vals:
                    seen_vals.add(v)
                    values.append(v)
            if len(sources) < 6 and r.page.url not in {s.url for s in sources}:
                sources.append(c.Source(url=r.page.url, snapshot_id=ledger.snapshots[r.page.first_snapshot].snapshot_id,
                                        quote=r.text[:200]))
        resolution = "; ".join(f"{r.label}{' (' + r.qual_text + ')' if r.qual_text else ''}: {', '.join(_value_text(r))}"
                               for r in winners[:6])
        q = winner.quals
        out.append(((len(q.variants), len(_topic(winner))), ("table", q.terms, q.years, q.amount),
                    c.Conflict(topic=_topic(winner), values=values, sources=sources, resolution=resolution,
                               reason=explain(winner, loser) + "; tiers and rate-vs-APY columns are kept as separate facts.")))
    return out


def _topic(row) -> str:
    bits = [row.caption, row.label]
    desc = row.quals.describe()
    if desc:
        bits.append(desc)
    metrics = sorted({m for m, _ in _numbers(row)})
    return f"{' - '.join(b for b in bits if b)} ({'/'.join(metrics)})"


# ---- repeated statements -------------------------------------------------------------------
_ORDINALS = r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
_COUNTS = re.compile(rf"\b(?:{_ORDINALS})\b|\d[\d,]*(?:\.\d+)?", re.I)
_JUNK = re.compile(r'^["{]|min read|^\W|\b(19|20)\d\d-\d\d-\d\d')


def _template(text: str) -> tuple:
    masked = _COUNTS.sub(" 0 ", text)
    return tuple(t for t in tokens(masked) if t not in STOPWORDS and t not in _HEDGES)


_HEDGES = {stem(w) for w in "more than over nearly about almost exceeding approximately".split()}
_BOILERPLATE_PAGES = 5   # a statement printed on this many dated pages is boilerplate, not a one-off detail
_EVENT = re.compile(r"\b(will|was|were|happened|plans?|planned)\b", re.I)
_ORDINAL = re.compile(rf"^(?:{_ORDINALS})$", re.I)


def _counts(text: str) -> tuple:
    return tuple(m.group(0).lower() for m in _COUNTS.finditer(text))


def _statement_conflicts(ledger, c) -> list:
    """Statements repeated on several dated pages (boilerplate) whose figures disagree."""
    latest = len(ledger.snapshots) - 1
    groups: dict[tuple, list] = defaultdict(list)
    for row in ledger.view(latest):
        if row.kind != "text" or not row.page.published or _JUNK.search(row.text) or len(row.text.split()) < 6:
            continue
        if _COUNTS.search(row.text) and len(row.text) < 300:
            groups[_template(row.text)].append(row)
    out = []
    for tmpl, rows in groups.items():
        if len({r.page.url for r in rows}) < 2 or sum(t == "0" for t in tmpl) == 0 or len(tmpl) < 4:
            continue
        by_value: dict[tuple, list] = defaultdict(list)
        for r in rows:
            by_value[_counts(r.text)].append(r)
        differing = _differing_numbers(list(by_value))
        if len(by_value) < 2 or not differing or all(re.fullmatch(r"(19|20)\d\d", k[i]) for k in by_value for i in differing):
            continue
        # A running count ("the fifth time") or a present-tense statement of how things are now
        # ("It has 22 branch office locations") repeated across pages; one-off event details
        # ("winners will be notified by ...") are not contradictions.
        ordinal = any(_ORDINAL.match(k[i]) for k in by_value for i in differing)
        if not ordinal and (len({r.page.url for r in rows}) < _BOILERPLATE_PAGES or any(_EVENT.search(r.text) for r in rows)):
            continue
        winner = max(rows, key=truth_key)
        loser = max((r for r in rows if _counts(r.text) != _counts(winner.text)), key=truth_key)
        values = []
        for key in sorted(by_value, key=lambda k: max(truth_key(r) for r in by_value[k]), reverse=True):
            v = " / ".join(key[i] for i in differing)
            if v not in values:
                values.append(v)
        sources = []
        for key in by_value:
            r = max(by_value[key], key=truth_key)
            sources.append(c.Source(url=r.page.url, snapshot_id=ledger.snapshots[r.page.first_snapshot].snapshot_id, quote=r.text[:200]))
        out.append(((0, len(winner.text)), ("statement",),
                    c.Conflict(topic=winner.text[:120], values=values, sources=sources[:6], resolution=winner.text,
                               reason=explain(winner, loser))))
    return out


def _differing_numbers(keys: list[tuple]) -> list[int]:
    if len({len(k) for k in keys}) > 1:
        return []
    width = min(len(k) for k in keys)
    return [i for i in range(width) if len({k[i] for k in keys}) > 1]


