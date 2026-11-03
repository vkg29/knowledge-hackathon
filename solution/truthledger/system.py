"""The KnowledgeSystem the harness talks to: ingest -> ledger, answer -> rules, conflicts -> report."""
from __future__ import annotations

from typing import Optional

from .answer import Answerer
from .conflicts import find_conflicts
from .ledger import Ledger


class LedgerKnowledge:
    def __init__(self, contract) -> None:
        self._c = contract            # the harness's contract module (Answer, Source, Conflict)
        self.ledger = Ledger()
        self._answerer: Optional[Answerer] = None
        self._cache: dict = {}

    def ingest(self, snapshot) -> None:
        self.ledger.add_snapshot(snapshot.snapshot_id, snapshot.taken_on, snapshot.pages)
        self._cache.clear()
        self._answerer = Answerer(self.ledger)   # index now: loading is untimed, answering is

    def _engine(self) -> Answerer:
        if self._answerer is None:
            self._answerer = Answerer(self.ledger)
        return self._answerer

    def answer(self, question: str, as_of: Optional[str] = None):
        key = (question, as_of)
        if key not in self._cache:
            self._cache[key] = self._answer(question, as_of)
        return self._cache[key]

    def _answer(self, question: str, as_of: Optional[str]):
        c = self._c
        if not self.ledger.snapshots:
            return c.Answer(text="I don't know yet; nothing has been loaded.", confident=False)
        res, k = self._engine().answer(question, as_of)
        snap = self.ledger.snapshots[k].snapshot_id
        sources, seen = [], set()
        for row in res.rows:
            if row.page.url in seen:
                continue
            seen.add(row.page.url)
            sid = snap if self.ledger.in_crawl(row, k) else self.ledger.snapshots[row.page.first_snapshot].snapshot_id
            sources.append(c.Source(url=row.page.url, snapshot_id=sid, quote=row.text[:240]))
        return c.Answer(text=res.text, sources=sources, confident=res.confident, conflict_note=res.note)

    def conflicts(self):
        return find_conflicts(self.ledger, self._c)
