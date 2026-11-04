"""The KnowledgeSystem the harness talks to: ingest -> ledger, answer -> rules, conflicts -> report.

With AI steps on (`ai.ClaudeHooks`), a question is first restated by the model (`Understanding`) and
the rules answer the restated question(s); the model never chooses a value. A comparison is answered
one product at a time by the rules and joined here. A final check keeps an AI-path answer only when
every number in it is one the rule-built answers stated; otherwise, and whenever the AI step is off,
fails or times out, the rules answer the original question exactly as they do without a key.
"""
from __future__ import annotations

import re
from typing import Optional

from .answer import Answerer, Result, _name
from .catalogue import family_matches
from .conflicts import find_conflicts
from .hooks import Hooks, Understanding, numbers, safe_phrase
from .ledger import Ledger

_MAX_SOURCES = 5
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_COMPARED = ("APY", "APR", "rate", "dividend rate")


class LedgerKnowledge:
    def __init__(self, contract, hooks: Optional[Hooks] = None) -> None:
        self._c = contract            # the harness's contract module (Answer, Source, Conflict)
        self.hooks = hooks or Hooks()  # optional AI steps; the default does nothing
        self.ledger = Ledger(extra_rows=self.hooks.extra_rows)
        self._answerer: Optional[Answerer] = None
        self._cache: dict = {}

    def ingest(self, snapshot) -> None:
        self.ledger.add_snapshot(snapshot.snapshot_id, snapshot.taken_on, snapshot.pages)
        self._cache.clear()
        self._answerer = Answerer(self.ledger)   # index now: loading is untimed, answering is
        self.ledger.timeline().warm()
        self.hooks.prepare(self.ledger)

    def answer(self, question: str, as_of: Optional[str] = None):
        key = (question, as_of)
        if key not in self._cache:
            self._cache[key] = self._answer(question, as_of)
        return self._cache[key]

    def _answer(self, question: str, as_of: Optional[str]):
        c = self._c
        if not self.ledger.snapshots:
            return c.Answer(text="I don't know yet; nothing has been loaded.", confident=False)
        res, k = self._answerer.answer(question, as_of)
        understood = self.hooks.understand(question)
        if understood is not None:
            found = self._with_ai(question, understood, res, as_of)
            if found is not None:
                res, k = found
        snap = self.ledger.snapshots[k].snapshot_id
        sources, seen = [], set()
        for row in list(res.rows) + list(res.support):
            if row.page.url in seen or len(sources) >= _MAX_SOURCES:
                continue
            seen.add(row.page.url)
            sid = snap if self.ledger.in_crawl(row, k) else self.ledger.snapshots[row.page.first_snapshot].snapshot_id
            sources.append(c.Source(url=row.page.url, snapshot_id=sid, quote=row.text[:240]))
        text = safe_phrase(self.hooks, question, res.text) if res.confident else res.text
        return c.Answer(text=text, sources=sources, confident=res.confident, conflict_note=res.note)

    def _with_ai(self, question: str, u: Understanding, rules: Result, as_of: Optional[str]) -> Optional[tuple]:
        """The rules' answer to the AI's restatement, when it should replace their answer to the member's own
        words; None keeps the rules' answer. The restatement is used only where the rules could not read the
        original: it compares products head to head, the rules would read a calendar year as a vehicle model
        year, the restatement shows a question kind (a change over time, a rate) the rules' reading missed, or
        the rules found nothing. It must keep what the rules did read (product family, account kinds, term), and
        it may overrule a refusal only with a rate-table answer: prose matching is too loose to beat a refusal."""
        if u.compare:
            found = self._understood(u, as_of)
            return found if found is not None and found[0].confident else None
        own = self._answerer.reading(question)
        new = self._answerer.reading(u.questions[0], u.calendar_year)
        if not _faithful(own, new):
            return None
        if not rules.confident:
            if not (new.family or new.product):
                return None   # 'What is the APR?' for a word the model could not place: keep the refusal
            found = self._understood(u, as_of)
            if found is not None and found[0].confident and any(r.kind != "table" for r in found[0].rows):
                return None
            return found
        if u.calendar_year and own.quals.years == (u.calendar_year, u.calendar_year):
            return self._understood(u, as_of)
        if own.wants_url or own.hours or new.wants_url or new.hours:
            return None
        if (new.change and not own.change) or (_tabled(new) and not _tabled(own)):
            return self._understood(u, as_of)
        return None

    def _understood(self, u: Understanding, as_of: Optional[str]) -> Optional[tuple]:
        """The rules' answer to the AI's restatement, or None to fall back to the original question."""
        if not u.compare:
            return self._answerer.answer(u.questions[0], as_of, calendar_year=u.calendar_year)
        parts = [self._answerer.answer(q, as_of, calendar_year=u.calendar_year, siblings=False) for q in u.questions]
        res = compare(parts)
        if not grounded(res.text, [r.text for r, _ in parts]):
            return None
        return res, parts[0][1]

    def conflicts(self):
        return find_conflicts(self.ledger, self._c)


def _faithful(own, new) -> bool:
    """A restatement keeps the product family, account kinds and terms the rules read in the original."""
    if own.family and not family_matches(own.family, new.family or ""):
        return False
    if not own.kinds <= new.kinds:
        return False
    return not own.quals.terms or own.quals.terms <= new.quals.terms


def _tabled(q) -> bool:
    return bool(q.rate_intent or q.rate_implied)


def compare(parts: list) -> Result:
    """Join the rules' answers for each compared product, and say which has the higher figure when each
    side is a single fact with a figure of the same measure. Every figure comes from a winning row."""
    answered = [r for r, _ in parts if r.confident]
    if not answered:
        return parts[0][0]
    lines = [r.text for r in answered]
    if len(answered) < len(parts):
        lines.append("The other product you asked about isn't listed on Dupaco's website.")
    verdict = _verdict(answered) if len(answered) == len(parts) else None
    if verdict:
        lines.append(verdict)
    rows, support, seen = [], [], set()
    for r in answered:
        for row in r.rows:
            if row.rid not in seen:
                seen.add(row.rid)
                rows.append(row)
        support.extend(r.support)
    note = next((r.note for r in answered if r.note), None)
    return Result(" ".join(lines), rows, note=note, support=support)


def _verdict(answered: list) -> Optional[str]:
    if any(len(r.rows) != 1 for r in answered):
        return None
    rows = [r.rows[0] for r in answered]
    for metric in _COMPARED:
        figs = []
        for row in rows:
            v = next((v for m, v in row.values if m == metric and _PERCENT.search(v)), None)
            if v is None:
                break
            figs.append(float(_PERCENT.search(v).group(1)))
        if len(figs) != len(rows):
            continue
        if len(set(figs)) == 1:
            return f"They have the same {metric}."
        top = max(range(len(rows)), key=lambda i: figs[i])
        if figs.count(figs[top]) > 1:
            return None
        return f"The {_name(rows[top])} has the higher {metric}."
    return None


def grounded(text: str, rule_texts: list) -> bool:
    """The final check: every number in an AI-path answer must be one a rule-built answer stated."""
    allowed = set(n for t in rule_texts for n in numbers(t))
    return all(n in allowed for n in numbers(text))
