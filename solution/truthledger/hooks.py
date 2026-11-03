"""The seam where optional AI steps plug in. The defaults do nothing, so the system runs on rules alone.

An implementation (for example one that calls an LLM when an API key is present) may:

* `extra_rows(page)`: read a messy prose page at ingest time and return more ledger rows in the
  same shape the rule-based extractor produces (`extract.RawRow`). The rows then go through the
  same truth rules as every other row; the model never decides which value is true.
* `rewrite_question(question)`: restate an oddly worded question in plain words before parsing.
* `phrase(question, text)`: reword the final answer. The rewording is only kept if it states
  exactly the same numbers as the rule-built answer, so phrasing can never change a value.
"""
from __future__ import annotations

import re

from .extract import RawRow

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class Hooks:
    def extra_rows(self, page) -> list[RawRow]:
        return []

    def rewrite_question(self, question: str) -> str:
        return question

    def phrase(self, question: str, text: str) -> str:
        return text


def safe_phrase(hooks: Hooks, question: str, text: str) -> str:
    """Use the hook's wording only when it keeps every figure of the rule-built answer unchanged."""
    worded = hooks.phrase(question, text)
    if sorted(_NUMBER.findall(worded)) == sorted(_NUMBER.findall(text)):
        return worded
    return text
