"""Question parsing: what a member is asking for, independent of how they phrased it."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from .catalogue import product_words, question_family
from .identity import KINDS
from .qualifiers import PAST_INTRO, Qualifiers, parse_question_qualifiers, strip_model_years
from .text import MONTHS, date_asked, norm, relative_date, strip_dates
from .vocab import GENERIC, STOPWORDS, content_tokens, expand, tokens

_RATE_WORDS = re.compile(r"\b(rates?|apys?|aprs?|yields?|dividends?|interest|percent)\b|%", re.I)
_NOT_PRICE = r"(?!\s+(with|for|by|off|my|bills?|using)\b)"
_EARN_WORDS = re.compile(r"\b(pay|pays|paying|paid|earn|earns|earning)\b" + _NOT_PRICE)
_COST_WORDS = re.compile(r"\b(charge|charges|cost)\b" + _NOT_PRICE)
_RATE_PRODUCTS = re.compile(r"\b(certificates?|cds?|savings|money market|checking|loans?|mortgages?|heloc|ira|visa|credit cards?|special)\b")
_LIST_WORDS = re.compile(r"\b(what|which)\b.*\b(do you|does dupaco)\s+(offer|have)\b|\ball\b|\blist\b|"
                         r"\b(rates|aprs|apys|fees|cards|loans|accounts|certificates|options|products)\b(?!\s+(on|for)\s+(a|an|the)\b)", re.I)
_SUPER_MAX = re.compile(r"\b(highest|best|top|maximum|max|most)\b", re.I)
_SUPER_MIN = re.compile(r"\b(lowest|cheapest|minimum|least)\b", re.I)
_URL_WORDS = re.compile(r"\b(link|url|web ?page|website address|web address)\b|"
                        r"\bwhere on (dupaco'?s |the |your )?(web ?)?site\b|\b(which|what) page\b", re.I)
_PAST = re.compile(r"\b(was|were|used to|back in|as of|previously|before|did|had|paid)\b|\bago\b|\blast (month|week|quarter)\b|"
                   r"\bthis past\b|\bearlier this year\b", re.I)
# "What changed since May?", "what did it pay before it went up?", "when did it last change?",
# "is it still offered?": questions answered from a fact's history, not one snapshot. "Over the past
# year" / "in the last year" is the trailing twelve months, not the previous calendar year.
_TRAILING_YEAR = re.compile(r"\b(?:the|this) (?:past|last) year\b", re.I)
_CHANGE = re.compile(r"\b(changed|went (up|down)|gone (up|down)|increased|decreased|raised|rose|risen|dropped|lowered|fell|moved|"
                     r"history|over time|trend(s|ed|ing)?|happen(ed|ing)?)\b|" + _TRAILING_YEAR.pattern, re.I)
# Verbs that also compare products ("lower than the jumbo", "does it go up with a bigger balance",
# "how does it change with the term"): a change only in a question about time.
_SHIFT = re.compile(r"\b(chang(e|es|ing)|go(es|ing)? (up|down)|increas(e|es|ing)|decreas(e|es|ing)|rais(e|es|ing)|"
                    r"drop(s|ping)?|lower(s|ing)?|higher|cut(s|ting)?|mov(e|es|ing)|compar(e|ed|es|ing)|differ(s|ent|ence)?)\b",
                    re.I)
_TIME = re.compile(r"\b(did|was|were|been|since|ago|used to|last|recent(ly)?|previous(ly)?|earlier)\b|\b(19|20)\d\d\b|"
                   r"\b(january|february|march|april|june|july|august|september|october|november|december)\b|"
                   r"\b(in|since|than|from|before|after|until|by|to|of|during|past) may\b|" + _TRAILING_YEAR.pattern, re.I)
_BEFORE = re.compile(r"\bbefore (it|its|the|this|that)\b[^.?,;]{0,30}?\b(chang|increas|decreas|rais|rose|ris|hike|cut|drop|lower|"
                     r"fell|reduc|updat|went|gone|go(es)? (up|down))|\bprevious(ly)?\b|\bprior\b|\bused to\b|\bold (rate|apy|apr)\b", re.I)
_WHEN_CHANGED = re.compile(r"^\s*when\b.*\b(chang|updat|increas|decreas|rais|lower|go up|went up|go down|went down)", re.I)
_STILL = re.compile(r"\bstill (offered|available|open|around|running|listed|being offered|offering|have|has|exist)|"
                    r"\b(no longer|discontinued|stopped offering|go away|went away|ended)\b", re.I)
_SINCE = re.compile(r"\b(?:since|after|from|between)\b", re.I)


@dataclass
class Question:
    raw: str
    words: dict              # weighted query tokens (thesaurus-expanded)
    key_words: list          # content words that name the thing asked about
    quals: Qualifiers
    metric: Optional[str]    # "APY" | "APR" | "dividend rate" | "rate" | None
    rate_intent: bool
    rate_implied: bool       # table value asked for without rate words ("what does it pay?", "what does it cost?")
    wants_rate: bool         # the answer must be a percentage rate (rate words, or "what does it pay/earn?")
    wants_list: bool
    superlative: Optional[str]
    wants_url: bool
    hours: Optional[str]     # "lobby" | "drive-up" | "any" | None
    as_of_hint: Optional[str]
    names: list              # capitalised names in the question ('Galena', 'Mullan Avenue')
    wants_number: bool       # "how many", "how much", "what number"
    answer_type: Optional[str]  # "address" | "phone" | "date" | None: the shape the answer must have
    wants_action: bool       # "how do I ...", "where can I ...": a link to act on helps
    change: Optional[str] = None      # "history" | "before" | "when" | "still" | None (see _CHANGE)
    since: Optional[str] = None       # start of the period a change question asks about
    until: Optional[str] = None       # its end ("between May and September 2026")
    family: Optional[str] = None      # catalogue family the question names (catalogue.py)
    product: frozenset = frozenset()  # distinctive product name words ('share', 'bright', 'special')
    kinds: frozenset = frozenset()    # account kinds / variants asked for (identity.KINDS)
    year: Optional[int] = None        # calendar year a statement must be about ('in 2024', 'last year')


def parse(question: str, today: Optional[str] = None, calendar_year: Optional[int] = None) -> Question:
    """`today` is the newest crawl's date; relative dates ('last month', 'two months ago') count back from it.
    `calendar_year` comes from AI question understanding: the year the question names is a point in time
    ('the auto loan rate in 2025'), not a vehicle model year, even next to a vehicle word."""
    q = question.strip()
    low = norm(q)
    plain = re.sub(r"\bcredit unions?\b", " ", q, flags=re.I)   # "the credit union" means Dupaco itself
    plain = PAST_INTRO.sub(" ", plain)   # "after the intro period" names what is *not* asked for
    toks = content_tokens(plain)
    metric = None
    if re.search(r"\bapys?\b|annual percentage yield", low):
        metric = "APY"
    elif re.search(r"\baprs?\b|annual percentage rate", low):
        metric = "APR"
    elif re.search(r"\bdividend", low):
        metric = "dividend rate"
    elif re.search(r"\bmin(imum|\.)?\b.*\bopen|\bopen\b.*\bmin(imum|\.)?\b|how much .*\bto open\b", low):
        metric = "minimum to open"
    # "How many years has a participation dividend been paid?" asks for a count, not a rate.
    rate_intent = bool(_RATE_WORDS.search(low)) and not re.search(r"\binterest(ed)? in\b|\bhow many\b", low)
    # "What does the 10-month special pay?" asks for a table value without saying "rate".
    earns = bool(_EARN_WORDS.search(low) and _RATE_PRODUCTS.search(low))
    rate_implied = not rate_intent and (earns or bool(_COST_WORDS.search(low) and _RATE_PRODUCTS.search(low))
                                        or metric == "minimum to open")
    hours = None
    if (re.search(r"\bhours?\b|what time", low)
            or re.search(r"\b(lobby|lobbies|drive[- ]?(up|thru|through))\b.*\bopen\b|\bopen\b.*\b(lobby|lobbies)\b", low)) and not rate_intent:
        if "lobby" in low or "lobbies" in low:
            hours = "lobby"
        elif re.search(r"drive[- ]?(up|thru|through)", low):
            hours = "drive-up"
        else:
            hours = "any"
    quals = parse_question_qualifiers(q)
    if not (calendar_year and quals.years == (calendar_year, calendar_year)):
        calendar_year = None   # the hint only matters where the rules would read the year as a model year
    change, since, until = _change(q, low, today, calendar_year)
    as_of_hint = date_asked(q, today) if _PAST.search(low) and not change else None
    dateless = re.sub(r"\b(19|20)\d\d\b|\b(january|february|march|april|may|june|july|august|september|october|"
                      r"november|december)\b(\s+\d{1,2}(st|nd|rd|th)\b)?|\btoday\b", " ", plain, flags=re.I)
    key_words = [t for t in content_tokens(dateless) if t not in GENERIC]
    family = question_family(plain)
    if calendar_year:
        quals = replace(quals, years=None)
    kinds = frozenset(v for v in quals.variants if v in KINDS)
    if "traditional" in kinds and not ({"ira", "roth"} & kinds or re.search(r"\bretirement\b", low)):
        kinds -= {"traditional"}   # 'traditional 30 year fixed home loan' is not an IRA question
    return Question(
        year=calendar_year or _year_asked(q, today, quals),
        change=change, since=since, until=until,
        family=family,
        product=product_words(dateless, family),
        kinds=kinds,
        raw=q,
        words=_boost(expand(toks), _names(q)),
        key_words=key_words,
        quals=quals,
        metric=metric,
        rate_intent=rate_intent,
        rate_implied=rate_implied,
        wants_rate=(rate_intent or earns) and metric in (None, "rate"),
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


_NOT_NAMES = {"dupaco", "i", "visa", "apy", "apr", "apys", "aprs", "ira", "iras", "hsa", "cd", "cds", "aba", "faq",
              "lobby", "hours", "drive", "branch", "usa", "us",
              "what", "when", "where", "who", "how", "is", "are", "can", "do", "does", "the", "a", "my", "mobile",
              "shine", "online", "credit", "union", "monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday"} | set(MONTHS)


_REL_YEAR = {"last year": -1, "previous year": -1, "this year": 0, "the year before last": -2}


def _year_asked(q: str, today: Optional[str], quals: Qualifiers) -> Optional[int]:
    """'How many ... in 2024?' -> 2024; 'last year' -> the year before the newest crawl's ('over the
    past year' is a period, not a calendar year). Not a model year, not a year that is part of a full date."""
    low = _TRAILING_YEAR.sub(" ", q.lower())
    for phrase, delta in _REL_YEAR.items():
        if phrase in low and today:
            return int(today[:4]) + delta
    m = re.search(r"\b(?:in|during|for|of)\s+((?:19|20)\d\d)\b(?!\s*model)", strip_dates(q))
    if m and not (quals.years and quals.years[0] == int(m.group(1))):
        return int(m.group(1))
    return None


def _change(q: str, low: str, today: Optional[str], calendar_year: Optional[int] = None) -> tuple:
    """(mode, since, until) for a question about how a fact changed over time."""
    dateless = strip_dates(low)
    undated = dateless if calendar_year else strip_model_years(dateless)
    timed = dateless != low or bool(_TIME.search(re.sub(r"\$\s?\d[\d,]*(\.\d+)?", " ", undated)))
    if _WHEN_CHANGED.search(low) and timed:
        mode = "when"
    elif _BEFORE.search(low) and re.search(r"\b(pay|paid|earn|earned|rate|apy|apr|charge|charged|cost)\b", low):
        mode = "before"
    elif (_CHANGE.search(low) or (timed and _SHIFT.search(low))) and not re.search(
            r"\b(change|update)s? (my|your|the) (address|name|phone|email|contact|password|pin)\b", low):
        mode = "history"
    elif _STILL.search(low):
        mode = "still"
    else:
        return None, None, None
    since = until = None
    m = _SINCE.search(q)
    if m:
        tail = q[m.start():]
        parts = re.split(r"\b(?:and|to|until|through)\b", tail, maxsplit=1)
        since = _period_start(parts[0], today)
        if len(parts) > 1:
            until = date_asked(parts[1], today)
            if since and until and since[:4] != until[:4] and not re.search(r"\b(19|20)\d\d\b", parts[0]):
                since = until[:4] + since[4:]   # 'between May and September 2026': May of 2026
    elif today and _TRAILING_YEAR.search(q):
        since = relative_date("a year ago", today)
    return mode, since, until


def mentions_time(question: str) -> bool:
    """The member's own words ask about time or change ('since May', 'did', 'last month', 'went up',
    'still offered'): only then may a restatement as a change-over-time question replace a current value."""
    low = question.lower()
    undated = strip_model_years(strip_dates(low))
    return bool(strip_dates(low) != low or _TIME.search(undated) or _CHANGE.search(low) or _STILL.search(low)
                or _BEFORE.search(low))


def _period_start(text: str, today: Optional[str]) -> Optional[str]:
    """'since June 2026' -> the end of June: the site as it stood in June is the baseline."""
    return date_asked(text, today)


def _answer_type(low: str) -> Optional[str]:
    if re.search(r"\baddress\b|\blocated\b|\bwhere('s| is| are)\b.*\bbranch\b", low) and "email" not in low and "web" not in low:
        return "address"
    if re.search(r"\b(phone|fax) number\b|\btelephone\b|\bfax\b|\bnumber\b.*\b(call|reach)\b|\bcall\b.*\bnumber\b|\bcall (you|dupaco)\b", low):
        return "phone"
    if re.search(r"\brouting\b|\baba\b", low):
        return "routing"
    if re.search(r"^when\b|\bwhat date\b|\bwhat day\b|\bstill open\b|\bstill (available|operating)\b|\bclos(e|ed|ing) (date|for good)\b", low):
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
        if w[0].isupper() and low not in _NOT_NAMES and (not w.isupper() or len(w) >= 3):
            out.extend(t for t in tokens(low) if t not in STOPWORDS)
    return list(dict.fromkeys(out))


__all__ = ["Question", "parse", "STOPWORDS"]
