"""Fact identity: which rate-table rows, on any page and in any crawl, state the same fact.

Every table row gets a fixed identity card while loading, never while answering, so every
question (however it is worded) and the contradiction report see the same products:

* family   - the catalogue family (`catalogue.py`): certificate, savings, mortgage, auto loan, ...
* product  - the product's distinctive name words inside the family ('share', 'bright',
             'special', 'platinum'); none for the family's plain product (regular certificates)
* kinds    - account kinds and variants that make it a different product: IRA, Roth,
             Traditional, business, refinance, intro, jumbo, mini-max
* terms    - the term in months
* tier     - the balance or loan-amount band, or the model-year *slot*

Matching runs exact rules first and looser ones second:

1. The row's own label names the product when it names a family ('Bright Savings'); otherwise
   the table caption does ('Rewards Visa' over a row labelled 'Standard'), then the in-page tab.
2. A table whose caption names no product inherits the name its own page printed for the same
   table in another crawl (same family, kinds and tiers): the May sheet's unnamed checking table
   is the September sheet's '1st Rate Checking'.
3. A shorter name joins the one fuller name of its family that contains it, and an unnamed
   balance-tier table joins the one named product of its family that prints all of its dollar bands
   (a landing page's 'Checking Accounts' table is 1st Rate Checking).

Model-year tiers are aligned by their position in the table, newest first, not by their printed
years: when '2023-26 Model Year' is renamed '2024-27 Model Year' in the same slot, both are the
"newest model years" tier, and a stale calculator copy of the old label is an older copy of that
same fact. The printed years still decide which slot a question's model year falls in (using
the slot's current, winning row).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .catalogue import DEPOSITS, family_of, product_words
from .qualifiers import parse_variants

# Variants that make a row a different product. "variable" and "home equity" are not here: the
# first describes a rate type ('Variable Interest Rate'), the second is a catalogue family.
KINDS = frozenset({"jumbo", "roth", "traditional", "ira", "refinance", "business", "intro", "minimax"})


@dataclass(frozen=True)
class FactKey:
    family: Optional[str]
    product: frozenset
    kinds: frozenset
    terms: frozenset
    tier: tuple = ()

    def plain(self) -> "FactKey":
        """The same fact without the jumbo size variant (its regular-size sibling)."""
        return FactKey(self.family, self.product, self.kinds - {"jumbo"}, self.terms, self.tier)


@dataclass
class _Card:
    row: object
    family: Optional[str]
    product: frozenset
    kinds: frozenset


def _kinds(text: str) -> frozenset:
    return frozenset(v for v in parse_variants(text) if v in KINDS)


def _path(url: str) -> str:
    return (urlparse(url).path or "/").lower()


_RATE_METRICS = ("rate", "dividend rate", "APY", "APR")


def _card(row) -> _Card:
    label, caption, tab = row.label, row.caption, row.tab
    captions = row.subject[: -len(label)].rstrip(" >") if label and row.subject.endswith(label) else row.subject
    upper = " ".join(x for x in (tab, captions) if x)   # the tab and every caption line above the row
    # 'Summer special: 6-month certificate APY 4.50%' names its product after the colon
    lab_fam = family_of(label) or (family_of(row.qual_text) if row.line else None)
    cap_fam = family_of(caption) or family_of(captions) or family_of(tab)   # the nearest caption names it first
    family = lab_fam or cap_fam
    # Context (caption, tab) describes the row only when the row does not name another family itself:
    # a 'Refinance: 30-Year Fixed Conventional' row under a 'Home Equity Loan' caption is a mortgage.
    shared = lab_fam is None or cap_fam is None or lab_fam == cap_fam
    kinds = _kinds(label) | (_kinds(f"{caption} {upper}") if shared else frozenset())
    if family is None and "ira" in kinds:   # 'Special IRAs: Roth 17 Month Fixed Special' is an IRA certificate
        family = "certificate" if row.quals.terms else "savings"
    if family is None and row.page.kind in ("official", "promo", "calculator") and any(m in _RATE_METRICS for m, _ in row.values):
        family = family_of(row.page.title.split("|")[0]) or family_of(_path(row.page.url).replace("-", " ").replace("/", " "))
    if _path(row.page.url).startswith("/business") and (family in DEPOSITS or family == "credit card"):
        kinds |= {"business"}
    product = product_words(label, family) if lab_fam else frozenset()
    if not product and shared:
        for ctx in (caption, captions, tab):   # the nearest context that names the family has the last word
            fam = family_of(ctx)
            if fam == family:
                product = product_words(ctx, family)
                break
            if fam is None:
                product = product_words(ctx, family)
                if product:
                    break
    if not product and not lab_fam and family is None:
        product = product_words(label)   # a row outside the catalogue is named by its own label
    return _Card(row, family, product, kinds)


def _amount_key(amount: Optional[tuple]) -> Optional[tuple]:
    """'$0.01 - $99.99' and '$0 - $99.99' are one band; '$24,999.99' and '$24,999' end at 25,000."""
    if not amount:
        return None
    lo, hi = amount
    lo = 0 if lo < 1 else round(lo)
    if hi != math.inf:
        whole = int(hi)
        hi = whole + 1 if str(whole).endswith("99") else round(hi)
    return (lo, hi)


def assign(rows: list) -> dict[int, FactKey]:
    """rid -> FactKey for every table row (all page versions of all crawls)."""
    cards = [_card(r) for r in rows if r.kind == "table"]
    tables: dict[tuple, list[_Card]] = defaultdict(list)
    for c in cards:
        tables[(c.row.page.vid, c.row.block)].append(c)
    slots = _year_slots(tables)

    def tier(c: _Card) -> tuple:
        if c.row.quals.years:
            return ("year", slots.get((c.family, c.kinds, c.row.quals.years), 0))
        amount = _amount_key(c.row.quals.amount)
        return ("amount",) + amount if amount else ()

    def signature(group: list[_Card]) -> frozenset:
        return frozenset((c.row.quals.terms, tier(c)) for c in group)

    # 2. an unnamed table inherits the name its own page gave the same table in another crawl
    by_url: dict[tuple, list[tuple]] = defaultdict(list)
    for key, group in tables.items():
        g = group[0]
        by_url[(g.row.page.url, g.family, g.kinds)].append((key, group))
    for versions in by_url.values():
        for key, group in versions:
            if any(c.product for c in group):
                continue
            sig = signature(group)
            names = {c.product for k2, g2 in versions if k2 != key and signature(g2) == sig for c in g2 if c.product}
            if len(names) == 1:
                name = next(iter(names))
                for c in group:
                    c.product = name
    # 3. a shorter name joins the one fuller name containing it; an unnamed table joins the one named
    #    product of its family that lists the same tiers
    known: dict[tuple, dict[frozenset, set]] = defaultdict(lambda: defaultdict(set))
    for group in tables.values():
        for c in group:
            if c.product:
                known[(c.family, c.kinds)][c.product].add((c.row.quals.terms, tier(c)))
    for group in tables.values():
        fam_kinds = (group[0].family, group[0].kinds)
        names = known.get(fam_kinds, {})
        for c in group:
            if c.product:
                fuller = [n for n in names if c.product < n and not any(n < m for m in names)]
                if len(fuller) == 1:
                    c.product = fuller[0]
        if group[0].family is not None and not any(c.product for c in group):
            # only a distinctive balance-tier table (every tier a dollar band, all of them the named
            # product's own); terms and model-year slots are shared by plain and named products alike
            sig = signature(group)
            banded = len(sig) >= 2 and all(t[:1] == ("amount",) for _, t in sig)
            fits = [n for n, keys in names.items() if banded and sig <= keys]
            if len(fits) == 1:
                for c in group:
                    c.product = fits[0]
    return {c.row.rid: FactKey(c.family, c.product, c.kinds, c.row.quals.terms, tier(c)) for c in cards}


def _year_slots(tables: dict) -> dict[tuple, int]:
    """(family, kinds, model-year range) -> its slot: the range's position, newest first, in the most
    authoritative and newest table that prints it. Renamed ranges in one table keep their slot."""
    best: dict[tuple, tuple] = {}
    for group in tables.values():
        ranges = sorted({c.row.quals.years for c in group if c.row.quals.years}, key=lambda y: (-y[1], -y[0]))
        if not ranges:
            continue
        g = group[0]
        rank = (g.row.authority, g.row.dated, g.row.page.fetched_at, -g.row.page.vid)
        for i, yr in enumerate(ranges):
            k = (g.family, g.kinds, yr)
            if k not in best or rank > best[k][0]:
                best[k] = (rank, i)
    return {k: v[1] for k, v in best.items()}
