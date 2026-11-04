"""A generic credit-union product catalogue: the families a rate, fee or term can belong to.

Nothing here is specific to one credit union. The families are the products almost every US credit
union sells (savings, money market, certificates, checking, mortgages, auto loans, cards, ...). Both
ledger rows and questions are mapped onto the same families, so "CD", "share certificate" and
"term-share certificate" all land on `certificate`, and "home loan" and "30-year fixed" on
`mortgage`. A family is a coarse grouping; the product inside it (Share Savings vs Bright Savings,
a special vs a regular certificate) is told apart by its distinctive name words.
"""
from __future__ import annotations

import re
from typing import Optional

from .vocab import STOPWORDS, stem, tokens

# (family, pattern) in priority order: the first pattern that matches names the family, so the
# most specific products come first ("home equity line" before "home equity", "student loan"
# before a refinance, "recreational vehicle" before "vehicle").
_FAMILY_RULES: list[tuple[str, str]] = [
    ("credit card", r"\b(visa|mastercard|credit cards?)\b"),
    ("heloc", r"\bhelocs?\b|\bhome equity lines?\b|\bequity lines? of credit\b"),
    ("home equity loan", r"\bhome equity\b"),
    ("home improvement loan", r"\bhome improvement\b"),
    ("student loan", r"\bstudent loans?\b"),
    ("mortgage", r"\bmortgages?\b|\bhome loans?\b|\bconventional\b|\b\d{1,2}[- ]?(year|yr)s?[- ]fixed\b|\bfixed[- ]rate home\b"
                 r"|\b\d+/\d+ arm\b|\barms?\b|\badjustable[- ]rate\b|\bhome buying\b|\bhomebuy\w*\b"),
    ("powersport loan", r"\b(atvs?|utvs?|snowmobiles?|jet skis?|powersports?)\b"),
    ("recreational loan", r"\b(boats?|rvs?|campers?|recreational)\b"),
    ("auto loan", r"\b(auto|autos|motorcycles?|vehicles?|cars?|trucks?)\b"),
    ("personal loan", r"\bpersonal loans?\b|\bcredit coach\b|\bsignature loans?\b"),
    ("certificate", r"\bcertificates?\b|\bcds?\b|\bterm[- ]shares?\b"),
    ("money market", r"\bmoney markets?\b"),
    ("health savings", r"\bhealth savings\b|\bhsas?\b"),
    ("checking", r"\bchecking\b"),
    ("savings", r"\bsavings\b|\bclub\b"),
]
_COMPILED = [(f, re.compile(p, re.I)) for f, p in _FAMILY_RULES]

DEPOSITS = frozenset({"certificate", "money market", "health savings", "checking", "savings"})
# A question naming a broader group matches every family in it ("home equity" covers the line of
# credit and the fixed loan).
_GROUPS = {"home equity": frozenset({"heloc", "home equity loan"})}


def family_of(text: str) -> Optional[str]:
    """The catalogue family a piece of text names, or None."""
    t = (text or "").replace("’", "'")
    for fam, pat in _COMPILED:
        if pat.search(t):
            return fam
    return None


def question_family(text: str) -> Optional[str]:
    """Like `family_of`, but "home equity" without "line" or "loan" is the whole home-equity group."""
    fam = family_of(text)
    if fam == "home equity loan" and not re.search(r"\bhome equity loans?\b", text, re.I):
        return "home equity"
    return fam


def family_matches(asked: Optional[str], fam: Optional[str]) -> bool:
    if asked is None:
        return True
    return asked == fam or fam in _GROUPS.get(asked, ())


# Words that never tell one product from another inside a family: the family's own words, metric
# and tier words, and the filler every rate table uses ("Regular Certificates", "Standard",
# "Purchase: 30-Year Fixed Conventional"). What is left is the product's name: "share",
# "bright", "special", "platinum", "rewards", "investor choice", "1st", "student".
_FAMILY_VOCAB = {
    "credit card": "visa mastercard credit card cards",
    "heloc": "heloc helocs home equity line lines of credit",
    "home equity loan": "home equity loan loans",
    "home improvement loan": "home improvement loan loans",
    "student loan": "student students loan loans",
    "mortgage": "mortgage mortgages home loan loans conventional fixed arm arms adjustable buying homebuying homebuyer",
    "powersport loan": "atv atvs utv utvs snowmobile snowmobiles jet ski skis powersport powersports loan loans",
    "recreational loan": "boat boats rv rvs camper campers recreational vehicle vehicles loan loans",
    "auto loan": "auto autos motorcycle motorcycles vehicle vehicles car cars truck trucks loan loans",
    "personal loan": "personal loan loans",
    "certificate": "certificate certificates cd cds term share shares",
    "money market": "money market markets",
    "health savings": "health savings saving hsa hsas",
    "checking": "checking",
    "savings": "savings saving",
}
_VOCAB = {f: frozenset(stem(w) for w in ws.split()) for f, ws in _FAMILY_VOCAB.items()}
_ALL_VOCAB = frozenset().union(*_VOCAB.values())
_FILLER = """account accounts rate rates regular standard purchase program programs product products option options type types
plan plans interest dividend dividends apy apr annual percentage yield as low tier tiers balance balances minimum min
open earn month months year years yr yrs mo mos model older and or less more up over under new used qualified borrowers
borrower online only fixed option intro introductory jumbo ira iras roth traditional retirement business commercial
refinance refinancing refi transfer details detail payment example points table schedule competitive loan loans
pay pays paid paying earn earns earned charge charges charged cost costs get gets got today now current currently still offered
available happen happened change changed changes increase increased decrease decreased raise raised drop dropped since before
after between recent recently last ago earlier past previous prior latest new newest old down up""".split()
GENERIC_PRODUCT = frozenset(stem(w) for w in _FILLER) | STOPWORDS


def product_words(text: str, family: Optional[str] = None) -> frozenset:
    """The distinctive name words of a product of `family` in `text` (stemmed), without numbers. The
    family's own words are dropped ('Share Savings' is the savings product 'share'; a 'Term-Share
    Certificate' is just a certificate); with no family, every family's words are. An ordinal is a
    name word, written one way whichever way the text spells it ('Second Chance' is '2nd Chance')."""
    drop = GENERIC_PRODUCT | _VOCAB.get(family, _ALL_VOCAB)
    words = (_ORDINAL_WORDS.get(t, t) for t in tokens(text))
    return frozenset(t for t in words if _ORDINAL.fullmatch(t) or (t.isalpha() and len(t) > 1 and t not in drop))


_ORDINAL_WORDS = dict(zip((stem(w) for w in "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split()),
                          "1st 2nd 3rd 4th 5th 6th 7th 8th 9th 10th 11th 12th".split()))
_ORDINAL = re.compile(r"\d+(st|nd|rd|th)")
