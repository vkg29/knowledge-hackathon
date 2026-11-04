"""conflicts(): every place the site contradicts itself, the value believed right, and why.

Two kinds of contradiction are reported:

* rate-table facts: one entry per fact identity (`identity.py`: product, kinds, term, tier) whose
  copies disagree, on two pages at once or on the same page across crawls (a rate that changed
  over time is a contradiction between the old page and the new one). The values are only that
  identity's own figures, newest first; the resolution is the single value the truth rules pick
  for the newest snapshot, and the reason names the rule.
* repeated statements: one sentence template (typically press-release boilerplate such as
  "It has over 600 employees and N branch office locations") printed with different numbers.
  The newest dated copy wins.

Tiers are never conflicts: a $10,000 tier and a $25,000 tier of the same account, or a rate and
an APY of the same row, are different identities or metrics.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .rules import explain, truth_key
from .text import long_date
from .timeline import disagree, value_text
from .vocab import STOPWORDS, stem, tokens

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def find_conflicts(ledger, contract) -> list:
    if not ledger.snapshots:
        return []
    ranked = _table_conflicts(ledger, contract) + _statement_conflicts(ledger, contract)
    return _merge_duplicates(sorted(ranked, key=lambda item: (item[0], item[3].topic)))


def _merge_duplicates(ranked: list) -> list:
    """The same change shown under several products (a special and its IRA or business twins: same family,
    term and tier, the same figures) is one contradiction; keep the plainest product's entry. Contradictions
    the newest crawl still shows (a stale copy beside the current one) are listed before changes over time,
    and the newest model years before older ones."""
    seen, out = set(), []
    for _, tier, order, c in ranked:
        key = (tier, frozenset(c.values))
        if key not in seen:
            seen.add(key)
            out.append((order, c.topic, c))
    return [c for _, _, c in sorted(out, key=lambda x: x[:2])]


# ---- rate tables ---------------------------------------------------------------------------
def _table_conflicts(ledger, c) -> list:
    tl = ledger.timeline()
    latest = len(ledger.snapshots) - 1
    rows_of: dict = defaultdict(dict)
    for k in range(len(ledger.snapshots)):
        for key, rows in tl.members(k).items():
            for r in rows:
                rows_of[key][r.rid] = r
    out = []
    for key, by_rid in rows_of.items():
        rows = sorted(by_rid.values(), key=lambda r: r.rid)
        spans = tl.spans(key)
        if not spans or key.family is None:
            continue
        current = spans[-1].last == latest
        winner = spans[-1].row
        losers = [r for r in rows if disagree(winner, r)]
        if not losers:
            continue
        values, sources, seen_vals = [], [], set()
        ordered = sorted(rows, key=lambda r: (r.rid != winner.rid, -r.page.first_snapshot, -r.authority, r.page.url, r.pos))
        for r in ordered:
            v = value_text(r, full=True)
            if v and v not in seen_vals:
                seen_vals.add(v)
                values.append(v)
            if len(sources) < 6 and r.page.url not in {s.url for s in sources}:
                sources.append(c.Source(url=r.page.url, snapshot_id=ledger.snapshots[r.page.first_snapshot].snapshot_id,
                                        quote=r.text[:200]))
        if len(values) < 2:
            continue
        loser = max(losers, key=lambda r: (r.rid in {x.rid for x in tl.members(latest).get(key, [])}, truth_key(r)))
        reason = explain(winner, loser, current=loser.rid in {x.rid for x in tl.members(latest).get(key, [])})
        changes = [f"{value_text(s.row)} from {long_date(s.valid_from)}" for s in spans if value_text(s.row)]
        if len(dict.fromkeys(changes)) > 1:
            reason += "; over time the winning figure was " + ", then ".join(dict.fromkeys(changes))
        if not current:
            reason += "; the fact is no longer listed in the newest crawl, so this was its last stated value"
        live = any(r.rid in {x.rid for x in tl.members(latest).get(key, [])} for r in losers)
        newest_year = winner.quals.years[1] if winner.quals.years else 0
        out.append(((len(key.kinds), len(_topic(winner))), ("table", key.family, key.terms, key.tier), (not live, -newest_year),
                    c.Conflict(topic=_topic(winner), values=values, sources=sources, resolution=value_text(winner, full=True),
                               reason=reason + ". Tiers and rate-vs-APY columns are kept as separate facts.")))
    return out


def _topic(row) -> str:
    """'Auto & Motorcycle Loans - 2024-27 Model Year (60 Months) [60-month, model years 2024-2027; APR]'."""
    name = " - ".join(dict.fromkeys(b for b in (row.caption, row.label) if b))
    if row.qual_text:
        name += f" ({row.qual_text})"
    metrics = "/".join(sorted({m for m, v in row.values if m in ("rate", "dividend rate", "APY", "APR", "fee") and ("%" in v or "$" in v)}))
    return f"{name} [{'; '.join(x for x in (row.quals.describe(), metrics) if x)}]"


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
    found = []   # (template words, rows, rid -> the figures that differ between its copies)
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
        found.append((set(tmpl), list(rows), {r.rid: " / ".join(_counts(r.text)[i] for i in differing) for r in rows}))
    # One claim printed in several wordings over the years ("Chartered in 1948, membership totals more than
    # 141,000 ..." and "Founded in 1948 by ten ... employees, membership has grown to more than 179,000 ...")
    # is one contradiction, settled by its newest dated copy.
    claims: list = []
    for tmpl, rows, value_of in found:
        same = next((cl for cl in claims if _same_claim(cl[0], tmpl)), None)
        if same is None:
            claims.append((tmpl, rows, value_of))
        else:
            same[0].update(tmpl)
            same[1].extend(rows)
            same[2].update(value_of)
    out = []
    for _tmpl, rows, value_of in claims:
        winner = max(rows, key=truth_key)
        loser = max((r for r in rows if value_of[r.rid] != value_of[winner.rid]), key=truth_key)
        values = list(dict.fromkeys(value_of[r.rid] for r in sorted(rows, key=truth_key, reverse=True)))
        sources, shown = [], set()
        for r in sorted(rows, key=truth_key, reverse=True):
            if value_of[r.rid] not in shown:
                shown.add(value_of[r.rid])
                sources.append(c.Source(url=r.page.url, snapshot_id=ledger.snapshots[r.page.first_snapshot].snapshot_id,
                                        quote=r.text[:200]))
        out.append(((0, len(winner.text)), ("statement",), (False, 0),
                    c.Conflict(topic=winner.text[:120], values=values, sources=sources[:6], resolution=winner.text,
                               reason=explain(winner, loser) + "; the newest dated statement of a repeated claim is the current one")))
    return out


def _same_claim(a: set, b: set) -> bool:
    """Two wordings of one repeated claim: most of the shorter wording's words are in the longer one."""
    shared = len(a & b)
    return shared / len(a | b) >= 0.6 or (min(len(a), len(b)) >= 5 and shared / min(len(a), len(b)) >= 0.75)


def _differing_numbers(keys: list[tuple]) -> list[int]:
    if len({len(k) for k in keys}) > 1:
        return []
    width = min(len(k) for k in keys)
    return [i for i in range(width) if len({k[i] for k in keys}) > 1]


