"""Question parsing: what a member is asking for, independent of how they phrased it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .qualifiers import Qualifiers, parse_question_qualifiers
from .text import MONTHS, month_year_end, norm
from .vocab import GENERIC, STOPWORDS, content_tokens, expand, tokens

_RATE_WORDS = re.compile(r"\b(rates?|apy|apr|yield|dividends?|interest|earn|pays?|percent|%)", re.I)
_LIST_WORDS = re.compile(r"\b(what|which)\b.*\b(do you|does dupaco)\s+(offer|have)\b|\ball\b|\blist\b|"
                         r"\b(rates|cards|accounts|certificates|options)\b(?!\s+(on|for)\s+(a|an|the)\b)", re.I)
_SUPER_MAX = re.compile(r"\b(highest|best|top|maximum|max|most)\b", re.I)
_SUPER_MIN = re.compile(r"\b(lowest|cheapest|minimum|least)\b", re.I)
_URL_WORDS = re.compile(r"\b(link|url|web ?page|website address|web address)\b", re.I)
_PAST = re.compile(r"\b(was|were|used to|back in|as of|previously|before)\b", re.I)


@dataclass
class Question:
    raw: str
    words: dict              # weighted query tokens (thesaurus-expanded)
    key_words: list          # content words that name the thing asked about
    quals: Qualifiers
    metric: Optional[str]    # "APY" | "APR" | "dividend rate" | "rate" | None
    rate_intent: bool
    wants_list: bool
    superlative: Optional[str]
    wants_url: bool
    hours: Optional[str]     # "lobby" | "drive-up" | "any" | None
    as_of_hint: Optional[str]
    names: list              # capitalised names in the question ('Galena', 'Mullan Avenue')
    wants_number: bool       # "how many", "how much", "what number"
    answer_type: Optional[str]  # "address" | "phone" | "date" | None: the shape the answer must have
    wants_action: bool       # "how do I ...", "where can I ...": a link to act on helps


def parse(question: str) -> Question:
    q = question.strip()
    low = norm(q)
    toks = content_tokens(q)
    metric = None
    if re.search(r"\bapy\b|annual percentage yield", low):
        metric = "APY"
    elif re.search(r"\bapr\b|annual percentage rate", low):
        metric = "APR"
    elif re.search(r"\bdividend", low):
        metric = "dividend rate"
    rate_intent = bool(_RATE_WORDS.search(low)) and not re.search(r"\binterest(ed)? in\b", low)
    hours = None
    if re.search(r"\bhours?\b|what time", low) and not rate_intent:
        if "lobby" in low:
            hours = "lobby"
        elif re.search(r"drive[- ]?(up|thru|through)", low):
            hours = "drive-up"
        elif re.search(r"\bhours?\b", low):
            hours = "any"
    as_of_hint = month_year_end(q) if _PAST.search(low) else None
    dateless = re.sub(r"\b(19|20)\d\d\b|\b(january|february|march|april|may|june|july|august|september|october|"
                      r"november|december)\b|\btoday\b", " ", q, flags=re.I)
    key_words = [t for t in content_tokens(dateless) if t not in GENERIC]
    return Question(
        raw=q,
        words=_boost(expand(toks), _names(q)),
        key_words=key_words,
        quals=parse_question_qualifiers(q),
        metric=metric,
        rate_intent=rate_intent,
        wants_list=bool(_LIST_WORDS.search(low)),
        superlative="max" if _SUPER_MAX.search(low) else ("min" if _SUPER_MIN.search(low) else None),
        wants_url=bool(_URL_WORDS.search(low)),
        hours=hours,
        as_of_hint=as_of_hint,
        names=_names(q),
        answer_type=_answer_type(low),
        wants_action=bool(re.search(r"\bhow (do|can|should) i\b|\bwhere (do|can|should) i\b|\bonline\b|\bapply\b|\bsign up\b", low)),
        wants_number=bool(re.search(r"\bhow (many|much|long)\b(?! times)|\bnumber of\b|\bwhat (number|percent)|^when\b", low)),
    )


_NOT_NAMES = {"dupaco", "i", "visa", "apy", "apr", "ira", "hsa", "cd", "lobby", "hours", "drive", "branch",
              "what", "when", "where", "who", "how", "is", "are", "can", "do", "does", "the", "a", "my", "mobile",
              "shine", "online", "credit", "union", "monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday"} | set(MONTHS)


def _answer_type(low: str) -> Optional[str]:
    if re.search(r"\baddress\b|where is .*\blocated\b|\blocated at\b", low) and "email" not in low and "web" not in low:
        return "address"
    if re.search(r"\bphone\b|\btelephone\b|\bcall\b|\bnumber to (call|reach)\b", low):
        return "phone"
    if re.search(r"^when\b|\bwhat date\b|\bstill open\b|\bstill (available|operating)\b|\bclos(e|ed|ing) (date|for good)\b", low):
        return "date"
    return None


def _boost(words: dict, names: list) -> dict:
    """Names are the most specific words a member uses; weigh them up."""
    return {t: (w * 1.5 if t in names else w) for t, w in words.items()}


def _names(question: str) -> list:
    """Stemmed tokens of capitalised words after the first word: usually a place, brand or product."""
    words = re.findall(r"[A-Za-z][A-Za-z$'-]*", question)
    out = []
    for w in words[1:]:
        low = w.lower().removesuffix("'s")
        if w[0].isupper() and low not in _NOT_NAMES and not w.isupper():
            out.extend(t for t in tokens(low) if t not in STOPWORDS)
    return list(dict.fromkeys(out))


__all__ = ["Question", "parse", "STOPWORDS"]
