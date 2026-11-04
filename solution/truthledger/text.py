"""Small text helpers shared by extraction and question parsing: cleaning, dates, numbers."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-eight": 48, "fifty": 50, "sixty": 60, "seventy-two": 72, "eighty-four": 84,
    "twenty-four": 24, "thirty-six": 36,
}

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
_EMPH = re.compile(r"(\*\*|__|(?<![\w*])\*(?!\s)|(?<!\w)_(?!\s))")
_SPACE = re.compile(r"\s+")


def clean_inline(s: str) -> str:
    """Markdown line -> plain text: drop images, keep link text, drop emphasis and escapes."""
    s = _IMAGE.sub("", s)
    s = _LINK.sub(lambda m: m.group(1), s)
    s = s.replace("\\\\r\\\\n", " ").replace("\\r\\n", " ").replace("\\\\", "")
    s = re.sub(r"\\([#*_\[\]()$&|-])", r"\1", s)
    s = _EMPH.sub("", s)
    return _SPACE.sub(" ", s).strip()


def links(s: str) -> list[tuple[str, str]]:
    """(anchor text, url) pairs in a markdown line."""
    return [(clean_inline(m.group(1)), m.group(2)) for m in _LINK.finditer(_IMAGE.sub("", s))]


def norm(s: str) -> str:
    """Lower-case, unify dashes and whitespace."""
    s = s.lower().replace("\u2013", "-").replace("\u2014", "-").replace("’", "'")
    return _SPACE.sub(" ", s).strip()


_DATE_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_DATE_LONG = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_YEAR = re.compile(rf"\b({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.I)


def _mk(y: int, m: int, d: int) -> Optional[str]:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def find_dates(s: str) -> list[str]:
    """Every full date written in the text, as ISO strings, in order of appearance."""
    found = []
    for m in _DATE_NUMERIC.finditer(s):
        found.append((m.start(), _mk(int(m.group(3)), int(m.group(1)), int(m.group(2)))))
    for m in _DATE_LONG.finditer(s):
        found.append((m.start(), _mk(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))))
    for m in _DATE_ISO.finditer(s):
        found.append((m.start(), _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    return [d for _, d in sorted(found) if d]


def month_year_end(s: str) -> Optional[str]:
    """'May 2026' -> last day of that month (the latest moment the phrase covers)."""
    m = _MONTH_YEAR.search(s)
    if not m:
        return None
    y, mo = int(m.group(2)), MONTHS[m.group(1).lower()]
    nxt = date(y + (mo == 12), mo % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1).isoformat()


_MID_MONTH = re.compile(rf"\bmid[-\s]?({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.I)


def date_asked(s: str, today: Optional[str] = None) -> Optional[str]:
    """The date a question asks about: a full date ('May 15, 2026', '6/1/2026'), else the middle
    of a month for 'mid-May 2026', else the last day of a named month, else (with `today`, the
    newest crawl's date) a relative date: 'last month', 'two months ago', 'this past May'."""
    dates = find_dates(s)
    if dates:
        return dates[0]
    m = _MID_MONTH.search(s)
    if m:
        return _mk(int(m.group(2)), MONTHS[m.group(1).lower()], 15)
    return month_year_end(s) or (relative_date(s, today) if today else None)


_COUNT_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a few": 3, "several": 3}
_AGO = re.compile(r"\b(\d+|a couple of|a few|several|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
                  r"(day|week|month|year)s?\s+ago\b", re.I)
_LAST_UNIT = re.compile(r"\b(last|previous|past)\s+(week|month|quarter)\b", re.I)
_BARE_MONTH = re.compile(rf"\b(?:in|this past|last|back in|during|earlier this year,? in|as of|since|after|before|from|between)\s+"
                         rf"(?:early\s+|mid[-\s]?|late\s+)?({_MONTH_ALT})\b(?![\s,]*\d{{4}})", re.I)


def _shift_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    m += 1
    last = (date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1)
    return date(y, m, min(d.day, date.fromordinal(last).day))


def _month_end(y: int, m: int) -> date:
    return date.fromordinal(date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1)


def relative_date(s: str, today: str) -> Optional[str]:
    """A date written relative to `today`: 'two months ago', 'last month' (its last day), 'this past
    May' or 'in May' without a year (the latest May on or before today, its last day)."""
    now = date.fromisoformat(today[:10])
    m = _AGO.search(s)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _COUNT_WORDS[m.group(1).lower()]
        unit = m.group(2).lower()
        if unit == "day":
            return date.fromordinal(now.toordinal() - n).isoformat()
        if unit == "week":
            return date.fromordinal(now.toordinal() - 7 * n).isoformat()
        return _shift_months(now, 12 * n if unit == "year" else n).isoformat()
    m = _LAST_UNIT.search(s)
    if m:
        unit = m.group(2).lower()
        if unit == "week":
            return date.fromordinal(now.toordinal() - 7).isoformat()
        back = _shift_months(now, 3 if unit == "quarter" else 1)
        return _month_end(back.year, back.month).isoformat()
    m = _BARE_MONTH.search(s)
    if m:
        mo = MONTHS[m.group(1).lower()]
        y = now.year if mo <= now.month else now.year - 1
        return _month_end(y, mo).isoformat()
    return None


def strip_dates(s: str) -> str:
    """The text with every written date ('May 15, 2026', '6/1/2026', 'May 2026') blanked out."""
    for pattern in (_DATE_NUMERIC, _DATE_LONG, _DATE_ISO, _MONTH_YEAR):
        s = pattern.sub(" ", s)
    return s


EFFECTIVE = re.compile(r"effective\s+(?:as\s+of\s+|on\s+|beginning\s+)?(\S+(?:\s+\d{1,2},?\s+\d{4})?)", re.I)


def effective_date(s: str) -> Optional[str]:
    """The date in an 'effective as of <date>' phrase, if any."""
    for m in EFFECTIVE.finditer(s):
        ds = find_dates(s[m.start(): m.end() + 20])
        if ds:
            return ds[0]
    return None


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")


def percents(s: str) -> list[str]:
    return [m.group(1) + "%" for m in _PCT.finditer(s)]


def money_values(s: str) -> list[float]:
    return [float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")) for m in _MONEY.finditer(s)]


def strip_footnote(cell: str) -> str:
    """'4.20%1' -> '4.20%', '5.19% APR1' -> '5.19% APR', '12.50% \u2013 19.50%2' -> '12.50% \u2013 19.50%'."""
    cell = re.sub(r"%\s?[¹²³\d]{1,2}\b", "%", cell)
    cell = re.sub(r"\b(APR|APY)[¹²³\d]{1,2}\b", r"\1", cell)
    return cell.strip()


def has_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in s)


# A count can be written as an ordinal word: "the fifth time Forbes recognized Dupaco", "the eighth
# consecutive year". Such words are figures of a statement just like digits.
ORDINAL_WORDS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                 "eleventh", "twelfth")
COUNT = re.compile(r"\b(?:" + "|".join(ORDINAL_WORDS) + r")\b|\d[\d,]*(?:\.\d+)?", re.I)


def counts(s: str) -> list[str]:
    """The figures a statement gives, digits and ordinal words alike, in order ('fifth', '22', '3.6')."""
    return [m.group(0).lower() for m in COUNT.finditer(s)]


_ORDINAL_COUNT = re.compile(r"\b(?:" + "|".join(ORDINAL_WORDS) + r")\s+(?:consecutive\s+|straight\s+|annual\s+)?"
                            r"(?:time|year|award|honor|recognition)s?\b", re.I)


def states_count(s: str) -> bool:
    """Does the text state a figure: a digit, or an ordinal count ('the eighth consecutive year', 'the fifth
    time'), not an ordinal used as a name ('first-time homebuyer')?"""
    return has_digit(s) or bool(_ORDINAL_COUNT.search(s))


_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December"]


def long_date(iso: str) -> str:
    """'2026-09-01' -> 'September 1, 2026'."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{_MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"
