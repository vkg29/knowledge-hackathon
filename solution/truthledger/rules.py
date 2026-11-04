"""The truth rules. Given ledger rows that state the same fact, decide which one is true.

In priority order:

1. A statement that has ended is not current: rows from a page that says the offer has ended,
   or rows that carry their own past end date ("offer ended 2025-09-30"), drop out of current
   answers. They still answer "as of" questions for the time they were live.
2. Authority: an official rate sheet beats an official product page, which beats a promotion
   page, a downloaded document, a press release, a blog post and finally a calculator.
3. Recency among equals: the date printed on the page wins ("Rates are effective as of ...", or
   a press release's byline date), then the crawl that last saw the page.

Qualifiers are handled before these rules run: rows for different terms, tiers, model years,
loan amounts or metrics are different facts and never compete, and so are products whose names
differ by a word that is not generic ("Student" certificates); see `same_fact`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from .text import find_dates
from .vocab import STOPWORDS, stem, tokens

_ENDED_PAGE = re.compile(r"this offer has ended|offer has expired|promotion has ended|this offer is no longer", re.I)
_ENDS_ON = re.compile(r"\b(?:offer|promotion|special|rate|sale)\b[^.]{0,40}?\b(?:ended|expired|ends|expires|valid (?:through|until))\b", re.I)


def page_has_ended(markdown: str) -> bool:
    return bool(_ENDED_PAGE.search(markdown[:3000]))


def ends_on(text: str) -> Optional[str]:
    """End date stated in the row itself ('offer ended 2025-09-30'), if any."""
    m = _ENDS_ON.search(text)
    if not m:
        return None
    dates = find_dates(text[m.start():])
    return dates[0] if dates else None


def is_live(row, on_date: str) -> bool:
    """Was the statement still in force on `on_date`?"""
    if row.page.ended:
        return False
    end = row.ends
    return end is None or end >= on_date


def truth_key(row) -> tuple:
    """Sort key; the highest key is the row believed true."""
    return (row.authority, row.dated, row.page.fetched_at, -row.rid)


_TIER_WORDS = frozenset(stem(w) for w in "model year month or less and older type program".split())
_GENERIC_PRODUCT_WORDS = _TIER_WORDS | frozenset(stem(w) for w in (
    "rate rates loan loans account accounts certificate certificates regular term share fixed standard conventional").split())


def core_words(row) -> Optional[frozenset]:
    """The words that tell one table row's product from another's (its caption and label, minus tier
    and generic product words): 'Regular Certificates - 12 Month' has none, a student certificate
    has 'student'. None when the caption and label name no product at all ('$100,000+')."""
    words = [t for t in tokens(f"{row.caption} {row.label}") if t.isalpha() and t not in STOPWORDS and t not in _TIER_WORDS]
    if not words:
        return None
    return frozenset(t for t in words if t not in _GENERIC_PRODUCT_WORDS)


def product_names(rows) -> dict[int, Optional[frozenset]]:
    """rid -> the product a row names. A shorter name joins the one fuller name of the same term and
    variant that contains it ('Auto Loans' is 'Auto & Motorcycle Loans'); a plain name (no core
    words) or one that fits several fuller names stays its own product."""
    names: dict[tuple, set] = defaultdict(set)
    for r in rows:
        if core_words(r) is not None:
            names[(r.quals.terms, r.quals.variants)].add(core_words(r))
    out = {}
    for r in rows:
        own = core_words(r)
        if own:
            known = names[(r.quals.terms, r.quals.variants)]
            fuller = [n for n in known if own < n and not any(n < m for m in known)]
            if len(fuller) == 1:
                own = fuller[0]
        out[r.rid] = own
    return out


def same_fact(a, b, names: dict) -> bool:
    """Two table rows state the same fact: same term, variant, amount tier and product name,
    overlapping model years, and at least one metric both state."""
    if names[a.rid] is None or names[a.rid] != names[b.rid]:
        return False
    if (a.quals.terms, a.quals.variants, a.quals.amount) != (b.quals.terms, b.quals.variants, b.quals.amount):
        return False
    ya, yb = a.quals.years, b.quals.years
    if (ya is None) != (yb is None) or (ya and not (ya[0] <= yb[1] and yb[0] <= ya[1])):
        return False
    return bool({m for m, _ in a.values} & {m for m, _ in b.values})


def fact_groups(rows) -> list[list]:
    """Rows grouped so that every row is in the group of each row it states the same fact as."""
    names = product_names(rows)
    buckets: dict[tuple, list] = defaultdict(list)
    for r in rows:
        buckets[(r.quals.terms, r.quals.variants, r.quals.amount, names[r.rid])].append(r)
    groups = []
    for bucket in buckets.values():
        parent = list(range(len(bucket)))

        def find(i: int) -> int:
            while parent[i] != i:
                i = parent[i]
            return i

        for i, a in enumerate(bucket):
            for j in range(i):
                if same_fact(a, bucket[j], names):
                    parent[find(i)] = find(j)
        members: dict[int, list] = defaultdict(list)
        for i, r in enumerate(bucket):
            members[find(i)].append(r)
        groups.extend(members.values())
    return groups


def explain(winner, loser) -> str:
    """Why `winner` beat `loser`, in one sentence."""
    if winner.authority != loser.authority:
        return (f"{_kind_name(winner)} ({winner.page.url}) outranks {_kind_name(loser)} ({loser.page.url})")
    if winner.dated != loser.dated:
        return f"newer statement: dated {winner.dated} on {winner.page.url} vs {loser.dated} on {loser.page.url}"
    return f"seen in a newer crawl of {winner.page.url}"


def _kind_name(row) -> str:
    return {6: "official rate sheet", 5: "official product page", 4: "promotion page", 3: "downloaded document",
            2: "press release", 1: "blog post", 0: "calculator page"}[row.authority]
