"""The seam where optional AI steps plug in. The defaults do nothing, so the system runs on rules alone.

An implementation (`ai.ClaudeHooks` calls Claude when an API key is present) may:

* `prepare(ledger)`: called after every ingest, once the ledger is built (loading is untimed).
* `understand(question)`: restate an oddly worded question in the plain wording the rule parser reads,
  split a head-to-head comparison into one question per product, and say whether a year in it is a
  calendar year or a vehicle model year (`Understanding`). The rules still find the facts and the
  truth rules still pick every value; the model never sees a figure from the site.
* `extra_rows(page)`: read a messy prose page at ingest time and return more ledger rows in the
  same shape the rule-based extractor produces (`extract.RawRow`). The rows then go through the
  same truth rules as every other row; the model never decides which value is true.
* `phrase(question, text)`: reword the final answer. The rewording is only kept if it states
  exactly the same numbers as the rule-built answer, so phrasing can never change a value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .extract import RawRow

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Understanding:
    questions: tuple            # one plain restatement, or one standalone question per compared product
    compare: bool = False       # a head-to-head comparison of the products in `questions`
    calendar_year: Optional[int] = None   # a year asked about as a point in time ('the rate in 2025')
    model_year: Optional[int] = None      # a vehicle model year ('a 2025 truck')


class Hooks:
    name = "rules only"

    def prepare(self, ledger) -> None:
        return None

    def understand(self, question: str) -> Optional[Understanding]:
        return None

    def extra_rows(self, page) -> list[RawRow]:
        return []

    def phrase(self, question: str, text: str) -> str:
        return text


def numbers(text: str) -> list:
    return sorted(_NUMBER.findall(text))


def safe_phrase(hooks: Hooks, question: str, text: str) -> str:
    """Use the hook's wording only when it keeps every figure of the rule-built answer unchanged."""
    worded = hooks.phrase(question, text)
    if numbers(worded) == numbers(text):
        return worded
    return text
