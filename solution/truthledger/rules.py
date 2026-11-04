"""The truth rules. Given ledger rows that state the same fact, decide which one is true.

In priority order:

1. A statement that has ended is not current: rows from a page that says the offer has ended,
   or rows that carry their own past end date ("offer ended 2025-09-30"), drop out of current
   answers. They still answer "as of" questions for the time they were live.
2. Authority: an official rate sheet beats an official product page, which beats a promotion
   page, a downloaded document, a press release, a blog post and finally a calculator.
3. Recency among equals: the date printed on the page wins ("Rates are effective as of ...", or
   a press release's byline date), then the crawl that last saw the page.

Identity is settled before these rules run (`identity.py`): rows for different products, terms,
tiers, model-year slots or account kinds are different facts and never compete, and the metrics
of one row (rate vs APY) are compared metric by metric.
"""
from __future__ import annotations

import re
from typing import Optional

from .text import find_dates

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


def explain(winner, loser, current: bool = True) -> str:
    """Why `winner` beat `loser`, in one sentence. `current` is False when the loser's page no longer
    states it in the snapshot being judged."""
    if not current and winner.page.url != loser.page.url:
        return (f"{_kind_name(loser)} ({loser.page.url}) no longer states it in the newest crawl; "
                f"{_kind_name(winner)} ({winner.page.url}) does")
    if winner.authority != loser.authority:
        return (f"{_kind_name(winner)} ({winner.page.url}) outranks {_kind_name(loser)} ({loser.page.url})")
    if winner.dated != loser.dated:
        return f"newer statement: dated {winner.dated} on {winner.page.url} vs {loser.dated} on {loser.page.url}"
    if winner.page.url == loser.page.url:
        return f"seen in a newer crawl of {winner.page.url}"
    if winner.page.fetched_at != loser.page.fetched_at:
        return f"equally official and dated; {winner.page.url} was seen in a newer crawl than {loser.page.url}"
    return f"equally official and dated; kept {winner.page.url} over {loser.page.url}"


def _kind_name(row) -> str:
    return {6: "official rate sheet", 5: "official product page", 4: "promotion page", 3: "downloaded document",
            2: "press release", 1: "blog post", 0: "calculator page"}[row.authority]
