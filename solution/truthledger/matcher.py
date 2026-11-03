"""The meaning matcher: finds ledger rows that talk about what a question asks.

It is a BM25 index over each row's own words plus its context (table caption, heading path,
page title), with question words expanded through the banking thesaurus in `vocab.py`. It runs
locally, needs no model download or key, and answers in milliseconds. Its only job is *finding*
candidate rows; deciding which of them is true is left to `rules.py`.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .vocab import STOPWORDS, tokens, with_prefixes

K1, B = 1.2, 0.5


CONTEXT_WEIGHT = 0.4
FAQ_WEIGHT = 0.8


def row_document(row) -> dict[str, float]:
    """Words a row is findable by: its own text at full weight, plus its subject, heading path and
    page title at reduced weight (context helps find a row but should not outvote its content)."""
    doc: dict[str, float] = {}
    for t in with_prefixes([t for t in tokens(row.text) if t not in STOPWORDS]):
        doc[t] = doc.get(t, 0.0) + 1.0
    if row.kind == "table":   # a row label like "60 Month" needs its caption to be findable
        for t in dict.fromkeys(with_prefixes([t for t in tokens(row.subject) if t not in STOPWORDS])):
            doc[t] = max(doc.get(t, 0.0), 1.0)
    ctx = " ".join([row.subject, row.section, row.page.title.split("|")[0]])
    for t in dict.fromkeys(with_prefixes([t for t in tokens(ctx) if t not in STOPWORDS])):
        doc[t] = doc.get(t, 0.0) + CONTEXT_WEIGHT
    faq = row.section.rsplit(" > ", 1)[-1]
    if faq.endswith("?"):   # an FAQ answer is findable by its question, which is how members ask
        for t in dict.fromkeys(with_prefixes([t for t in tokens(faq) if t not in STOPWORDS])):
            doc[t] = doc.get(t, 0.0) + FAQ_WEIGHT - CONTEXT_WEIGHT
    return doc


class LexicalIndex:
    def __init__(self, docs: list[dict[str, float]]) -> None:
        self.n = len(docs)
        self.lengths = [sum(d.values()) for d in docs]
        self.avg = (sum(self.lengths) / self.n) if self.n else 1.0
        postings: dict[str, dict[int, float]] = defaultdict(dict)
        for i, d in enumerate(docs):
            for t, tf in d.items():
                postings[t][i] = tf
        self.postings = dict(postings)
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def scores(self, query: dict[str, float], allowed=None) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for term, w in query.items():
            plist = self.postings.get(term)
            if not plist:
                continue
            idf = self.idf[term] * w
            for doc, tf in plist.items():
                if allowed is not None and doc not in allowed:
                    continue
                dl = self.lengths[doc]
                out[doc] += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * dl / self.avg))
        return out

    def coverage(self, query: dict[str, float], doc_terms: set) -> float:
        """Share of the question's (idf-weighted) words that a row contains."""
        total = sum(self.idf.get(t, 0.0) * w for t, w in query.items() if w >= 1.0)
        hit = sum(self.idf.get(t, 0.0) * w for t, w in query.items() if w >= 1.0 and t in doc_terms)
        return hit / total if total else 0.0
