"""Answer composition: find the facts a question asks about, take their truth winners, phrase them.

Rate questions are matched onto fact identities (`identity.py`); the value of each identity is
its winner over every live copy (`timeline.py`), never just the copies the question's words
pulled up. Change questions read the identities' histories. Prose, hours and link questions use
the ledger's passages. Every answer is built from ledger rows only: the text quotes the winning
row(s) and nothing else, so a stale or other-tier value never appears next to the right one, and
the same question always produces byte-identical text because every step is a deterministic sort.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Optional
from urllib.parse import urlparse

from .catalogue import family_matches
from .identity import FactKey
from .ledger import Ledger, Row
from .matcher import QUESTION_HEADING, LexicalIndex, row_document
from .pages import AUTHORITY, canonical_url, page_kind
from .qualifiers import amount_match, parse_terms, terms_match, years_match
from .query import Question, parse
from .rules import explain, is_live, truth_key
from .text import COUNT, counts, long_date, norm, states_count
from .timeline import figures, value_text
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
    first: list = field(default_factory=list)     # more official pages stating that value, cited ahead of `rows`


class Answerer:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        docs = [row_document(r) for r in ledger.rows]
        self.doc_terms = [frozenset(d) for d in docs]
        self.index = LexicalIndex(docs)
        self._view_ids: dict[int, frozenset] = {}
        self._page_cache: dict[int, frozenset] = {}
        self.product_vocab = frozenset(w for key in ledger.identities().values() for w in key.product)
        self._official: dict[int, dict] = {}

    # ---- entry point ---------------------------------------------------------------------
    def answer(self, question: str, as_of: Optional[str] = None, calendar_year: Optional[int] = None,
               siblings: bool = True) -> tuple[Result, int]:
        """`calendar_year` and `siblings=False` come from AI question understanding: a year the question
        asks about as a point in time, and one side of a head-to-head comparison (only the product named)."""
        latest = self.ledger.snapshots[-1].taken_on
        q = parse(question, today=as_of or latest, calendar_year=calendar_year)
        if as_of is None and q.as_of_hint and q.as_of_hint < latest:
            as_of = q.as_of_hint
        k = self.ledger.snapshot_for(as_of)
        on_date = min(as_of, latest) if as_of else self.ledger.snapshots[k].taken_on
        tabled = q.rate_intent or q.rate_implied
        if as_of is None and q.year and tabled and not q.change and q.year < int(latest[:4]):
            first = self.ledger.snapshots[0].taken_on
            if q.year < int(first[:4]):   # before the first crawl: the site as it stood then is not known
                return Result(f"I can't confirm what Dupaco listed in {q.year}: every copy of Dupaco's website I have is newer "
                              f"than that. Please contact Dupaco for older rates.", confident=False), 0
            as_of = f"{q.year}-12-31"
            k = self.ledger.snapshot_for(as_of)
            on_date = min(as_of, latest)
        if q.change and not (q.wants_url or q.hours) and (tabled or q.family or q.product & self.product_vocab):
            res = self._change(q, k)
            if res is not None:
                return res, k
        if _ELIGIBILITY_Q.search(q.raw) and not (q.wants_url or q.hours or q.rate_intent):
            res = self._eligibility(q, k, on_date)
            if res is not None:
                return res, k
        if _SERVICE_Q.search(q.raw) and not (q.wants_url or q.hours or q.rate_intent or q.change):
            res = self._service(q, k, on_date)
            if res is not None:
                return res, k
        if q.wants_url:
            res = self._url(q, k, on_date)
        elif q.hours:
            res = self._hours(q, k, on_date)
        elif q.rate_intent:
            res = self._rate(q, k, on_date, siblings) or self._prose(q, k, on_date, need_percent=True)
        elif q.rate_implied:   # "what does it pay?" still needs a stated rate, not any passage about the product
            res = self._rate(q, k, on_date, siblings) or self._prose(q, k, on_date, need_percent=q.wants_rate)
        elif q.wants_action:
            res = self._task(q, k, on_date) or self._prose(q, k, on_date)
        else:
            res = self._prose(q, k, on_date)
        return (res or Result(REFUSAL, confident=False)), k

    def reading(self, question: str, calendar_year: Optional[int] = None) -> Question:
        """How the rules read a question (used to compare their reading with the AI's restatement)."""
        return parse(question, today=self.ledger.snapshots[-1].taken_on, calendar_year=calendar_year)

    # ---- shared helpers ------------------------------------------------------------------
    def _view(self, k: int) -> frozenset:
        if k not in self._view_ids:
            self._view_ids[k] = frozenset(r.rid for r in self.ledger.view(k))
        return self._view_ids[k]

    def _coverage(self, q: Question, row: Row, extra: frozenset = frozenset()) -> float:
        """Share (idf-weighted) of the question's subject words that the row or its page mention; `extra`
        are words the row's fact identity is known by (its product name), as if the row printed them."""
        words = [w for w in dict.fromkeys(q.key_words) if not w.isdigit()]
        if not words:
            return 1.0
        own = self._own_terms(row) | extra
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

    def _covered(self, q: Question, row: Row, threshold: float = 0.5, extra: frozenset = frozenset()) -> bool:
        return self._coverage(q, row, extra) >= threshold

    def _scored(self, q: Question, allowed: frozenset) -> list[tuple[float, Row]]:
        rows = self.ledger.rows
        return [(s, rows[rid]) for rid, s in self.index.scores(q.words, allowed).items()]

    # ---- rates and other table values ----------------------------------------------------
    def _rate(self, q: Question, k: int, on_date: str, siblings: bool = True) -> Optional[Result]:
        sel = self._select(q, k, on_date, siblings)
        if sel is None:
            return None
        rows, members, winners = sel
        ids = self.ledger.identities()
        shown_keys = {ids[r.rid] for r in rows}
        copies = [r for key in shown_keys for r in members[key] if r.rid != winners[key].rid]
        losers = [r for r in copies if _differs(r, winners[ids[r.rid]], q)]
        note = None
        if losers:
            loser = max(losers, key=truth_key)
            note = ("Other pages show a different figure; kept the value from the truest source: "
                    + explain(winners[ids[loser.rid]], loser) + ".")
        shown_pages = {r.page.vid for r in rows}
        support = [r for r in sorted(copies, key=lambda r: (-r.authority, r.page.url, r.rid))
                   if not _differs(r, winners[ids[r.rid]], q) and r.page.vid not in shown_pages]
        return Result("; ".join(_render(r, q) for r in rows) + ".", rows, note=note, support=support)

    def _select(self, q: Question, k: int, on_date: str, siblings: bool = True) -> Optional[tuple]:
        """The winning rows that answer a rate question at snapshot k, with that snapshot's members and
        winners. Rows only *find* fact identities; each identity's value comes from its truth winner
        over every live row of that identity, so how a question is worded never changes which copy wins."""
        tl = self.ledger.timeline()
        ids = self.ledger.identities()
        members, winners = tl.members(k, on_date), tl.winners(k, on_date)
        allowed = frozenset(rid for rid in self._view(k) if rid in ids and ids[rid] in members)
        listing = q.wants_list and not _specific(q)
        rel: dict = {}
        for s, row in self._scored(q, allowed):
            if not is_live(row, on_date) or not _compatible(q, row):
                continue
            key = ids[row.rid]
            if not family_matches(q.family, key.family):
                continue
            if q.names and not self._has_names(q, row):
                continue
            if not self._covered(q, row, extra=key.product):   # every fact in a rate answer must be about what was asked
                continue
            rel[key] = max(rel.get(key, 0.0), s)
        facts = self._identities(q, rel, winners, listing)
        if not facts:
            return None
        top = facts[0][0]
        band = [f for f in facts if f[0] >= (0.6 if listing else 0.75) * top]
        if listing:
            # the product family asked about, as the most authoritative page presents it
            plain = [f for f in facts if f[1].kinds <= q.kinds] or band
            best = max(plain, key=lambda f: (AUTHORITY[f[2].page.kind], f[0], truth_key(f[2])))
            family = best[1].family
            extra_ok = {"business"} if _catalogue(q) else set()
            # the best fact's family, or every family of a group the question named ("home equity")
            families = {family} | ({f[1].family for f in facts if f[1].family and family_matches(q.family, f[1].family)}
                                   if q.family and q.family != family else set())
            chosen = [f for f in facts if f[1].family in families and (f[1].kinds - q.kinds) <= extra_ok
                      and (f[1].family is not None or f in band)]
        else:
            # A question that names no product ('the money market rate') means the one the most official
            # page lists, among the close candidates, not a promotion's product that repeats its words.
            if not q.product & self.product_vocab:
                wide = [f for f in facts if f[0] >= 0.5 * top]
                level = max(f[2].authority for f in wide)
                lead = max(f[0] for f in wide if f[2].authority == level)
                band = [f for f in wide if f[2].authority == level and f[0] >= 0.75 * lead]
            best = max(band, key=lambda f: (truth_key(f[2]), f[0]))
            # Roth and Traditional are parallel versions of one IRA product: an "IRA" question gets both
            loose = _WRAPPERS - q.kinds
            group = (best[1].family, best[1].product, best[1].kinds - loose)
            chosen = [f for f in band if (f[1].family, f[1].product, f[1].kinds - loose) == group]
            if siblings:
                chosen += self._siblings(q, chosen, winners, k)
        keys = list(dict.fromkeys(f[1] for f in chosen))
        rows = sorted({winners[key].rid: winners[key] for key in keys}.values(),
                      key=lambda r: (r.page.vid != best[2].page.vid, r.page.url, r.pos))
        rows = _dedupe(rows)
        if q.superlative:
            rows = [_extreme(rows, q)]
        return rows[:12], members, winners

    # ---- what changed: a fact's history ---------------------------------------------------
    def _change(self, q: Question, end_k: int) -> Optional[Result]:
        """Answer from the facts' histories: the asked facts are found as of the end of the period (or,
        for a fact no longer listed, the last crawl that listed it) and followed through every crawl.
        A superseded figure is always stated as a past one, with the date it changed."""
        snaps = self.ledger.snapshots
        if q.until:
            end_k = self.ledger.snapshot_for(q.until)
        ids = self.ledger.identities()
        sel = None
        for ref_k in range(end_k, -1, -1):   # the newest crawl that lists the fact; a plain match beats a variant
            found = self._select(q, ref_k, snaps[ref_k].taken_on, siblings=False)
            if found is None:
                continue
            plain = any(not ids[r.rid].kinds - q.kinds for r in found[0])
            if sel is None or plain:
                sel = found
            if plain:
                break
        if sel is None:
            return None
        anchors = [ids[r.rid] for r in sel[0]]
        start_k = self.ledger.snapshot_for(q.since) if q.since else 0
        histories = [(key, self._history(q, key, end_k)) for key in anchors]
        metric = q.metric if q.metric in ("APY", "APR", "dividend rate", "minimum to open") else None
        lines, rows = [], []
        for key, runs in histories:
            text, used = _describe_history(q, runs, start_k, end_k, metric, snaps)
            if text:
                lines.append(text)
                rows.extend(used)
        if not lines:
            return None
        if q.wants_list and len(lines) > 1:
            changed = [ln for ln, (_, runs) in zip(lines, histories) if len(_window(runs, start_k)) > 1]
            lines = changed or lines
        confident = not all(t.startswith(_GONE) for t in lines)
        return Result(" ".join(lines[:8]), list({r.rid: r for r in rows}.values()), confident=confident)

    def _history(self, q: Question, key: FactKey, end_k: int) -> list:
        """[(first snapshot, last snapshot, winning row or None)] runs of one fact up to snapshot end_k. With
        a model year in the question, each crawl's slot is the one whose printed years contain it."""
        tl = self.ledger.timeline()
        runs: list = []
        for k in range(end_k + 1):
            winners = tl.winners(k)
            row = winners.get(key)
            if q.quals.years and key.tier[:1] == ("year",):
                row = next((w for kk, w in sorted(winners.items(), key=lambda kv: kv[1].rid)
                            if (kk.family, kk.product, kk.kinds, kk.terms) == (key.family, key.product, key.kinds, key.terms)
                            and years_match(q.quals.years, w.quals.years)), None)
            if runs and _same_figures(runs[-1][2], row):
                runs[-1] = (runs[-1][0], k, row if row is not None else runs[-1][2])
            else:
                runs.append((k, k, row))
        return runs

    def _identities(self, q: Question, rel: dict, winners: dict, listing: bool) -> list:
        """(relevance, FactKey, winning row) for the identities a question asks about, best first."""
        need = q.kinds   # an IRA, business, jumbo or refinance question is answered only by such facts
        facts = []
        for key, s in rel.items():
            w = winners[key]
            if years_match(q.quals.years, w.quals.years) is False or amount_match(q.quals.amount, w.quals.amount) is False:
                continue   # a model year or balance falls in the tier the slot's *current* row prints
            if not need <= key.kinds:
                continue
            extra = key.kinds - q.kinds   # kinds the question did not ask for ('jumbo' for a plain special)
            if extra:
                s *= (0.8 if listing else 0.55) ** len(extra)
            facts.append((s, key, w))
        named = q.product & self.product_vocab
        if named and any(named <= f[1].product for f in facts):
            facts = [f for f in facts if named <= f[1].product]
        if q.quals.amount:
            facts = _tightest_tier(facts)
        facts = _prefer_plainer(q, facts, listing)
        return sorted(facts, key=lambda f: (-f[0], f[2].rid))

    def _siblings(self, q: Question, chosen: list, winners: dict, k: int) -> list:
        """The jumbo version of a named special when the question did not pick a size: it is part of the
        complete current answer, unless its figure equals one the asked product itself showed at another
        time (then the answer could not be read unambiguously, so it is left out)."""
        if "jumbo" in q.kinds or k != len(self.ledger.snapshots) - 1:
            return []   # an "as of" answer states the asked size only: old sibling figures read like the asked one's
        tl = self.ledger.timeline()
        have = {f[1] for f in chosen}
        out = []
        for s, key, w in chosen:
            if "jumbo" in key.kinds or not key.product:
                continue
            twin = FactKey(key.family, key.product, key.kinds | {"jumbo"}, key.terms, key.tier)
            tw = winners.get(twin)
            if tw is None or twin in have or not _compatible(q, tw):
                continue
            own = {n for span in tl.spans(key) for _, ns in span.figures for n in ns}
            mine = {n for _, ns in figures(tw) for n in ns}
            if mine & own:
                continue
            out.append((s, twin, tw))
            have.add(twin)
        return out


    # ---- prose ---------------------------------------------------------------------------
    def _prose(self, q: Question, k: int, on_date: str, need_percent: bool = False) -> Optional[Result]:
        view = self._view(k)
        rows = self.ledger.rows
        allowed = frozenset(rid for rid in view if rows[rid].kind in ("text", "hours"))
        scored = []
        for s, row in self._scored(q, allowed):
            if not is_live(row, on_date) or not _names_account_kind(q, row) or _is_header(row.text):
                continue   # a heading ('ONE PAGE PLAN', 'Fees:') names a topic; it states nothing
            if need_percent and not _RATE_VALUE.search(row.text):
                continue
            if q.wants_number and not states_count(row.text):
                continue
            if q.answer_type and not _ANSWER_SHAPES[q.answer_type].search(row.text):
                continue
            if q.quals.terms and not (q.quals.terms & parse_terms(row.text)):
                continue
            if _LABEL_LINE.match(row.text) and set(tokens(_LABEL_LINE.match(row.text).group(1))) <= set(q.words):
                s *= 1.5   # "Routing Number: 273974581" states exactly what was asked
            if q.wants_action and any(set(tokens(a)) & set(q.words) for a, u in row.links if u.startswith("http")):
                s *= 1.3   # a passage whose button does what the member asked ("Join Dupaco")
            if q.names and self._titled(q, row):
                s *= 1.5   # a page titled with the name asked about ('Forbes ranks Dupaco ...') is about it
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
        # The same statement printed by several pages or crawls (press-release boilerplate, a running count
        # such as "the fifth time Forbes recognized Dupaco"): one fact, stated by its truest, newest dated copy.
        tmpl = _template(best.text)
        rivals = [r for i, (s, r) in enumerate(scored) if _template(r.text) == tmpl
                  or (i < 60 and s >= 0.6 * best_s and _similar(_template(r.text), tmpl))]
        yearly = _by_year(rivals)
        if yearly:   # the same statement about different years: separate facts, the year is a qualifier
            if q.year in yearly:
                rivals = yearly[q.year]
            elif q.year:
                return self._yearly(q, yearly)
            else:
                rivals = yearly[max(yearly)]
        if all(counts(r.text) == counts(best.text) for r in rivals):
            # Every copy gives the same figures: the best match stands unless a copy is more official or has a
            # newer byline (its page's other sentences are then the newest too: the May release, not April's).
            newer = [r for r in rivals if _byline_key(r) > _byline_key(best)]
            winner = max(newer, key=truth_key) if newer else best
        else:
            winner = max(rivals, key=truth_key)
        if not yearly and all(counts(r.text) == counts(winner.text) for r in rivals):
            # Near-ties on the winning copy's page are read from the earliest, usually the lead or definition.
            score = {r.rid: s for s, r in scored[:20]}
            top = score.get(winner.rid, best_s)
            cov = self._coverage(q, winner)
            lead = [r for s, r in scored[:20] if r.page.vid == winner.page.vid and s >= 0.7 * top
                    and not _is_header(r.text) and self._coverage(q, r) >= cov]
            winner = min(lead, key=lambda r: r.pos, default=winner)
        losers = [r for r in rivals if counts(r.text) != counts(winner.text)]
        note = None
        if losers:
            note = "Older or less authoritative pages state this differently; kept: " + explain(winner, max(losers, key=truth_key)) + "."
        same = [r for r in rivals if r is not winner and counts(r.text) == counts(winner.text)]
        support = sorted(same, key=lambda r: (-r.authority, r.page.url))
        text = self._join_places(winner, self._passage(winner))
        lede = self._lede(q, winner)
        if lede is not None:   # an article about the name asked: its lead paragraph says what the news is
            head = self._passage(lede)
            text = head if _sentence(winner.text) in head else f"{head} {text}"
            winner = lede
        if q.wants_action:
            link = self._action_link(q, winner)
            if link and link not in text:
                text += f" You can get started here: {link}"
        if q.answer_type in ("phone", "routing", "address"):
            m = _ANSWER_SHAPES[q.answer_type].search(text)
            if m:
                support = support + self._stating(m.group(0), k, on_date)
        return _cited(Result(text, [winner], note=note, support=support))

    def _stating(self, value: str, k: int, on_date: str) -> list[Row]:
        """Other live passages that state the same value (a routing number, a phone number, an address)."""
        digits = max(re.findall(r"\d+", value), key=len, default="")
        if len(digits) < 3:
            return []
        view = self._view(k)
        rows = self.ledger.rows
        want = re.sub(r"\W", "", value).lower()
        return [rows[rid] for rid in sorted(self.index.postings.get(digits, {}))
                if rid in view and rows[rid].kind == "text" and is_live(rows[rid], on_date)
                and want in re.sub(r"\W", "", rows[rid].text).lower()]

    def _titled(self, q: Question, row: Row) -> bool:
        """Does the row's page title name everything the question names?"""
        title = set(tokens(row.page.title.split("|")[0]))
        return all(n in title for n in q.names)

    def _lede(self, q: Question, row: Row) -> Optional[Row]:
        """The lead paragraph of a dated article whose title names what the question names ('What recognition
        has Dupaco received from Forbes?' -> 'Forbes ranks Dupaco as a top credit union in America'): a news
        story's first paragraph states the news, where a later sentence only details it. Not for a question
        that wants a figure or a shape (an address, a phone number), which the matching sentence states."""
        if (row.authority not in (1, 2) or not q.names or q.answer_type or q.wants_number or not _ASKS_WHAT.match(q.raw)
                or not self._titled(q, row)):
            return None
        for r in self.ledger.rows_by_version[row.page.vid][: row.pos]:
            if r.kind == "text" and not _is_header(r.text) and len(r.text.split()) >= 12:
                return r
        return None

    def _yearly(self, q: Question, yearly: dict) -> Result:
        """The site has no statement for the asked year: give each year it does state, newest first."""
        rows = [max(yearly[y], key=truth_key) for y in sorted(yearly, reverse=True)[:3]]
        lead = f"The site has no figure for {q.year}. " if q.year else ""
        return Result(lead + " ".join(_sentence(r.text) for r in rows), rows)

    def _eligibility(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        """'Who can join?' / 'Who is eligible to become a member?' is answered by the section of the site
        that says who may join (a heading like 'Who Can Join' or 'You're Eligible to Join If'), the most
        official copy first, read from its first line and joined with where the area it names is."""
        sections: dict = defaultdict(list)
        for r in self.ledger.view(k):
            if r.kind == "text" and is_live(r, on_date) and _ELIGIBILITY_HEAD.search(r.section.rsplit(" > ", 1)[-1]):
                sections[(r.page.vid, r.section)].append(r)
        if not sections:
            return None
        rows = max(sections.values(), key=lambda rs: (rs[0].authority, len(rs), -len(rs[0].page.url), -rs[0].page.vid))
        first = min(rows, key=lambda r: r.pos)
        return Result(self._join_places(first, self._passage(first)), [first])

    def _service(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        """A request about the member's own account (update my address or phone, a lost card, trouble signing
        in, a question about my account) is answered by Dupaco's own instructions: the official page whose
        title names the task ('Shine Login Help' for a sign-in problem), read from its best-matching passage,
        else the site's contact page, read from its lead (how members reach Dupaco about an account). The
        phone number that page states is added when the passage does not give it."""
        pages = self._official_pages(k)
        best = self._task_page(q, k, on_date)
        if best is None:
            contact = [vid for vid, rs in pages.items() if "contact" in set(tokens(rs[0].page.title.split("|")[0]))
                       and any(_ANSWER_SHAPES["phone"].search(r.text) for r in rs)]
            if not contact:
                return None
            vid = min(contact, key=lambda v: (len(pages[v][0].page.url), v))
            best = min((r for r in pages[vid] if len(r.text.split()) >= 4 and is_live(r, on_date)), key=lambda r: r.pos,
                       default=None)
            if best is None:
                return None
        text = self._passage(best)
        if not _ANSWER_SHAPES["phone"].search(text):
            phone = min((r for r in pages[best.page.vid] if _ANSWER_SHAPES["phone"].search(r.text)), key=lambda r: r.pos, default=None)
            if phone is not None:
                text += " " + _sentence(phone.text)
        return Result(text, [best])

    def _official_pages(self, k: int) -> dict:
        """page version -> its passages, for every official page in the site as it stood at snapshot k."""
        if k not in self._official:
            pages: dict = defaultdict(list)
            for r in self.ledger.view(k):
                if r.kind == "text" and r.authority >= AUTHORITY["official"] and not _is_header(r.text):
                    pages[r.page.vid].append(r)
            self._official[k] = dict(pages)
        return self._official[k]

    def _task_page(self, q: Question, k: int, on_date: str) -> Optional[Row]:
        """The best passage of the official page whose title or main heading names the task asked about, in the
        member's own words, verbs included ('I can't log in to Shine' -> 'Shine Login Help'; 'become a member
        online' -> the Join page headed 'Ready to Become Dupaco's Newest Member?'). One word alone does not
        make a page about the task unless it is the whole task."""
        words = [w for w, weight in q.words.items() if weight >= 1.0 and not w.endswith("*") and not w.isdigit()]
        best_share, best_vid = 0.0, None
        for vid, rs in sorted(self._official_pages(k).items()):
            named = self._page_terms(rs[0]) | frozenset(with_prefixes(tokens(rs[0].section.split(" > ")[0])))
            idf = [(self.index.idf.get(w, 2.0), w in named or any(a in named for a in expand([w]) if a != w)) for w in words]
            hits = sum(hit for _, hit in idf)
            total = sum(i for i, _ in idf)
            if not total or (hits < 2 and hits < len(words)):
                continue
            share = sum(i for i, hit in idf if hit) / total
            if share > best_share:
                best_share, best_vid = share, vid
        if best_vid is None or best_share < 0.5:
            return None
        rows = self.ledger.rows
        allowed = frozenset(r.rid for r in self._official_pages(k)[best_vid] if is_live(r, on_date))
        hits = sorted(((s, rows[rid]) for rid, s in self.index.scores(q.words, allowed).items()), key=lambda c: (-c[0], c[1].pos))
        return next((r for _, r in hits if self._covered(q, r, 0.7)), None)

    def _task(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        """'How do I ...?' answered from the official page about that task, with the link that does it."""
        row = self._task_page(q, k, on_date)
        if row is None:
            return None
        text = self._passage(row)
        link = self._action_link(q, row)
        if link and link not in text:
            text += f" You can get started here: {link}"
        return Result(text, [row])

    def _join_places(self, row: Row, text: str) -> str:
        """A passage that defines an area only by name ('live or work within our charter area') is joined
        with the sentence on the same page that says where that area is ('You can find us in Iowa,
        Illinois and Wisconsin'), so the answer names the places."""
        if not _AREA_TERM.search(text) or len(_STATES.findall(text)) >= 2:
            return text
        for r in self.ledger.rows_by_version[row.page.vid]:
            if r.kind == "text" and len(set(_STATES.findall(r.text))) >= 2 and len(r.text) <= 240:
                return f"{text} {_sentence(r.text)}"
        return text

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
        heading = row.section.rsplit(" > ", 1)[-1]
        if heading.endswith("?") or QUESTION_HEADING.search(heading):   # an FAQ answer reads best from its first sentence
            first = row.pos
            while first > 0 and siblings[first - 1].section == row.section and siblings[first - 1].kind == "text":
                first -= 1
            if row.pos - first <= 4 and not any(_is_header(r.text) for r in siblings[first:row.pos]):
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
        # Official pages that print the most common weekday schedule ('Monday-Friday, 8:30 a.m.-5:30 p.m.')
        # state the same hours as these branch announcements and are cited first.
        week = _schedules(ordered[0])[:1]
        ties = [r for r in self.ledger.view(k) if week and r.kind == "text" and r.authority >= AUTHORITY["official"]
                and is_live(r, on_date) and week[0] in _schedules(r.text)] if week else []
        return _cited(Result(text, [example[p] for p in ordered], support=ties))

    # ---- links ---------------------------------------------------------------------------
    def _url(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        if q.rate_intent or q.rate_implied:
            res = self._rates_link(q, k, on_date)
            if res is not None:
                return res
        present = self.ledger.snapshots[k].pages   # canonical url -> page version this crawl saw
        hosts = {urlparse(u).netloc for u in present}   # the site's own hosts
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
                # a descriptive label that says what the link does ('Join Dupaco'); a printed address is no label
                label = "" if re.search(r"www\.|https?:|\.com\b|/", anchor) else anchor
                bonus = 1.0 + sum(0.5 for t in tokens(label) if t in words)
                vote = s * bonus * _PROSE_WEIGHT[row.authority] * (0.3 + self._coverage(q, row)) ** 2
                if urlparse(url).netloc in hosts and canonical_url(url) not in present:
                    vote *= 0.5   # an old link inside the site to a page this crawl no longer has
                if page_kind(url) in ("news", "blog") and not words.keys() & _ARTICLE_WORDS:
                    vote *= 0.5   # a press release or blog post is read, not where a member does something
                votes[url] = max(votes[url], vote)
                source.setdefault(url, row)
        if not votes:
            return None
        url = max(sorted(votes), key=lambda u: votes[u])
        return _cited(Result(f"You can find it here: {url}", [source[url]], support=self._page_row(url, k)))

    def _page_row(self, url: str, k: int) -> list[Row]:
        """The first passage of the page a link points to, when this crawl has that page (to cite it)."""
        vid = self.ledger.snapshots[k].pages.get(canonical_url(url))
        found = [r for r in self.ledger.rows_by_version.get(vid, []) if r.kind == "text"] if vid is not None else []
        return found[:1]

    def _rates_link(self, q: Question, k: int, on_date: str) -> Optional[Result]:
        """'What link shows the certificate rates?': the official product page whose rate table lists the facts
        asked about, at the in-page rates section the site's own links point to ('.../savings-account#rates')."""
        about = replace(q, words={t: w for t, w in q.words.items() if t not in _URL_WORDS},
                        key_words=[t for t in q.key_words if t not in _URL_WORDS])   # what the page is about, not "link"
        sel = self._select(about, k, on_date, siblings=False)
        if sel is None:
            return None
        found, members, _ = sel
        ids = self.ledger.identities()
        holders = {r.page.url for key in {ids[r.rid] for r in found} for r in members.get(key, [])
                   if r.authority >= AUTHORITY["official"]}
        present = self.ledger.snapshots[k].pages
        uses: Counter = Counter()
        where: dict = {}
        for r in self.ledger.view(k):
            if r.authority < AUTHORITY["official"] or not r.links or not is_live(r, on_date):
                continue
            for _anchor, link in r.links:
                base, _, frag = link.partition("#")
                target = canonical_url(base)
                if "rate" in frag.lower() and target in holders and target in present:
                    url = _clean_link(link)
                    uses[url] += 1
                    where.setdefault(url, r)
        if not uses:
            return None
        url = max(sorted(uses), key=lambda u: (uses[u], -len(u)))
        return _cited(Result(f"You can find it here: {url}", self._page_row(url, k) or [where[url]], support=[where[url]]))


# ---- module helpers ----------------------------------------------------------------------
def _cited(res: Result) -> Result:
    """Order an answer's citations: among the pages that state the answering value, the most authoritative
    kind first (an official product or contact page before a press release, blog post, calculator or PDF),
    then the answering row's own page, then every other page that states it; one citation per page."""
    top = max((r.authority for r in res.rows), default=-1)
    seen = {r.page.url for r in res.rows}
    first, rest = [], []
    for r in sorted(res.first + res.support, key=lambda r: (-r.authority, r.page.url, r.rid)):
        if r.page.url in seen:
            continue
        seen.add(r.page.url)
        (first if r.authority > top else rest).append(r)
    res.first, res.support = first, rest
    return res



_ARTICLE_WORDS = {stem(w) for w in "news newsroom press release article blog story announcement".split()}
_HOURS_WORDS = {stem(w) for w in ("hour lobby drive up thru through open close branch branches office time times what "
                                  "general typical usual regular").split()}
_URL_WORDS = {stem(w) for w in "link url web page website address site".split()}


def _compatible(q: Question, row: Row) -> bool:
    """Row-level checks that every copy of one fact shares (term, metric); model years and balances are
    checked on the identity's winning row instead."""
    if terms_match(q.quals.terms, row.quals.terms) is False:
        return False
    if q.metric and q.metric != "rate" and not any(m == q.metric for m, _ in row.values):
        return False
    if q.wants_rate and not any(m in _RATE_FAMILY and "%" in v for m, v in row.values):
        return False
    return True


_RATE_FAMILY = ("rate", "dividend rate", "APY", "APR")
_ACCOUNT_KINDS = frozenset(stem(w) for w in ("ira", "roth"))
_RETIREMENT = frozenset(stem(w) for w in ("ira", "retirement"))
_TRADITIONAL = stem("traditional")


def _names_account_kind(q: Question, row: Row) -> bool:
    """Does the row carry every account kind the question names: IRA, Roth, and Traditional when the
    question is about retirement ('traditional IRA', not a 'traditional 30 year fixed home loan')?
    A table row carries it in its caption, label or section, a passage in its text or section."""
    asked = _ACCOUNT_KINDS.intersection(q.key_words)
    if _TRADITIONAL in q.key_words and _RETIREMENT.intersection(q.key_words):
        asked |= {_TRADITIONAL}
    where = f"{row.caption} {row.label}" if row.kind == "table" else row.text
    return not asked or asked <= set(tokens(f"{where} {row.section}"))


_GONE = "No longer offered:"
_WRAPPERS = frozenset({"roth", "traditional"})
_ELIGIBILITY_Q = re.compile(r"\b(who|eligib\w*|qualif\w*|requirements?|criteria)\b.{0,40}"
                           r"\b(join|become an? (\w+ )?member|membership|be an? member)\b|"
                           r"\b(join|membership)\b.{0,40}\b(eligib\w*|qualif\w*|requirements?|criteria)\b", re.I)
_ELIGIBILITY_HEAD = re.compile(r"\b(eligib\w*|who can)\b.*\b(join|member\w*)\b|\bfield of membership\b|"
                              r"\bmembership (requirements?|eligibility)\b", re.I)
_AREA_TERM = re.compile(r"\b(charter area|field of membership|service area|our (area|communities|footprint)|counties we serve)\b", re.I)
_STATES = re.compile(r"\b(Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
                     r"Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
                     r"Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|"
                     r"North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|"
                     r"Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\b")
# A request about the member's own account: their contact details, a lost card, signing in, an account question.
_SERVICE_Q = re.compile(
    r"\b(update|change|correct|edit|fix)\b[^?.]{0,40}\b(address|phone|e-?mail|name|contact|information|info|details)\b|"
    r"\b(lost|stolen|missing|misplaced)\b[^?.]{0,30}\bcard\b|\bcard\b[^?.]{0,30}\b(lost|stolen|missing)\b|"
    r"\b(can'?t|cannot|unable to|trouble|problems?|locked out|forgot)\b[^?.]{0,40}\b(log ?in|sign ?in|login|password|username)\b|"
    r"\b(questions?|problems?|issues?)\b[^?.]{0,20}\babout (my|an?) account\b|"
    r"\b(reach|contact|talk to|speak (to|with)|get in touch with)\b[^?.]{0,15}\b(you|dupaco|someone|a person|support|customer service)\b",
    re.I)
_ASKS_WHAT = re.compile(r"\s*(what|tell me|describe)\b", re.I)   # about a thing, not which/where/when/who/how
_ASKS_VALUE = re.compile(r"\b(what|how much)\b.*\b(pay|paid|earn|earned|rate|apy|apr|yield|charge|cost)\b", re.I)


def _same_figures(a: Optional[Row], b: Optional[Row]) -> bool:
    if a is None or b is None:
        return a is b
    return figures(a) == figures(b)


def _when(row: Row, k: int, prev: Optional[Row], snaps) -> str:
    """When a run's figures took effect: the printed effective date when the page gives a new one."""
    if row.effective and row.effective <= snaps[k].taken_on and (prev is None or prev.effective != row.effective):
        return f"effective {long_date(row.effective)}"
    return f"as of the {long_date(snaps[k].taken_on)} crawl"


def _window(runs: list, start_k: int) -> list:
    return [r for r in runs if r[1] >= start_k]


def _describe_history(q: Question, runs: list, start_k: int, end_k: int, metric: Optional[str], snaps) -> tuple:
    """One sentence on how a fact moved, and the rows it quotes."""
    listed = [r for r in runs if r[2] is not None]
    if not listed:
        return "", []
    last_row = listed[-1][2]
    name = _name(last_row)
    current = runs[-1][2] is not None and runs[-1][1] == end_k
    if q.change == "still":
        if current:
            return f"Yes, the {name} is still listed: {value_text(last_row, metric)}.", [last_row]
        gone = f"the {long_date(snaps[listed[-1][1] + 1].taken_on)} crawl" if listed[-1][1] + 1 < len(snaps) else "the newest crawl"
        if _ASKS_VALUE.search(q.raw):
            return (f"The {name} is no longer offered: it has not been listed on Dupaco's website since {gone}. When it was last "
                    f"listed ({long_date(snaps[listed[-1][1]].taken_on)} crawl) it paid {value_text(last_row, metric)}."), [last_row]
        return f"{_GONE} the {name} no longer appears on Dupaco's rate pages.", []
    steps = []   # (row, when it took effect, first snapshot) for each listed run, oldest first
    prev = None
    for k0, _k1, row in runs:
        if row is not None:
            steps.append((row, _when(row, k0, prev, snaps), k0))
        prev = row
    tail = "" if current else f" It is no longer listed on Dupaco's website (last listed in the {long_date(snaps[listed[-1][1]].taken_on)} crawl)."
    if q.change in ("before", "when"):
        if len(steps) < 2:
            return f"The {name} has not changed since it was first listed ({steps[0][1]}): {value_text(last_row, metric)}.{tail}", [last_row]
        (old, _w0, _), (new, w1, _) = steps[-2], steps[-1]
        verb = "pays" if current else "paid"
        if q.change == "when":
            return (f"The {name} last changed {w1}: from {value_text(old, metric)} to {value_text(new, metric)}.{tail}"), [new, old]
        return (f"Before its most recent change ({w1}), the {name} paid {value_text(old, metric)}; it now {verb} "
                f"{value_text(new, metric)}.{tail}"), [new, old]
    window = [st for st in steps if st[2] >= start_k or st is steps[-1]]
    base = [st for st in steps if st[2] < start_k]
    if base:
        window = [base[-1]] + [st for st in window if st is not base[-1]]
    if len(window) < 2:
        since = f" since {long_date(q.since)}" if q.since else ""
        return f"The {name} has not changed{since}: {value_text(last_row, metric)} ({window[-1][1]}).{tail}", [last_row]
    relabelled = len({r.label for r, _, _ in window}) > 1
    parts = [f"{value_text(r, metric)} ({r.label + ', ' if relabelled else ''}{w})" for r, w, _ in window]
    move = _direction(window[0][0], window[-1][0], metric)
    end = " which is the current rate." if current else "."
    return f"The {name} {move}: " + ", then ".join(parts) + "," + end + tail, [window[-1][0]] + [r for r, _, _ in window[:-1]]


def _direction(old: Row, new: Row, metric: Optional[str]) -> str:
    a, b = _headline(old, metric), _headline(new, metric)
    if a is None or b is None or a == b:
        return "changed"
    return f"went {'up' if b > a else 'down'} by {abs(b - a):.2f} percentage points" if "%" in value_text(new, metric) else "changed"


def _headline(row: Row, metric: Optional[str]) -> Optional[float]:
    m = _NUMBER.search(value_text(row, metric))
    return float(m.group(0).replace(",", "")) if m else None


def _name(r: Row) -> str:
    """'17 Month Fixed Certificate Special', or 'Auto & Motorcycle Loans 2024-27 Model Year (60 Months)'."""
    label = r.label
    if r.caption and not (set(tokens(r.caption)) - _CAPTION_FILLER) & set(tokens(label)):
        label = f"{r.caption} {label}"
    if r.qual_text:
        label += f" ({r.qual_text})"
    return label


_YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")


def _by_year(rivals: list) -> dict:
    """year -> rivals, when the copies of one statement each describe a different single year ('294
    employees used VTO in 2023', '256 ... in 2024'); empty when they are copies of one fact."""
    out: dict = defaultdict(list)
    for r in rivals:
        years = set(_YEAR.findall(r.text))
        if len(years) != 1:
            return {}
        out[int(years.pop())].append(r)
    return dict(out) if len(out) > 1 else {}


def _tightest_tier(facts: list) -> list:
    """With a dollar amount in the question, keep each product's narrowest tier that contains it."""
    best: dict = {}
    for s, key, w in facts:
        if w.quals.amount:
            g = (key.family, key.product, key.kinds, key.terms)
            width = w.quals.amount[1] - w.quals.amount[0]
            best[g] = min(best.get(g, width), width)
    return [f for f in facts if not f[2].quals.amount
            or f[2].quals.amount[1] - f[2].quals.amount[0] <= best[(f[1].family, f[1].product, f[1].kinds, f[1].terms)]]


def _specific(q: Question) -> bool:
    return bool(q.quals.terms or q.quals.years or q.quals.amount or q.superlative)


def _prefer_plainer(q: Question, facts: list, listing: bool) -> list:
    """A product named with words the question did not use loses to a plainer sibling of the same
    family, kinds and term that is also a candidate ('Student' vs plain certificates for a
    '12-month certificate')."""
    known: dict[tuple, set] = defaultdict(set)
    for _, key, _w in facts:
        known[(key.family, key.kinds, key.terms)].add(key.product)
    asked = set(q.product)
    out = []
    for s, key, w in facts:
        plainer = [n for n in known[(key.family, key.kinds, key.terms)] if n < key.product]
        if plainer:
            s *= (0.8 if listing else 0.55) ** max(len(key.product - n - asked) for n in plainer)
        out.append((s, key, w))
    return out


def _differs(a: Row, b: Row, q: Question) -> bool:
    """Do two copies of a fact disagree on a wanted figure they both state?"""
    wanted = _wanted_metrics(q)
    va, vb = _by_metric(a.values, wanted), _by_metric(b.values, wanted)
    return any(va[m] != vb[m] for m in va.keys() & vb.keys())


def _by_metric(values, wanted: set) -> dict:
    out: dict = defaultdict(list)
    for m, v in values:
        if m in wanted:
            out[m].append(v)
    return out


def _catalogue(q: Question) -> bool:
    return bool(re.search(r"\b(what|which)\b.*\b(do you|does dupaco|are)\b.*\b(offer|have|available)\b|\ball\b|\btypes? of\b", q.raw, re.I))


def _wanted_metrics(q: Question) -> set:
    if q.metric and q.metric != "rate":
        return {q.metric}
    return {"rate", "APR", "APY", "dividend rate", "fee"}   # a "minimum to open" is shown only when asked


def _dedupe(rows: list[Row]) -> list[Row]:
    seen, out = set(), []
    for r in rows:
        key = (norm(r.caption), norm(r.text))
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
    vals = [(m, v) for m, v in r.values if m in wanted] or [(m, v) for m, v in r.values if m not in _NEVER_SHOWN]
    label = r.label
    if r.caption and not (set(tokens(r.caption)) - _CAPTION_FILLER) & set(tokens(label)):
        label = f"{r.caption} - {label}"
    if r.qual_text:
        label += f" ({r.qual_text})"
    return f"{label}: " + ", ".join(f"{v} {m}" for m, v in vals)


_NEVER_SHOWN = ("points", "payment example", "minimum to open")
_CAPTION_FILLER = {stem(w) for w in "rates rate loans loan accounts account savings regular special".split()}


def _byline_key(row: Row) -> tuple:
    """How official a copy is and how new its printed byline date is (official pages carry none)."""
    return row.authority, row.page.published or ""


def _template(text: str) -> frozenset:
    """A statement's words with its figures (digits and ordinal counts) masked out."""
    return frozenset(t for t in tokens(COUNT.sub(" ", text)) if t not in STOPWORDS)


def _similar(a: frozenset, b: frozenset) -> bool:
    """Two statements of one claim: mostly the same words, or one restating the other with a few words
    added ('Forbes named Dupaco one of the top-rated credit unions in America' printed with and without
    'for a third year in a row')."""
    if not a or not b:
        return False
    shared = len(a & b)
    return shared / len(a | b) >= 0.6 or (min(len(a), len(b)) >= 8 and shared / min(len(a), len(b)) >= 0.8)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _is_header(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    upper = sum(c.isupper() for c in letters)
    return text.rstrip().endswith(":") or (len(letters) > 3 and upper / len(letters) > 0.6)


_DAY = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_TIME = r"(\d{1,2}(?::\d\d)?\s*[ap]\.?\s?m\.?|noon)"
_SCHEDULE = re.compile(_DAY + r"\s*(?:-|–|—|through|to)\s*" + _DAY + r"\W{1,3}" + _TIME + r"\s*(?:-|–|—|to)\s*" + _TIME, re.I)


def _schedules(text: str) -> list[tuple]:
    """(first day, last day, opens, closes) for each day-range schedule a text prints, normalised."""
    def t(x: str) -> str:
        return re.sub(r"[\s.]", "", x.lower())
    return [(a.lower(), b.lower(), t(o), t(c)) for a, b, o, c in _SCHEDULE.findall(text)]


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
