"""AI question understanding with a fake Anthropic client: no network, no key.

Run from the repository root:  python -m unittest discover -s solution/tests -v
"""
import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "solution"))
os.environ.pop("ANTHROPIC_API_KEY", None)   # unit tests never call the real API

import contract  # noqa: E402
from contract import Page, Snapshot  # noqa: E402
from truthledger import ai  # noqa: E402
from truthledger.hooks import Hooks  # noqa: E402
from truthledger.system import LedgerKnowledge, grounded  # noqa: E402

from solution import build  # noqa: E402

SITE = "https://cu.example"

RATES = """# Rates

Certificates

Regular Certificates

| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Open |
| --- | --- | --- | --- |
| 12 Month | 2.90% | 2.95%1 | $1,000.00 |
| 6 Month | 3.37% | 3.40%1 | $1,000.00 |

Rates are effective as of 06/02/2026 and are subject to change without notice.

Auto & Motorcycle Loans

| Loan Program | Term | APR |
| --- | --- | --- |
| 2024-27 Model Year | 60 Months | 5.39% |
| 2021-23 Model Year | 60 Months | 5.64% |

Rates are effective as of 09/01/2026.
"""

BOATS = """# Boat loans

Enjoy flexible terms up to 84 months on a new boat, so fun stays fun.
"""

MONEY_MARKET = """# Money Market

Money Market Accounts

| Balance | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| $0 - $9,999.99 | 0.50% | 0.50% |
| $10,000 and up | 1.00% | 1.01% |
"""

WARMUP = "What is the 12-month certificate APY?"


def reply(*questions, compare=False, calendar_year=None, model_year=None):
    return json.dumps({"questions": list(questions), "compare": compare, "calendar_year": calendar_year,
                       "model_year": model_year})


class FakeClient:
    """Stands in for anthropic.Anthropic(): `replies` maps a member question to the model's JSON reply
    (or to an exception to raise); `delays` makes a question's first call slow."""

    def __init__(self, replies, delays=None):
        self.replies = {WARMUP: reply(WARMUP), **replies}
        self.delays = dict(delays or {})
        self.seen = []
        self.lock = threading.Lock()
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        question = kw["messages"][0]["content"].removeprefix("member: ")
        with self.lock:
            self.seen.append(question)
            delay = self.delays.pop(question, 0)
        time.sleep(delay)
        out = self.replies[question]
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=out)],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=20, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))


def site(*extra):
    pages = {"/rates": RATES}
    for i, md in enumerate(extra):
        pages[f"/page{i}"] = md
    return Snapshot("01_2026-09-28_only", "2026-09-28",
                    [Page(url=SITE + path, title=path.strip("/"), markdown=md, fetched_at="2026-09-28T00:00:00Z")
                     for path, md in pages.items()])


def with_ai(replies, *extra, **kw):
    client = FakeClient(replies, kw.pop("delays", None))
    hooks = ai.ClaudeHooks(client, log=lambda msg: None, **kw)
    ks = LedgerKnowledge(contract, hooks)
    ks.ingest(site(*extra))
    return ks, client, hooks


def rules_only(*extra):
    ks = LedgerKnowledge(contract, Hooks())
    ks.ingest(site(*extra))
    return ks


class KeyDetection(unittest.TestCase):
    def test_no_key_prints_off_and_answers_with_rules(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ks = build()
        self.assertIn("AI steps: OFF (no key found", out.getvalue())
        self.assertEqual(type(ks.hooks), Hooks)

    def test_failed_api_check_switches_ai_off_while_loading(self):
        client = FakeClient({WARMUP: ConnectionError("no network")})
        logged = []
        hooks = ai.ClaudeHooks(client, log=logged.append)
        ks = LedgerKnowledge(contract, hooks)
        ks.ingest(site())
        self.assertFalse(hooks.enabled)
        self.assertIn("AI steps: OFF", logged[-1])
        self.assertIn("2.95%", ks.answer("What is the APY on a 12-month certificate?").text)


class Restatement(unittest.TestCase):
    def test_slang_the_rules_cannot_read_is_answered_from_the_restatement(self):
        q = "whats the apy on ur 1 yr cd rn"
        self.assertFalse(rules_only().answer(q).confident)
        ks, _, _ = with_ai({q: reply("What is the APY on the 12-month certificate?")})
        ans = ks.answer(q)
        self.assertTrue(ans.confident)
        self.assertIn("2.95%", ans.text)
        self.assertNotIn("3.40%", ans.text)
        self.assertTrue(ans.sources)

    def test_a_question_the_rules_already_read_keeps_their_answer(self):
        q = "What is the APY on a 12-month certificate?"
        ks, _, _ = with_ai({q: reply("What is the APY on the 6-month certificate?")})
        ans = ks.answer(q)
        self.assertIn("2.95%", ans.text)
        self.assertNotIn("3.40%", ans.text)

    def test_restatement_that_changes_the_product_family_is_ignored(self):
        q = "whats the 1 yr cd apy"
        ks, _, _ = with_ai({q: reply("What is the APR on a 60-month auto loan for a 2025 model-year vehicle?")})
        self.assertNotIn("5.39%", ks.answer(q).text)

    def test_a_refusal_is_not_overruled_by_a_loose_prose_match(self):
        q = "do y'all have a 7 year cd?"
        ks, _, _ = with_ai({q: reply("Do you offer an 84-month certificate?")}, BOATS)
        ans = ks.answer(q)
        self.assertFalse(ans.confident)
        self.assertNotIn("84", ans.text)

    def test_restatement_naming_no_product_cannot_overrule_a_refusal(self):
        q = "helok apr?"
        ks, _, _ = with_ai({q: reply("What is the APR?")})
        self.assertFalse(ks.answer(q).confident)

    def test_a_confident_answer_is_not_replaced_by_a_refusal(self):
        q = "tell me about boat loans"
        ks, _, _ = with_ai({q: reply("What is the APR on boat loans?")}, BOATS)
        ans = ks.answer(q)
        self.assertTrue(ans.confident)
        self.assertEqual(ans.text, rules_only(BOATS).answer(q).text)

    def test_restatement_that_drops_the_members_balance_is_ignored(self):
        q = "what do money markit pay on $15,000"
        ks, _, _ = with_ai({q: reply("What does the Money Market account pay?")}, MONEY_MARKET)
        self.assertNotIn("0.50%", ks.answer(q).text)
        ks, _, _ = with_ai({q: reply("What does the Money Market account pay on a $15,000 balance?")}, MONEY_MARKET)
        ans = ks.answer(q)
        self.assertIn("1.01%", ans.text)
        self.assertNotIn("0.50%", ans.text)

    def test_restatement_that_drops_the_members_model_year_is_ignored(self):
        q = "2022 model year ride apr"
        ks, _, _ = with_ai({q: reply("What is the APR on an auto loan?")})
        self.assertNotIn("5.39%", ks.answer(q).text)
        ks, _, _ = with_ai({q: reply("What is the APR on an auto loan for a 2022 model-year vehicle?", model_year=2022)})
        ans = ks.answer(q)
        self.assertIn("5.64%", ans.text)
        self.assertNotIn("5.39%", ans.text)

    def test_restatement_that_narrows_to_a_term_the_member_did_not_write_is_ignored(self):
        q = "what r ur certifcate rates"
        ks, _, _ = with_ai({q: reply("What is the rate on the 12-month certificate?")})
        self.assertFalse(ks.answer(q).confident)
        ks, _, _ = with_ai({q: reply("What are the certificate rates?")})
        ans = ks.answer(q)
        self.assertIn("2.95%", ans.text)
        self.assertIn("3.40%", ans.text)


class CalendarYear(unittest.TestCase):
    Q = "What was the auto loan rate in 2025?"

    def test_rules_alone_read_the_year_as_a_model_year(self):
        self.assertIn("5.39%", rules_only().answer(self.Q).text)

    def test_calendar_year_before_the_first_crawl_is_refused_without_figures(self):
        ks, _, _ = with_ai({self.Q: reply("What was the auto loan APR in 2025?", calendar_year=2025)})
        ans = ks.answer(self.Q)
        self.assertFalse(ans.confident)
        self.assertIn("2025", ans.text)
        self.assertNotRegex(ans.text.replace("2025", ""), r"\d")

    def test_model_year_still_selects_the_tier(self):
        q = "60 month loan on a 2025 truck?"
        ks, _, _ = with_ai({q: reply("What is the APR on a 60-month auto loan for a 2025 model-year vehicle?",
                                     model_year=2025)})
        ans = ks.answer(q)
        self.assertIn("5.39%", ans.text)
        self.assertNotIn("5.64%", ans.text)


class Comparison(unittest.TestCase):
    def test_each_side_answered_by_the_rules_and_the_higher_one_named(self):
        q = "6 month vs 12 month cd - which pays more?"
        ks, _, _ = with_ai({q: reply("What is the APY on the 6-month certificate?", "What is the APY on the 12-month certificate?",
                                     compare=True)})
        ans = ks.answer(q)
        self.assertIn("3.40%", ans.text)
        self.assertIn("2.95%", ans.text)
        self.assertTrue(ans.text.endswith("Regular Certificates 6 Month has the higher APY."), ans.text)

    def test_one_side_not_listed_says_so(self):
        q = "6 month vs 13 month cd?"
        ks, _, _ = with_ai({q: reply("What is the APY on the 6-month certificate?", "What is the APY on the 13-month certificate?",
                                     compare=True)})
        ans = ks.answer(q)
        self.assertIn("3.40%", ans.text)
        self.assertIn("isn't listed", ans.text)

    def test_final_check_rejects_a_number_no_rule_answer_stated(self):
        self.assertTrue(grounded("A: 3.40% APY. B: 2.95% APY.", ["A: 3.40% APY.", "B: 2.95% APY."]))
        self.assertFalse(grounded("A: 3.40% APY, 0.45 points more than B.", ["A: 3.40% APY.", "B: 2.95% APY."]))


class Robustness(unittest.TestCase):
    def test_api_errors_fall_back_to_rules_and_switch_off_after_repeated_failures(self):
        qs = [f"cd question number {i}" for i in range(ai.MAX_FAILURES + 1)]
        ks, client, hooks = with_ai({q: TimeoutError("slow") for q in qs})
        for q in qs[:ai.MAX_FAILURES]:
            ks.answer(q)
        self.assertFalse(hooks.enabled)
        calls = len(client.seen)
        ks.answer(qs[-1])
        self.assertEqual(len(client.seen), calls)   # no more calls once switched off

    def test_slow_reply_is_hedged_and_the_first_good_reply_wins(self):
        q = "whats the apy on ur 1 yr cd rn"
        ks, client, _ = with_ai({q: reply("What is the APY on the 12-month certificate?")}, delays={q: 1.0},
                                deadline=0.8, hedge_after=0.1)
        start = time.monotonic()
        ans = ks.answer(q)
        self.assertLess(time.monotonic() - start, 0.8)
        self.assertIn("2.95%", ans.text)
        self.assertEqual(client.seen.count(q), 2)

    def test_no_reply_within_the_deadline_uses_the_rules(self):
        q = "whats the apy on ur 1 yr cd rn"
        client = FakeClient({q: reply("What is the APY on the 12-month certificate?")})
        hooks = ai.ClaudeHooks(client, log=lambda m: None, deadline=0.3, hedge_after=0.1)
        ks = LedgerKnowledge(contract, hooks)
        ks.ingest(site())
        original = client.create

        def slow(**kw):   # every attempt is slow
            time.sleep(1.0)
            return original(**kw)
        client.messages.create = slow
        start = time.monotonic()
        ans = ks.answer(q)
        self.assertLess(time.monotonic() - start, 0.6)
        self.assertFalse(ans.confident)

    def test_ai_call_runs_while_the_rules_answer(self):
        q = "whats the apy on ur 1 yr cd rn"
        ks, _, _ = with_ai({q: reply("What is the APY on the 12-month certificate?")}, delays={q: 0.5})
        rules = ks._answerer.answer

        def slow_rules(*args, **kw):
            time.sleep(0.5)
            return rules(*args, **kw)
        ks._answerer.answer = slow_rules
        start = time.monotonic()
        ans = ks.answer(q)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertIn("2.95%", ans.text)

    def test_same_question_is_understood_once_and_answered_identically(self):
        q = "whats the apy on ur 1 yr cd rn"
        ks, client, _ = with_ai({q: reply("What is the APY on the 12-month certificate?")})
        first = ks.answer(q)
        again = ks.answer(q, as_of="2026-09-28")
        self.assertEqual(client.seen.count(q), 1)
        self.assertEqual(first.text, again.text)


class ReplyValidation(unittest.TestCase):
    def test_malformed_or_out_of_bounds_replies_are_rejected(self):
        self.assertIsNone(ai.parse_reply("not json", "q"))
        self.assertIsNone(ai.parse_reply('{"questions": []}', "q"))
        self.assertIsNone(ai.parse_reply('{"questions": "one"}', "q"))
        self.assertIsNone(ai.parse_reply(reply("x" * 400), "q"))

    def test_json_inside_a_code_fence_is_read(self):
        u = ai.parse_reply("```json\n" + reply("What is the 12-month certificate APY?") + "\n```", "1 yr cd apy")
        self.assertEqual(u.questions, ("What is the 12-month certificate APY?",))

    def test_changed_numbers_are_rejected_and_conversions_kept(self):
        q = "What was the 30-year fixed mortgage rate for loans of $100,000 or less before its latest change?"
        self.assertIsNone(ai.parse_reply(reply("What did the 20-year fixed mortgage for loans of $100,000 or less pay before "
                                               "its most recent change?"), q))
        self.assertIsNotNone(ai.parse_reply(reply("What is the APY on the 12-month certificate?"), "1 yr cd apy"))
        self.assertIsNotNone(ai.parse_reply(reply("What does Share Savings pay on a $15,000 balance?"), "15k in savings"))
        self.assertIsNotNone(ai.parse_reply(reply("What is the APY on an 18-month Roth IRA certificate?"),
                                            "Roth IRA CD for a year and a half"))
        self.assertIsNotNone(ai.parse_reply(reply("What is the APY on the 12-month certificate?"), "a year cd apy"))
        self.assertIsNotNone(ai.parse_reply(reply("What is the APY on the 1-year certificate?"), "12 month cd apy"))
        self.assertIsNotNone(ai.parse_reply(reply("What does Share Savings pay on $15,000?"), "15 thousand in savings"))

    def test_numbers_the_member_never_wrote_are_rejected(self):
        self.assertIsNone(ai.parse_reply(reply("What is the rate on the 12-month certificate?"), "what r ur certifcate rates"))
        self.assertIsNone(ai.parse_reply(reply("What does Share Savings pay on a $1,000 balance?"), "how much does savings pay"))
        self.assertIsNone(ai.parse_reply(reply("What does the 6-month certificate pay on $6,000?"), "6 month cd"))
        self.assertIsNone(ai.parse_reply(reply("What is the APY on the 72-month certificate?"), "6 month cd"))

    def test_years_the_member_did_not_write_are_dropped(self):
        u = ai.parse_reply(reply("What is the auto loan APR?", calendar_year=2024), "auto loan apr")
        self.assertIsNone(u.calendar_year)
        u = ai.parse_reply(reply("What was the auto loan APR in 2024?", calendar_year=2024), "auto loan apr in 2024")
        self.assertEqual(u.calendar_year, 2024)

    def test_a_single_question_is_never_a_comparison(self):
        u = ai.parse_reply(reply("What is the 12-month certificate APY?", compare=True), "12 month cd")
        self.assertFalse(u.compare)


class Catalogue(unittest.TestCase):
    def test_prompt_lists_the_sites_own_product_names(self):
        ks, _, hooks = with_ai({})
        self.assertIn("Regular Certificates", hooks.system)
        self.assertIn("Auto & Motorcycle Loans", hooks.system)
        self.assertIn("vehicle model year", hooks.system)


if __name__ == "__main__":
    unittest.main()
