"""Answer composition: find candidate rows, let the truth rules pick, phrase the winner.

Every answer is built from ledger rows only. The text quotes the winning row(s) and nothing
else, so a stale or other-tier value never appears next to the right one; the same question
always produces byte-identical text because every step is a deterministic sort.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .ledger import Ledger, Row
from .matcher import LexicalIndex, row_document
from .qualifiers import amount_match, parse_terms, terms_match, years_match
from .query import Question, parse
from .rules import explain, is_live, truth_key
from .text import has_digit, norm
from .vocab import STOPWORDS, expand, stem, tokens, with_prefixes

REFUSAL = ("I'm sorry, that isn't listed on Dupaco's website, so I can't confirm it. "
           "Please contact Dupaco directly by phone, in Shine Online Banking or at any branch, and a team member can help.")

# How much a prose statement is trusted, by page kind (official pages first).
_PROSE_WEIGHT = {6: 1.0, 5: 1.0, 4: 0.9, 3: 0.8, 2: 0.85, 1: 0.75, 0: 0.2}
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ANSWER_SHAPES = {
    "address": re.compile(r"\b\d{2,6}\s+(?:[NSEW]\.?\s+)?[A-Z0-9][\w.']*(?:\s+[\w.']+){0,3}\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|"
                          r"Ln|Lane|Hwy|Highway|Blvd|Boulevard|Way|Ct|Court|Pkwy|Parkway|Plaza|Pl|Square|Sq)\b|P\.?\s?O\.? Box", re.I),
    "phone": re.compile(r"\(?\b\d{3}\)?[-.\s]\d{3}[-.]\d{4}\b"),
    "routing": re.compile(r"\b\d{9}\b"),
    "date": re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", re.I),
}
_LABEL_LINE = re.compile(r"^([A-Za-z][A-Za-z ’'/-]{1,30}):\s*\S")
_RATE_VALUE = re.compile(r"\d(?:\.\d+)?\s?%\*?\s*(?:\(?apy|\(?apr|annual percentage|dividend|rate|interest|introductory|intro|fixed|variable)|"
                         r"(?:apy|apr|rate|yield) of \d", re.I)


@dataclass
class Result:
    text: str
    rows: list = field(default_factory=list)
    confident: bool = True
    note: Optional[str] = None
    support: list = field(default_factory=list)   # other pages that state the same winning value


class Answerer:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        docs = [row_document(r) for r in ledger.rows]
        self.doc_terms = [frozenset(d) for d in docs]
        self.index = LexicalIndex(docs)
        self._view_ids: dict[int, frozenset] = {}
        self._page_cache: dict[int, frozenset] = {}

    # ---- entry point ---------------------------------------------------------------------
    def answer(self, question: str, as_of: Optional[str] = None) -> tuple[Result, int]:
        q = parse(question)
        latest = self.ledger.snapshots[-1].taken_on
        if as_of is None and q.as_of_hint and q.as_of_hint < latest:
            as_of = q.as_of_hint
        k = self.ledger.snapshot_for(as_of)
        on_date = min(as_of, latest) if as_of else self.ledger.snapshots[k].taken_on
        if q.wants_url:
            res = self._url(q, k, on_date)
        elif q.hours:
            res = self._hours(q, k, on_date)
        elif q.rate_intent:
            res = self._rate(q, k, on_date) or self._prose(q, k, on_date, need_percent=True)
        elif q.rate_implied:
            res = self._rate(q, k, on_date) or self._prose(q, k, on_date)
        else:
            res = self._prose(q, k, on_date)
        return (res or Result(REFUSAL, confident=False)), k

    # ---- shared helpers ------------------------------------------------------------------
    def _view(self, k: int) -> frozenset:
        if k not in self._view_ids:
            self._view_ids[k] = frozenset(r.rid for r in self.ledger.view(k))
        return self._view_ids[k]

    def _coverage(self, q: Question, row: Row) -> float:
        """Share (idf-weighted) of the question's subject words that the row or its page mention."""
        words = [w for w in dict.fromkeys(q.key_words) if not w.isdigit()]
        if not words:
            return 1.0
        own = self._own_terms(row)
        ctx = self.doc_terms[row.rid] | self._page_terms(row)
        total = hit = 0.0
        for w in words:
            idf = self.index.idf.get(w, 2.0)   # a word the site never uses is likely just phrasing
            total += idf
            alts = [s for s in expand([w]) if s != w]
            if w in own:
                hit += idf
            elif any(s in own for s in alts):
                hit += 0.7 * idf
            elif w in ctx:
                hit += 0.6 * idf
            elif any(s in ctx for s in alts):
                hit += 0.4 * idf
        return hit / total if total else 1.0

    def _own_terms(self, row: Row) -> frozenset:
        """A row's own words; for table rows the caption is part of the row."""
        text = row.text + (" " + row.subject if row.kind == "table" else "")
        return frozenset(with_prefixes(tokens(text)))

    def _page_terms(self, row: Row) -> frozenset:
        key = row.page.vid
        if key not in self._page_cache:
            self._page_cache[key] = frozenset(with_prefixes(tokens(row.page.title) + tokens(row.page.url)))
        return self._page_cache[key]

    def _has_names(self, q: Question, row: Row) -> bool:
        terms = self.doc_terms[row.rid] | self._page_terms(row)
        return all(n in terms for n in q.names)

    def _covered(self, q: Question, row: Row, threshold: float = 0.5) -> bool:
        return self._coverage(q, row) >= threshold

    def _scored(self, q: Question, allowed: frozenset) -> list[tuple[float, Row]]:
        rows = self.ledger.rows
        return [(s, rows[rid]) for rid, s in self.index.scores(q.words, allowed).items()]

    # ---- rates and other table values ----------------------------------------------------
    def _rate(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        view = self._view(k)
        allowed = frozenset(rid for rid in view if self.ledger.rows[rid].kind == "table")
        listing = q.wants_list and not _specific(q)
        cands = []
        for s, row in self._scored(q, allowed):
            if not is_live(row, on_date) or not _compatible(q, row):
                continue
            extra = row.quals.variants - q.quals.variants
            if extra:
                s *= (0.8 if listing else 0.55) ** len(extra)
            missing = q.quals.variants - row.quals.variants
            if missing:
                s *= 0.5 ** len(missing)
            cands.append((s, row))
        if q.names:
            cands = [c for c in cands if self._has_names(q, c[1])]
        # Every row in a rate answer must itself be about the product asked for.
        cands = [c for c in cands if self._covered(q, c[1])]
        if not cands:
            return None
        if q.quals.amount:
            cands = _tightest_tier(cands)
        cands.sort(key=lambda c: (-c[0], c[1].rid))
        top = cands[0][0]
        band = [c for c in cands if c[0] >= (0.6 if listing else 0.75) * top]
        # Rows stating the same fact compete; the truest copy of each fact survives.
        winners: dict[tuple, tuple] = {}
        for s, r in band:
            key = _fact_key(r)
            if key not in winners or (truth_key(r), s) > (truth_key(winners[key][1]), winners[key][0]):
                winners[key] = (s, r)
        best = max(winners.values(), key=lambda c: (truth_key(c[1]), c[0]))[1]
        if not listing:
            rows = [r for _, r in winners.values() if _same_table(r, best)]
        elif _catalogue(q):
            rows = [r for _, r in winners.values()]
        else:   # everything the truest page lists for this product
            rows = [r for _, r in cands if r.page.vid == best.page.vid]
        rows = _dedupe(sorted(rows, key=lambda r: (r.page.vid != best.page.vid, r.page.url, r.pos)))
        if q.superlative:
            rows = [_extreme(rows, q)]
        rows = rows[:12]
        losers = [r for _, r in band if _fact_key(r) in {_fact_key(x) for x in rows} and r not in rows
                  and _value(r, q) != _value(next(x for x in rows if _fact_key(x) == _fact_key(r)), q)]
        note = None
        if losers:
            loser = max(losers, key=truth_key)
            note = "Other pages show a different figure; kept the value from the truest source: " + explain(best, loser) + "."
        keys = {(_fact_key(r), _value(r, q)) for r in rows}
        support = [r for _, r in sorted(cands, key=lambda c: (-c[1].authority, c[1].page.url))
                   if (_fact_key(r), _value(r, q)) in keys and r.page.vid not in {x.page.vid for x in rows}]
        return Result("; ".join(_render(r, q) for r in rows) + ".", rows, note=note, support=support)

    # ---- prose ---------------------------------------------------------------------------
    def _prose(self, q: Question, k: int, on_date: str, need_percent: bool = False) -> Optional[Result]:
        view = self._view(k)
        rows = self.ledger.rows
        allowed = frozenset(rid for rid in view if rows[rid].kind in ("text", "hours"))
        scored = []
        for s, row in self._scored(q, allowed):
            if not is_live(row, on_date):
                continue
            if need_percent and not _RATE_VALUE.search(row.text):
                continue
            if q.wants_number and not has_digit(row.text):
                continue
            if q.answer_type and not _ANSWER_SHAPES[q.answer_type].search(row.text):
                continue
            if q.quals.terms and not (q.quals.terms & parse_terms(row.text)):
                continue
            if _LABEL_LINE.match(row.text) and set(tokens(_LABEL_LINE.match(row.text).group(1))) <= set(q.words):
                s *= 1.5   # "Routing Number: 273974581" states exactly what was asked
            scored.append((s * _PROSE_WEIGHT[row.authority], row))
        if not scored:
            return None
        if q.names:
            # Names (places, brands, programs) must be on the row or its page; if no row mentions
            # them, the site does not cover the question.
            scored = [(s, r) for s, r in scored if self._has_names(q, r)]
            if not scored:
                return None
        scored.sort(key=lambda c: (-c[0], c[1].rid))
        scored = [(s * (0.3 + self._coverage(q, r)) ** 2, r) for s, r in scored[:300]]
        scored.sort(key=lambda c: (-c[0], c[1].rid))
        best_s, best = scored[0]
        # A matched name (place, brand, program) already grounds the topic, so less overlap is needed.
        if not self._covered(q, best, 0.35 if q.names else (0.4 if q.answer_type else 0.5)):
            return None
        # Same statement printed with different numbers (e.g. press-release boilerplate):
        # the truest, newest copy wins.
        tmpl = _template(best.text)
        rivals = [r for s, r in scored[:60] if s >= 0.6 * best_s and _similar(_template(r.text), tmpl)]
        disagree = any(_NUMBER.findall(r.text) != _NUMBER.findall(best.text) for r in rivals)
        winner = max(rivals, key=truth_key) if disagree else best
        if winner is best:
            # Near-ties on one page: the earliest mention is usually the lead/definition.
            cov = self._coverage(q, best)
            lead = [r for s, r in scored[:20] if r.page.vid == best.page.vid and s >= 0.7 * best_s
                    and not _is_header(r.text) and self._coverage(q, r) >= cov]
            winner = min(lead, key=lambda r: r.pos)
        losers = [r for r in rivals if _NUMBER.findall(r.text) != _NUMBER.findall(winner.text)]
        note = None
        if losers:
            note = "Older or less authoritative pages state this differently; kept: " + explain(winner, max(losers, key=truth_key)) + "."
        same = [r for r in rivals if r is not winner and _NUMBER.findall(r.text) == _NUMBER.findall(winner.text)]
        support = sorted(same, key=lambda r: (-r.authority, r.page.url))
        text = self._passage(winner)
        if q.wants_action:
            link = self._action_link(q, winner)
            if link and link not in text:
                text += f" You can get started here: {link}"
        return Result(text, [winner], note=note, support=support)

    def _action_link(self, q: Question, row: Row) -> Optional[str]:
        """The call-to-action link that goes with a passage, preferring one the question names."""
        siblings = self.ledger.rows_by_version[row.page.vid]
        found = [(a, u) for r in siblings[row.pos: row.pos + 4] if r.section == row.section for a, u in r.links if u.startswith("http")]
        if not found:
            return None
        words = set(q.words)
        best = max(found, key=lambda au: (sum(t in words for t in tokens(au[0])), -found.index(au)))
        return _clean_link(best[1])

    def _passage(self, row: Row, limit: int = 420) -> str:
        """The winning sentence plus the sentences that follow it in the same paragraph or list."""
        siblings = self.ledger.rows_by_version[row.page.vid]
        if row.section.endswith("?"):   # an FAQ answer reads best from its first sentence
            first = row.pos
            while first > 0 and siblings[first - 1].section == row.section and siblings[first - 1].kind == "text":
                first -= 1
            if row.pos - first <= 2 and not any(_is_header(r.text) for r in siblings[first:row.pos]):
                row = siblings[first]
        parts = [_sentence(row.text)]
        size = len(parts[0])
        for nxt in siblings[row.pos + 1: row.pos + 6]:
            if nxt.kind != "text" or nxt.section != row.section or nxt.block - row.block > 3 or _is_header(nxt.text):
                break
            if size + len(nxt.text) > limit:
                break
            parts.append(_sentence(nxt.text))
            size += len(nxt.text)
        return " ".join(parts)

    # ---- branch hours --------------------------------------------------------------------
    def _hours(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        rows = [r for r in self.ledger.view(k) if r.kind == "hours" and is_live(r, on_date)]
        if not rows:
            return None
        place = [stem(w) for w in q.names if stem(w) not in _HOURS_WORDS]
        if place:
            matched = [r for r in rows if all(w in self.doc_terms[r.rid] | self._page_terms(r) for w in place)]
            if not matched:
                return None
            best = max(matched, key=truth_key)
            return Result(f"{_place_name(best)}: {best.text}.", [best])
        kind = "Drive-up" if q.hours == "drive-up" else "Lobby"
        schedules: Counter = Counter()
        example: dict[str, Row] = {}
        for r in sorted(rows, key=lambda r: r.rid):
            part = _hours_part(r.text, kind)
            if part:
                schedules[part] += 1
                example.setdefault(part, r)
        if not schedules:
            return None
        ordered = sorted(schedules, key=lambda p: (-schedules[p], p))[:5]
        text = f"{kind} hours vary by branch. Most branches: {ordered[0]}."
        if len(ordered) > 1:
            text += " Other branches: " + " | ".join(ordered[1:]) + "."
        text += " Tell me which branch and I can give its exact hours."
        return Result(text, [example[p] for p in ordered])

    # ---- links ---------------------------------------------------------------------------
    def _url(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        view = self._view(k)
        rows = self.ledger.rows
        words = {t: w for t, w in q.words.items() if t not in _URL_WORDS}
        votes: dict[str, float] = defaultdict(float)
        source: dict[str, Row] = {}
        scored = sorted(((s, rows[rid]) for rid, s in self.index.scores(words, view).items()), key=lambda c: (-c[0], c[1].rid))[:80]
        for s, row in scored:
            if not is_live(row, on_date):
                continue
            for anchor, url in row.links:
                if not url.startswith("http"):
                    continue
                url = _clean_link(url)
                bonus = 1.0 + sum(0.5 for t in tokens(anchor) if t in words)
                vote = s * bonus * _PROSE_WEIGHT[row.authority] * (0.3 + self._coverage(q, row)) ** 2
                votes[url] = max(votes[url], vote)
                source.setdefault(url, row)
        if not votes:
            return None
        url = max(sorted(votes), key=lambda u: votes[u])
        return Result(f"You can find it here: {url}", [source[url]])


# ---- module helpers ----------------------------------------------------------------------
_HOURS_WORDS = {stem(w) for w in "hour lobby drive up thru through open close branch branches office time times what general typical usual regular".split()}
_URL_WORDS = {stem(w) for w in "link url web page website address site".split()}


def _compatible(q: Question, row: Row) -> bool:
    if terms_match(q.quals.terms, row.quals.terms) is False:
        return False
    if years_match(q.quals.years, row.quals.years) is False:
        return False
    if amount_match(q.quals.amount, row.quals.amount) is False:
        return False
    if q.metric and q.metric != "rate" and not any(m == q.metric for m, _ in row.values):
        return False
    return True


def _tightest_tier(cands: list) -> list:
    """With a dollar amount in the question, keep each table's narrowest tier that contains it."""
    best: dict = {}
    for s, r in cands:
        if r.quals.amount:
            key = (r.page.vid, r.caption, r.quals.terms, r.quals.variants)
            width = r.quals.amount[1] - r.quals.amount[0]
            best[key] = min(best.get(key, width), width)
    out = []
    for s, r in cands:
        if r.quals.amount:
            key = (r.page.vid, r.caption, r.quals.terms, r.quals.variants)
            if r.quals.amount[1] - r.quals.amount[0] > best[key]:
                continue
        out.append((s, r))
    return out


def _specific(q: Question) -> bool:
    return bool(q.quals.terms or q.quals.years or q.quals.amount or q.superlative)


def _same_table(a: Row, b: Row) -> bool:
    return a.page.vid == b.page.vid and a.caption == b.caption and a.section == b.section


def _fact_key(r: Row) -> tuple:
    """Rows with the same key state the same fact (same product, tier and qualifiers)."""
    label = norm(re.sub(r"[^a-z0-9$+ ]", " ", r.label.lower()))
    return (label, r.quals.terms, r.quals.years, r.quals.amount, r.quals.variants, tuple(sorted(m for m, _ in r.values)))


def _catalogue(q: Question) -> bool:
    return bool(re.search(r"\b(what|which)\b.*\b(do you|does dupaco|are)\b.*\b(offer|have|available)\b|\ball\b|\btypes? of\b", q.raw, re.I))


def _overlaps(a: Row, b: Row) -> bool:
    """Do two rows describe the same tier of the same kind of product?"""
    return a.quals.terms == b.quals.terms and a.quals.variants == b.quals.variants


def _value(r: Row, q: Question) -> tuple:
    return tuple(v for m, v in r.values if m in _wanted_metrics(q))


def _wanted_metrics(q: Question) -> set:
    if q.metric and q.metric != "rate":
        return {q.metric}
    return {"rate", "APR", "APY", "dividend rate", "fee"}


def _dedupe(rows: list[Row]) -> list[Row]:
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r.rid):
        key = norm(r.text)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _num(v: str) -> float:
    m = _NUMBER.search(v)
    return float(m.group(0).replace(",", "")) if m else 0.0


def _extreme(rows: list[Row], q: Question) -> Row:
    metric = q.metric if q.metric and q.metric != "rate" else None

    def val(r: Row) -> float:
        vs = [v for m, v in r.values if (metric is None or m == metric) and "%" in v]
        return max((_num(v) for v in vs), default=0.0)

    return (max if q.superlative == "max" else min)(rows, key=lambda r: (val(r), -r.rid))


def _render(r: Row, q: Question) -> str:
    wanted = _wanted_metrics(q)
    vals = [(m, v) for m, v in r.values if m in wanted] or [(m, v) for m, v in r.values if m not in ("points", "payment example")]
    label = r.label
    if r.caption and not (set(tokens(r.caption)) - _CAPTION_FILLER) & set(tokens(label)):
        label = f"{r.caption} - {label}"
    if r.qual_text:
        label += f" ({r.qual_text})"
    return f"{label}: " + ", ".join(f"{v} {m}" for m, v in vals)


_CAPTION_FILLER = {stem(w) for w in "rates rate loans loan accounts account savings regular special".split()}


def _template(text: str) -> frozenset:
    return frozenset(t for t in tokens(_NUMBER.sub(" ", text)) if t not in STOPWORDS)


def _similar(a: frozenset, b: frozenset) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.6


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _is_header(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    upper = sum(c.isupper() for c in letters)
    return text.rstrip().endswith(":") or (len(letters) > 3 and upper / len(letters) > 0.6)


def _hours_part(text: str, kind: str) -> Optional[str]:
    for part in text.split("; "):
        name, _, days = part.partition(": ")
        if name.lower().startswith(kind.lower()[:5]):
            return days
    return None


def _place_name(r: Row) -> str:
    return r.page.title.split("|")[0].strip() or r.subject


def _clean_link(url: str) -> str:
    url = url.split("?")[0]
    base, _, frag = url.partition("#")
    if frag and re.fullmatch(r"[a-z-]{2,20}", frag):
        return f"{base}#{frag}"
    return base
