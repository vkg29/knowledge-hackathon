"""AI question understanding with Claude (used only when ANTHROPIC_API_KEY is set).

The model restates a member's question in the plain wording the rule parser reads (typos, slang,
abbreviations, indirect phrasing), splits a head-to-head comparison into one question per product,
and says whether a year in the question is a calendar year or a vehicle model year. That is all it
does: it never sees a rate or any other figure from the site, and the ledger and its truth rules
still find every fact and pick every value (`system.LedgerKnowledge`).

Speed and stability: a fast model with thinking off, a short cached prompt, plain JSON validated
here, and a hard deadline per question. A request still unanswered after HEDGE_AFTER_S is sent a
second time and the first reply wins (a hedged request: it cuts the slow tail that decides p95
latency). Any error, timeout or invalid reply falls back to the rules on the original question.
Every result (including a fallback) is cached per question for the life of the process, so asking
again gives the same answer. The prompt lists the site's own product names,
built from the ledger after each ingest, so the model can map slang onto them.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, Optional

from .hooks import Hooks, Understanding
from .text import NUMBER_WORDS

MODEL = "claude-haiku-5-5"
DEADLINE_S = 1.9           # per question, all attempts included: p95 latency of 2 s or less earns full speed marks
HEDGE_AFTER_S = 0.9        # no reply yet: send one identical request alongside and take whichever answers first
WARMUP_TIMEOUT_S = 15.0    # the check call made while loading (untimed)
MAX_FAILURES = 5           # consecutive failed calls before the AI step switches itself off for the run
_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")
# what is left of a table label once tier words are removed; nothing left means the label names a tier, not a product
_TIER_WORDS = re.compile(r"[$\d,.+%()\u2013\u2014-]|\b(months?|mo|model|years?|and|older|or|less|more|up|to|over|"
                         r"standard|regular|variable|interest|rate)\b", re.I)
_KIND_PREFIX = re.compile(r"^(roth|traditional)\s*:\s*", re.I)
_AMOUNT = re.compile(r"\$\s?[\d,]+(?:\.\d+)?\+?(?:\s*(?:-|\u2013|to)\s*\$?\s?[\d,]+(?:\.\d+)?)?(?:\s*or (?:less|more))?", re.I)
_ORDINALS = {"first": 1, "second": 2, "third": 3}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MAX_QUESTIONS = 3
_MAX_CHARS = 300

# The instructions the model gets; "{catalogue}" is replaced by the site's product names after each ingest.
_RULES = (Path(__file__).with_name("understand_prompt.txt")).read_text(encoding="utf-8")


def from_environment(log: Callable[[str], None] = print) -> Optional["ClaudeHooks"]:
    """The AI steps when ANTHROPIC_API_KEY is set and the SDK is installed, else None (rules only)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("AI steps: OFF (no key found; set ANTHROPIC_API_KEY for best results). Answering with rules only.")
        return None
    try:
        import anthropic
    except ImportError:
        log("AI steps: OFF (the anthropic package is not installed; pip install -r requirements.txt). Answering with rules only.")
        return None
    hooks = ClaudeHooks(anthropic.Anthropic(max_retries=0), log=log)
    log(f"AI steps: ON (question understanding with Claude {MODEL} via the Anthropic API; the ledger's rules pick every value).")
    return hooks


class ClaudeHooks(Hooks):
    def __init__(self, client, model: str = MODEL, deadline: float = DEADLINE_S, hedge_after: float = HEDGE_AFTER_S,
                 log: Callable[[str], None] = print) -> None:
        self.client = client
        self.model = model
        self.deadline = deadline
        self.hedge_after = hedge_after
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="claude")
        self.log = log
        self.name = f"Claude {model} via the Anthropic API"
        self.enabled = True
        self.system: Optional[str] = None
        self.failures = 0
        self.calls = 0
        self.usage: Counter = Counter()   # tokens billed, for the cost estimate in NOTES.md
        self.cache: dict = {}
        self.trace = os.environ.get("TRUTHLEDGER_AI_TRACE")   # optional JSONL log of every question's AI step

    # ---- loading -----------------------------------------------------------------------
    def prepare(self, ledger) -> None:
        """Build the prompt from the site's product names, then check the API once (loading is untimed,
        and the check also opens the connection so the first timed question does not pay for it)."""
        self.system = _RULES.replace("{catalogue}", catalogue(ledger))
        self.cache.clear()
        if not self.enabled:
            return
        for _ in range(2):
            try:
                self._call("What is the 12-month certificate APY?", WARMUP_TIMEOUT_S)
                return
            except Exception as e:   # any SDK or network error means rules only
                err = e
        self.enabled = False
        self.log(f"AI steps: OFF (Anthropic API check failed: {type(err).__name__}: {str(err)[:160]}). Answering with rules only.")

    # ---- answering ---------------------------------------------------------------------
    def understand(self, question: str) -> Optional[Understanding]:
        if not self.enabled or self.system is None:
            return None
        if question not in self.cache:
            self.cache[question] = self._understand(question)
        return self.cache[question]

    def _understand(self, question: str) -> Optional[Understanding]:
        start = time.monotonic()
        try:
            reply = self._race(question)
        except Exception as e:   # timeouts, rate limits, overload, network: fall back to rules
            self.failures += 1
            self._log_trace(question, start, error=f"{type(e).__name__}: {e}"[:200])
            if self.failures >= MAX_FAILURES:
                self.enabled = False
                self.log(f"AI steps: OFF for the rest of this run after {self.failures} failed calls "
                         f"(last: {type(e).__name__}). Answering with rules only.")
            return None
        self.failures = 0
        understood = parse_reply(reply, question)
        self._log_trace(question, start, reply=reply, understood=understood)
        return understood

    def _log_trace(self, question: str, start: float, **fields) -> None:
        if not self.trace:
            return
        u = fields.pop("understood", None)
        rec = {"question": question, "seconds": round(time.monotonic() - start, 3), **fields,
               "understood": None if u is None else {"questions": list(u.questions), "compare": u.compare,
                                                     "calendar_year": u.calendar_year, "model_year": u.model_year},
               "calls_so_far": self.calls, "tokens_so_far": dict(self.usage)}
        with open(self.trace, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def _race(self, question: str) -> str:
        """The first good reply among at most two identical requests, within the deadline."""
        start = time.monotonic()
        pending = {self.pool.submit(self._call, question, self.deadline)}
        hedged, error = False, None
        while pending:
            left = self.deadline - (time.monotonic() - start)
            if left <= 0:
                break
            wait_for = min(left, self.hedge_after - (time.monotonic() - start)) if not hedged else left
            done, pending = wait(pending, timeout=max(0.0, wait_for), return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    return f.result()
                except Exception as e:   # this attempt failed; the other one (or a hedge) may still answer
                    error = e
            if not hedged and time.monotonic() - start < self.deadline - 0.2:
                hedged = True
                pending.add(self.pool.submit(self._call, question, self.deadline - (time.monotonic() - start)))
        raise error or TimeoutError(f"no reply within {self.deadline} s")

    def _call(self, question: str, timeout: float) -> str:
        self.calls += 1
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"member: {question.strip()[:_MAX_CHARS * 2]}"}],
            timeout=timeout,
        )
        u = msg.usage
        self.usage.update(input=u.input_tokens, output=u.output_tokens, cache_read=u.cache_read_input_tokens or 0,
                          cache_write=u.cache_creation_input_tokens or 0)
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def parse_reply(reply: str, question: str) -> Optional[Understanding]:
    """Validate the model's JSON; anything malformed or out of bounds means 'use the rules on the original'."""
    text = reply.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    qs = data.get("questions")
    if not isinstance(qs, list) or not qs:
        return None
    qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()]
    if not qs or len(qs) > _MAX_QUESTIONS or any(len(q) > _MAX_CHARS for q in qs):
        return None
    compare = data.get("compare") is True and len(qs) >= 2
    if not compare:
        qs = qs[:1]
    asked_years = {int(y) for y in _YEAR.findall(question)}

    if not _numbers_kept(question, qs):
        return None

    def year(key: str) -> Optional[int]:
        v = data.get(key)
        # only a year the member actually wrote: the model must not invent one
        return v if isinstance(v, int) and not isinstance(v, bool) and v in asked_years else None

    cal, model = year("calendar_year"), year("model_year")
    if cal is not None and cal == model:
        cal = model = None
    return Understanding(tuple(qs), compare, cal, model)


def _values(text: str) -> set:
    out = set()
    for m in _NUM.finditer(text):
        v = float(m.group(0).replace(",", ""))
        out.add(v)
        if text[m.end(): m.end() + 1].lower() == "k":
            out.add(v * 1000)
    return out


def _numbers_kept(question: str, restated: list) -> bool:
    """Every number in the restatement must be one the member wrote, or that number in months or years when the
    member gave a term, or in dollars ($15k, 15 thousand); a model that turns '30-year' into '20-year' is caught
    here and the rules answer instead."""
    low = question.lower()
    base = _values(low)
    base |= {float(n) for w, n in {**NUMBER_WORDS, **_ORDINALS}.items() if re.search(rf"\b{w}\b", low)}
    if re.search(r"\b(a|an)\s+(years?|yrs?|months?|mos?)\b", low):
        base |= {1.0}
    years = re.search(r"\b(years?|yrs?)\b|\d\s?y\b", low)
    if years:
        base |= {12.0}
    if re.search(r"\b(a |one )?(year|yr) and a half\b", low):
        base |= {18.0}
    allowed = set(base)
    if years:
        allowed |= {v * 12 for v in base}
    if re.search(r"\b(months?|mos?|mths?)\b|\d\s?m\b", low):
        allowed |= {v / 12 for v in base}
    if re.search(r"\b(thousand|grand)\b", low):
        allowed |= {v * 1000 for v in base}
    return all(v in allowed for q in restated for v in _values(q.lower()))


def catalogue(ledger) -> str:
    """One line per product family: the product names official pages print, the terms and how rates are
    tiered. Built from the ledger's fact identities, so it describes whatever site was loaded."""
    tl = ledger.timeline()
    names: dict = defaultdict(dict)
    terms: dict = defaultdict(set)
    tiers: dict = defaultdict(set)
    for k in range(len(ledger.snapshots)):
        for key, w in tl.winners(k).items():
            if key.family is None or w.authority < 5:
                continue
            label = _KIND_PREFIX.sub("", re.sub(r"\s+", " ", w.label)).strip()
            label = _AMOUNT.sub("", label).strip(" -:")
            name = label if _TIER_WORDS.sub("", label).strip(" :") else re.sub(r"\s+", " ", w.caption).strip()
            if name and not _YEAR.search(name):
                names[key.family].setdefault(name, None)
            terms[key.family] |= set(key.terms)
            if w.quals.amount:
                tiers[key.family].add("balance or loan amount")
            if w.quals.years:
                tiers[key.family].add("vehicle model year")
    lines = []
    for fam in sorted(names):
        bits = [f"- {fam}: " + "; ".join(sorted(names[fam])[:30])]
        if terms[fam]:
            bits.append("terms " + ", ".join(str(t) for t in sorted(terms[fam])) + " months")
        if tiers[fam]:
            bits.append("tiered by " + " and ".join(sorted(tiers[fam])))
        lines.append(" | ".join(bits))
    return "\n".join(lines)
