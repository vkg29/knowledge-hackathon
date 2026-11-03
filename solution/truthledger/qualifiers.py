"""Qualifiers: the things that make two similar-looking numbers *different facts*.

A 6-month and a 12-month certificate, the $10,000 and the $25,000 savings tier, a 2025 and a
2019 model-year auto loan, a jumbo and a regular special, a rate and an APY: none of these
contradict each other. Rows and questions are parsed with the same vocabulary so a question can
be matched to exactly the tier it asks about.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .text import NUMBER_WORDS, money_values, norm

INF = math.inf

# Words that turn a product into a different tier or variant. A row carrying one of these is only
# a match when the question asks for it too (otherwise the plain product is meant).
VARIANT_WORDS = {
    "jumbo": "jumbo", "roth": "roth", "traditional": "traditional", "ira": "ira", "iras": "ira",
    "refinance": "refinance", "refinancing": "refinance", "refi": "refinance",
    "business": "business", "commercial": "business",
    "introductory": "intro", "intro": "intro", "balance transfer": "intro",
    "mini-max": "minimax", "mini max": "minimax",
    "home equity": "home equity", "heloc": "home equity",
    "variable": "variable",
}

_NUM = r"(\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")"
_TERM = re.compile(_NUM + r"[\s-]*(month|mo|year|yr)s?\b", re.I)
_YEAR_RANGE = re.compile(r"\b(19|20)(\d\d)\s*[-–]\s*(\d{2,4})\s+model\s+year", re.I)
_YEAR_RANGE_AFTER = re.compile(r"model\s+year\s+((?:19|20)\d\d)\s*[-–]\s*(\d{2,4})", re.I)
_YEAR_OLDER = re.compile(r"\b((?:19|20)\d\d)\s+model\s+year\s*\(and older\)", re.I)
_YEAR_SINGLE = re.compile(r"\b((?:19|20)\d\d)\b")


@dataclass(frozen=True)
class Qualifiers:
    terms: frozenset = frozenset()            # months, e.g. {6} or {360}
    years: Optional[tuple] = None             # model-year range (lo, hi)
    amount: Optional[tuple] = None            # dollar range (lo, hi)
    variants: frozenset = frozenset()         # VARIANT_WORDS values

    def describe(self) -> str:
        bits = []
        if self.terms:
            bits.append("/".join(_term_text(t) for t in sorted(self.terms)))
        if self.years:
            bits.append(f"model years {self.years[0]}-{self.years[1]}")
        return ", ".join(bits)


def _term_text(months: int) -> str:
    return f"{months // 12}-year" if months % 12 == 0 and months >= 120 else f"{months}-month"


def _to_int(tok: str) -> Optional[int]:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok)


def parse_terms(text: str) -> frozenset:
    out = set()
    for m in _TERM.finditer(text):
        n = _to_int(m.group(1))
        if n is None or n == 0:
            continue
        months = n * 12 if m.group(2).lower().startswith("y") else n
        if months <= 600:
            out.add(months)
    return frozenset(out)


def parse_variants(text: str) -> frozenset:
    t = norm(text)
    return frozenset(v for w, v in VARIANT_WORDS.items() if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t))


def parse_amount_range(text: str) -> Optional[tuple]:
    """'$10,000 – $24,999.99' -> (10000, 24999.99); '$25,000+' -> (25000, inf); '$300,000 or less' -> (0, 300000)."""
    t = text.replace("–", "-").replace("—", "-")
    vals = money_values(t)
    if not vals:
        bare = re.findall(r"\b(\d{1,3}(?:,\d{3})+|\d{4,})\b(?!\s*model)", t)
        vals = [float(v.replace(",", "")) for v in bare if not re.fullmatch(r"(19|20)\d\d", v)]
    if not vals:
        return None
    if len(vals) >= 2:
        return (min(vals[:2]), max(vals[:2]))
    v = vals[0]
    if re.search(r"\+|or more|and (up|above|over)|over|above|at least|minimum", t, re.I):
        return (v, INF)
    if re.search(r"or less|and under|up to|below|under|maximum|max\b", t, re.I):
        return (0.0, v)
    return (v, v)


def parse_row_qualifiers(label: str, qual_cells: list[tuple[str, str]], caption: list[str]) -> Qualifiers:
    text = " ".join([label] + [c for _, c in qual_cells])
    years = None
    m = _YEAR_RANGE.search(text)
    if m:
        lo = int(m.group(1) + m.group(2))
        hi_raw = m.group(3)
        hi = int(hi_raw) if len(hi_raw) == 4 else int(m.group(1) + hi_raw)
        years = (lo, hi)
    elif _YEAR_RANGE_AFTER.search(text):
        m = _YEAR_RANGE_AFTER.search(text)
        a = int(m.group(1))
        b = int(m.group(2)) if len(m.group(2)) == 4 else int(m.group(1)[:2] + m.group(2))
        years = (min(a, b), max(a, b))
    else:
        m = _YEAR_OLDER.search(text)
        if m:
            years = (1900, int(m.group(1)))
    amount = None
    for head, cell in qual_cells:
        if re.search(r"balance|tier", head, re.I):
            amount = parse_amount_range(cell)
    if amount is None and "$" in label:
        amount = parse_amount_range(label)
    variants = parse_variants(" ".join(caption + [label]))
    return Qualifiers(parse_terms(text), years, amount, variants)


def parse_question_qualifiers(question: str) -> Qualifiers:
    q = question
    years = None
    for m in _YEAR_SINGLE.finditer(q):
        y = int(m.group(1))
        window = q[max(0, m.start() - 30): m.end() + 30].lower()
        if re.search(r"model|vehicle|car|truck|auto|motorcycle", window):
            years = (y, y)
    amount = None
    if "$" in q or re.search(r"\d{1,3},\d{3}", q):
        amount = parse_amount_range(re.sub(r"\b(19|20)\d\d\b", "", q))
    return Qualifiers(parse_terms(q), years, amount, parse_variants(q))


def terms_match(q: frozenset, row: frozenset) -> Optional[bool]:
    """None when the question names no term; a named term needs a row with that term."""
    if not q:
        return None
    return bool(q & row)


def years_match(q: Optional[tuple], row: Optional[tuple]) -> Optional[bool]:
    if not q:
        return None
    return bool(row) and row[0] <= q[0] <= row[1]


def amount_match(q: Optional[tuple], row: Optional[tuple]) -> Optional[bool]:
    if not q or not row:
        return None
    lo, hi = q
    if lo == hi:
        return row[0] <= lo <= row[1]
    return row[0] <= lo + 0.01 and hi <= row[1] + 1.0
