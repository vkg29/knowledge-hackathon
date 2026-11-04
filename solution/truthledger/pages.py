"""Page classification: what kind of page a URL is, and how much its facts are trusted.

The kind drives the first truth rule: an official product or rate page outranks a promotion
landing page, which outranks a downloaded document, a press release, a blog post and finally a
calculator (calculators embed rate tables that are rarely refreshed).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .text import find_dates

# Higher wins. The gaps leave room for the effective-date and recency tie-breakers.
AUTHORITY = {
    "rate_sheet": 6,   # official page that prints a dated rate table
    "official": 5,     # official product / service page
    "promo": 4,        # campaign landing page (true while the offer runs)
    "document": 3,     # PDF / spreadsheet download
    "news": 2,         # press release
    "blog": 1,         # article
    "calculator": 0,   # calculator widgets with embedded sample rates
}

_PROMO = re.compile(r"(-lp$|switch-and-earn|offer-expired|earn-more|checking-offer|money-makeover|sprint-to-savings)")


def url_path(url: str) -> str:
    p = urlparse(url)
    return (p.path or "/").rstrip("/") or "/"


def page_kind(url: str, title: str = "") -> str:
    path = url_path(url).lower()
    if "calculator" in path or path.startswith("/calculators") or "-calculator" in title.lower():
        return "calculator"
    if path.startswith("/hubfs") or re.search(r"\.(pdf|xlsx?|docx?)$", path):
        return "document"
    if path.startswith("/newsroom") or path.startswith("/resources/dupaco-news"):
        return "news"
    if (path.startswith("/learn") or path.startswith("/business/resources/") or path.startswith("/dupacodaily")
            or re.match(r"^/20\d\d/", path)):
        return "blog"
    if _PROMO.search(path):
        return "promo"
    return "official"


def canonical_url(url: str) -> str:
    """Drop tracking query strings and trailing slashes so one page has one identity."""
    p = urlparse(url)
    query = "" if any(k in p.query for k in ("campaignId", "hsLang", "scrollTo")) else p.query
    path = p.path.rstrip("/") or ""
    return f"{p.scheme}://{p.netloc}{path}" + (f"?{query}" if query else "")


def published_date(markdown: str) -> str | None:
    """Byline date printed near the top of news and blog pages ('June 18, 2024')."""
    head = "\n".join(markdown.splitlines()[:12])
    dates = find_dates(head)
    return dates[0] if dates else None
