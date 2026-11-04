"""Tokenizing and the small banking thesaurus the lexical matcher uses.

The thesaurus maps member wording to website wording ("CD" -> "certificate", "mortgage" ->
"home loan"). It is generic banking vocabulary, not tied to any particular question.
"""
from __future__ import annotations

import re
from functools import lru_cache

import snowballstemmer

from .text import NUMBER_WORDS

STOPWORDS = frozenset("""
a an the of for to in on at by with and or is are was were be been being do does did can could would should
will shall may might must i me my we our you your it its this that these those what which who whom whose when
where why how there here any some all about as from into than then so if not no yes please tell give get
dupaco dupacos dupaco's union cu currently current right now today know like want need much many
just also offer offers have has had am pm one
provide show find see list explain describe looking interested wondering repeat keep same let say
mid early late around approximately roughly exactly over under via
operate operates run runs maintain maintains their them they its his her us ours theirs
publish published post posted total overall currently anymore
use using used go going make made take each every other another more most still
""".split())

# Domain words that say *what kind* of answer is wanted but not *which product*.
GENERIC = frozenset("""
rate rates interest apy apr yield dividend percent percentage annual account accounts much cost fee fees
price pay paid pays charge charges number amount value information info detail details option options
earn earns earning balance balances between tier tiers term terms month months year years minimum standard
highest lowest best top maximum max least cheapest
finance financed financing borrow borrowing borrowed charged carry carries come comes get getting
change changed changes changing happen happened since before after between recent recently increase increased
decrease decreased went gone go goes rise rose risen raise raised drop dropped lower lowered fell move moved
latest previous previously prior ago earlier past history trend still offered available offering up down
higher compare compared comparing different difference differ trends trending
buy buying bought last advertise advertised advertises advertising quote quoted quotes list listed
""".split())

SYNONYMS = {
    "cd": ["certificate"], "cds": ["certificate"],
    "certificate": ["term", "share", "ira"],
    "mortgage": ["home", "loan", "conventional", "fixed"], "mortgages": ["home", "loan", "conventional", "fixed"],
    "auto": ["vehicle", "car", "motorcycle"], "car": ["auto", "vehicle"], "vehicle": ["auto", "car"],
    "truck": ["auto", "vehicle"],
    "heloc": ["home", "equity", "line", "credit"],
    "card": ["visa"], "cards": ["visa"], "visa": ["card"], "credit": ["visa"],
    "checking": ["check"], "savings": ["saving", "share"], "saving": ["savings"], "share": ["savings", "certificate"],
    "ira": ["retirement", "roth", "traditional"], "retirement": ["ira"],
    "boat": ["recreational"], "rv": ["recreational"], "camper": ["recreational"],
    "hours": ["lobby", "drive"], "open": ["close", "hours"], "close": ["closure", "closing"],
    "shut": ["close", "closure"], "closed": ["close"],
    "promotion": ["special", "promo", "offer"], "promo": ["special", "promotion"], "deal": ["special", "offer"],
    "office": ["branch", "location"], "offices": ["branch", "location"],
    "buyer": ["homebuyer"], "buyers": ["homebuyer"], "homebuyer": ["buyer"],
    "address": ["located", "location"], "location": ["branch", "address"], "locations": ["branch", "address"],
    "branches": ["branch"], "branch": ["office", "location"],
    "phone": ["call"], "call": ["phone"],
    "join": ["member", "membership"], "membership": ["member", "join"], "eligible": ["membership", "join"],
    "member": ["membership", "join"], "become": ["join"], "requirement": ["eligible"], "requirements": ["eligible"],
    "qualify": ["eligible"], "eligibility": ["eligible", "join"],
    "student": ["school", "college"], "university": ["college", "school", "student"],
    "employee": ["employees"], "employees": ["employee"],
    "wallet": ["pay"], "mobile": ["app", "digital"], "digital": ["mobile", "online"],
    "load": ["add"], "add": ["load"],
    "routing": ["aba"],
}

_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")


_STEMMER = snowballstemmer.stemmer("english")


@lru_cache(maxsize=None)
def stem(tok: str) -> str:
    """Snowball (Porter2) stem, so 'closing', 'closed' and 'close' meet."""
    return _STEMMER.stemWord(tok) if tok.isalpha() else tok


def tokens(text: str) -> list[str]:
    t = text.lower().replace("’", "'").replace("home$tart", "hometart")
    out = []
    for tok in _TOKEN.findall(t):
        tok = tok.replace("'s", "").replace("'", "")
        if tok in NUMBER_WORDS:
            tok = str(NUMBER_WORDS[tok])
        if tok.replace(".", "").isdigit() and "." in tok:
            continue           # decimal values are matched as numbers, not words
        out.append(stem(tok))
    return out


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in STOPWORDS]


PREFIX = 6


def with_prefixes(toks: list[str]) -> list[str]:
    """Add a truncated form of long words so 'recognition' still finds 'recognized'."""
    return toks + [t[:PREFIX] + "*" for t in toks if len(t) >= PREFIX and t.isalpha()]


def expand(toks: list[str]) -> dict[str, float]:
    """Query tokens with weights: originals 1.0, thesaurus additions 0.5."""
    weights: dict[str, float] = {}
    for t in toks:
        weights[t] = max(weights.get(t, 0.0), 1.0)
    for t in toks:
        for syn in _STEMMED_SYNONYMS.get(t, []):
            weights.setdefault(syn, 0.5)
    for t in list(weights):
        if len(t) >= PREFIX and t.isalpha():
            weights.setdefault(t[:PREFIX] + "*", 0.5 * weights[t])
    return weights


_STEMMED_SYNONYMS = {stem(k): [stem(v) for v in vs] for k, vs in SYNONYMS.items()}
# Tokens are stemmed before lookup, so the word lists hold both forms.
STOPWORDS = STOPWORDS | frozenset(stem(w) for w in STOPWORDS)
GENERIC = GENERIC | frozenset(stem(w) for w in GENERIC)
