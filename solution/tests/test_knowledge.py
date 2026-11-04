"""Behaviour tests on small synthetic websites (no real snapshot text).

Run from the repository root:  python -m unittest discover -s solution/tests -v
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "solution"))

from contract import Page, Snapshot  # noqa: E402
from truthledger.qualifiers import parse_question_qualifiers, parse_row_qualifiers  # noqa: E402
from truthledger.query import parse  # noqa: E402
from truthledger.text import effective_date, find_dates  # noqa: E402

from solution import build  # noqa: E402

SITE = "https://cu.example"

CERT_TABLE = """# Rates

Certificates

Regular Certificates

| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Open |
| --- | --- | --- | --- |
| 12 Month | {r12}% | {a12}%1 | $1,000.00 |
| 6 Month | 3.37% | 3.40%1 | $1,000.00 |

Rates are effective as of {eff} and are subject to change without notice.
"""

SAVINGS_TABLE = """# Savings

Share Savings

| Account Type | Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- | --- |
| Share Savings | $25,000+ | 0.20% | 0.20% |
| Share Savings | $10,000 \u2013 $24,999.99 | 0.15% | 0.15% |
| Share Savings | $0 \u2013 $9,999.99 | 0.05% | 0.05% |

Rates are effective as of 06/02/2026 and are subject to change without notice.
"""

AUTO_TABLE = """# Auto Loans

Auto Loans

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| {new} Model Year | 60 Months | {rate_new}% APR1 |
| 2019-22 Model Year | 60 Months | 6.10% APR1 |

Rates are effective as of {eff}. All loans are subject to approval.
"""

CONTACT = """# Contact Us

**Phone:** 800-555-0100

**Routing Number:** 123456789

## Find a Location

Main Street Branch

100 Main St., Springfield, IA, 52001
"""

NEWS = """2 min read

{date}

# {title}

It has over 600 employees and {n} branch office locations.
"""

PROMO = """# Summer Savings

Summer special: 6-month certificate APY 4.50% (offer ended 2026-05-31)
"""

BLOG_TABLE = """# Saving tips

Certificates

| Certificate | APY |
| --- | --- |
| 12 Month Term Certificate | 2.90% |
"""

STUDENT_TABLE = """# Student Certificates

Student Certificates

| Certificate | APY |
| --- | --- |
| 12 Month Student Certificate | 4.00% |
"""

TERMS_TABLE = """# Rates

Share Certificates

| Certificate | Annual Percentage Yield (APY) |
| --- | --- |
| 6 Month | {apy}% |
| 9 Month | {apy}% |

Rates are effective as of {eff}.
"""


def snap(sid, date, pages):
    return Snapshot(sid, date, [Page(url=SITE + path, title=path.strip("/") or "home", markdown=md,
                                     fetched_at=date + "T00:00:00Z") for path, md in pages.items()])


def system(*snapshots):
    ks = build()
    for s in snapshots:
        ks.ingest(s)
    return ks


def numbers(text):
    return re.findall(r"\d+(?:\.\d+)?%", text)


class TruthRules(unittest.TestCase):
    def setUp(self):
        old = {
            "/rates": CERT_TABLE.format(r12="2.88", a12="2.90", eff="03/03/2026"),
            "/auto-loans": AUTO_TABLE.format(new="2023-26", rate_new="5.19", eff="05/01/2026"),
            "/calculators/vehicle": AUTO_TABLE.format(new="2023-26", rate_new="5.34", eff="1/1/2026"),
            "/promo": PROMO,
            "/contact": CONTACT,
        }
        new = dict(old)
        new["/rates"] = CERT_TABLE.format(r12="3.37", a12="3.40", eff="06/02/2026")
        new["/auto-loans"] = AUTO_TABLE.format(new="2024-27", rate_new="5.39", eff="08/31/2026")
        self.ks = system(snap("01_2026-05-12_base", "2026-05-12", old), snap("02_2026-09-28_latest", "2026-09-28", new))

    def test_newest_official_value_wins(self):
        ans = self.ks.answer("What is the APY on a 12-month certificate?")
        self.assertIn("3.40%", ans.text)
        self.assertNotIn("2.90%", ans.text)
        self.assertTrue(ans.confident)
        self.assertEqual(ans.sources[0].url, SITE + "/rates")

    def test_official_rate_sheet_beats_calculator(self):
        ans = self.ks.answer("APR for a 60-month auto loan on a 2025 vehicle?")
        self.assertIn("5.39%", ans.text)
        self.assertNotIn("5.34%", ans.text)

    def test_as_of_reads_the_ledger_as_it_stood(self):
        ans = self.ks.answer("What was the 12-month certificate APY?", as_of="2026-05-31")
        self.assertIn("2.90%", ans.text)
        self.assertNotIn("3.40%", ans.text)

    def test_as_of_before_first_snapshot_uses_first(self):
        ans = self.ks.answer("What was the 12-month certificate APY?", as_of="2025-01-01")
        self.assertIn("2.90%", ans.text)

    def test_ended_promotion_is_not_current_and_official_page_outranks_it(self):
        current = self.ks.answer("What is the 6-month certificate APY?")
        self.assertIn("3.40%", current.text)
        self.assertNotIn("4.50%", current.text)
        past = self.ks.answer("What was the 6-month certificate APY?", as_of="2026-05-20")
        self.assertNotIn("4.50%", past.text)
        self.assertIn("3.40%", past.text)

    def test_ended_promotion_answers_as_of_its_time(self):
        ks = system(snap("01_2026-05-12_base", "2026-05-12", {"/promo": PROMO, "/contact": CONTACT}),
                    snap("02_2026-09-28_latest", "2026-09-28", {"/promo": PROMO, "/contact": CONTACT}))
        past = ks.answer("What APY did the summer special 6-month certificate pay?", as_of="2026-05-20")
        self.assertIn("4.50%", past.text)
        self.assertTrue(past.confident)
        self.assertNotIn("4.50%", ks.answer("What APY does the summer special 6-month certificate pay?").text)

    def test_date_in_question_text_sets_the_as_of_date(self):
        for q in ("What was the 12-month certificate APY as of May 15, 2026?", "What was the 12-month certificate APY on 5/15/2026?"):
            ans = self.ks.answer(q)
            self.assertIn("2.90%", ans.text, q)
            self.assertNotIn("3.40%", ans.text, q)
        self.assertEqual(parse("What was the 12-month certificate APY as of mid-May 2026?").as_of_hint, "2026-05-15")
        self.assertEqual(parse("What was the 12-month certificate APY in May 2026?").as_of_hint, "2026-05-31")

    def test_date_in_question_is_not_a_model_year(self):
        ans = self.ks.answer("What was the 60-month auto loan APR in May 2026?")
        self.assertIn("5.19%", ans.text)
        self.assertIn("6.10%", ans.text)
        self.assertIsNone(parse_question_qualifiers("60-month auto loan rate as of June 2026").years)
        self.assertEqual(parse_question_qualifiers("As of May 2026, the APR on a 2025 model-year car?").years, (2025, 2025))


class Authority(unittest.TestCase):
    def setUp(self):
        self.ks = system(snap("01_2026-09-28_only", "2026-09-28", {
            "/rates": CERT_TABLE.format(r12="3.37", a12="3.40", eff="09/01/2026"), "/learn/certificate-tips": BLOG_TABLE}))

    def test_official_rate_sheet_beats_a_closer_worded_blog_row(self):
        ans = self.ks.answer("What is the 12-month certificate APY?")
        self.assertIn("3.40%", ans.text)
        self.assertNotIn("2.90%", ans.text)
        self.assertEqual(ans.sources[0].url, SITE + "/rates")
        self.assertIn("/rates", ans.conflict_note)

    def test_catalogue_never_lists_the_blog_value(self):
        ans = self.ks.answer("Which certificates do you offer and what are their APYs?")
        self.assertIn("3.40%", ans.text)
        self.assertNotIn("2.90%", ans.text)

    def test_a_product_the_question_names_is_not_merged_into_another(self):
        ks = system(snap("01_2026-09-28_only", "2026-09-28", {
            "/rates": CERT_TABLE.format(r12="3.37", a12="3.40", eff="09/01/2026"), "/student-certificates": STUDENT_TABLE}))
        self.assertEqual(numbers(ks.answer("What is the 12-month student certificate APY?").text), ["4.00%"])
        self.assertEqual(numbers(ks.answer("What is the 12-month certificate APY?").text), ["3.40%"])


class Qualifiers(unittest.TestCase):
    def setUp(self):
        self.ks = system(snap("01_2026-09-28_only", "2026-09-28", {"/savings": SAVINGS_TABLE, "/rates": CERT_TABLE.format(
            r12="3.37", a12="3.40", eff="06/02/2026")}))

    def test_balance_tier_is_selected(self):
        ans = self.ks.answer("What dividend rate does Share Savings pay on a $15,000 balance?")
        self.assertEqual(numbers(ans.text), ["0.15%"])

    def test_highest_tier(self):
        ans = self.ks.answer("What is the highest Share Savings APY?")
        self.assertIn("0.20%", ans.text)
        self.assertNotIn("0.15%", ans.text)

    def test_term_is_respected(self):
        ans = self.ks.answer("6-month certificate APY?")
        self.assertIn("3.40%", ans.text)
        self.assertNotIn("12 Month", ans.text)

    def test_number_words(self):
        self.assertEqual(parse_question_qualifiers("six month certificate").terms, frozenset({6}))
        self.assertEqual(parse_question_qualifiers("30-year mortgage").terms, frozenset({360}))

    def test_scaled_amounts(self):
        self.assertEqual(parse_question_qualifiers("APY for a $2 million balance").amount, (2_000_000.0, 2_000_000.0))

    def test_minimum_to_open_is_a_table_value(self):
        ans = self.ks.answer("What is the minimum deposit to open a 12 month certificate?")
        self.assertIn("$1,000.00", ans.text)
        self.assertNotIn("%", ans.text)

    def test_row_qualifiers(self):
        q = parse_row_qualifiers("2024-27 Model Year", [("Term", "60 Months")], ["Auto Loans"])
        self.assertEqual(q.years, (2024, 2027))
        self.assertEqual(q.terms, frozenset({60}))
        q = parse_row_qualifiers("Share Savings", [("Balance Tier", "$10,000 \u2013 $24,999.99")], [])
        self.assertEqual(q.amount, (10000.0, 24999.99))
        q = parse_row_qualifiers("17 Month Fixed Jumbo Certificate Special", [], ["Special Certificates"])
        self.assertIn("jumbo", q.variants)

    def test_tiers_are_not_conflicts(self):
        topics = " ".join(c.topic for c in self.ks.conflicts())
        self.assertNotIn("Share Savings", topics)


class Refusals(unittest.TestCase):
    def setUp(self):
        self.ks = system(snap("01_2026-09-28_only", "2026-09-28", {
            "/rates": CERT_TABLE.format(r12="3.37", a12="3.40", eff="06/02/2026"), "/contact": CONTACT}))

    def assert_refused(self, ans):
        self.assertFalse(ans.confident)
        self.assertFalse(re.search(r"\d", ans.text), "a refusal must not state any number")

    def test_unlisted_term_is_refused(self):
        self.assert_refused(self.ks.answer("What APY does a 9-month certificate earn?"))

    def test_unlisted_product_is_refused(self):
        self.assert_refused(self.ks.answer("What is your rate on a 7/1 adjustable-rate mortgage?"))

    def test_unknown_branch_is_refused(self):
        self.assert_refused(self.ks.answer("What are the lobby hours at the Elm Street branch?"))

    def test_label_value_lines_are_answered(self):
        self.assertIn("123456789", self.ks.answer("What is the routing number?").text)
        self.assertIn("100 Main St.", self.ks.answer("What is the address of the Springfield branch?").text)


class Statements(unittest.TestCase):
    def test_newest_press_release_wins(self):
        pages = {f"/newsroom/release-{i}": NEWS.format(date=d, title=f"Release {i}", n=n)
                 for i, (d, n) in enumerate([("March 1, 2023", 24), ("June 1, 2023", 24), ("June 1, 2024", 23),
                                             ("March 3, 2025", 23), ("May 5, 2026", 22)])}
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))
        ans = ks.answer("How many branches does the credit union have?")
        self.assertIn("22", ans.text)
        self.assertNotIn("24", ans.text)
        conflicts = ks.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertIn("22", conflicts[0].resolution)
        self.assertGreaterEqual(len(conflicts[0].values), 3)


class Conflicts(unittest.TestCase):
    def test_changed_rate_is_reported_with_newest_winner(self):
        ks = system(snap("01_2026-05-12_base", "2026-05-12", {"/rates": CERT_TABLE.format(r12="2.88", a12="2.90", eff="03/03/2026")}),
                    snap("02_2026-09-28_latest", "2026-09-28", {"/rates": CERT_TABLE.format(r12="3.37", a12="3.40", eff="06/02/2026")}))
        conflicts = ks.conflicts()
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertIn("12", c.topic)
        self.assertTrue(any("2.90%" in v for v in c.values) and any("3.40%" in v for v in c.values))
        self.assertIn("3.40%", c.resolution)
        self.assertTrue(c.reason)

    def test_terms_with_the_same_rate_history_are_separate_conflicts(self):
        ks = system(snap("01_2026-05-12_base", "2026-05-12", {"/rates": TERMS_TABLE.format(apy="3.25", eff="03/03/2026")}),
                    snap("02_2026-09-28_latest", "2026-09-28", {"/rates": TERMS_TABLE.format(apy="3.40", eff="06/02/2026")}))
        topics = sorted(c.topic for c in ks.conflicts())
        self.assertEqual(len(topics), 2, topics)
        self.assertIn("6", topics[0])
        self.assertIn("9", topics[1])

    def test_page_that_changes_back_is_sourced_from_the_crawl_that_saw_it_again(self):
        a = CERT_TABLE.format(r12="3.37", a12="3.40", eff="06/02/2026")
        b = CERT_TABLE.format(r12="2.88", a12="2.90", eff="07/01/2026")
        ks = system(snap("01_2026-05-12_a", "2026-05-12", {"/rates": a}), snap("02_2026-06-09_b", "2026-06-09", {"/rates": b}),
                    snap("03_2026-09-28_a", "2026-09-28", {"/rates": a}))
        self.assertIn("3.40%", ks.answer("What is the APY on a 12-month certificate?").text)
        conflicts = ks.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].sources[0].snapshot_id, "03_2026-09-28_a")


class Determinism(unittest.TestCase):
    QUESTIONS = ["What is the APY on a 12-month certificate?", "What is the routing number?",
                 "What is the rate on a 5/1 ARM?", "Certificate rates?"]

    def test_same_answer_every_time(self):
        def run():
            ks = system(snap("01_2026-09-28_only", "2026-09-28", {
                "/rates": CERT_TABLE.format(r12="3.37", a12="3.40", eff="06/02/2026"), "/contact": CONTACT}))
            return [(a.text, [s.url for s in a.sources], a.confident) for a in map(ks.answer, self.QUESTIONS)]
        first = run()
        self.assertEqual(first, run())


class HookSeam(unittest.TestCase):
    def test_phrasing_hook_cannot_change_figures(self):
        from truthledger.hooks import Hooks, safe_phrase

        class Rewords(Hooks):
            def phrase(self, question, text):
                return "Good news! " + text

        class ChangesValue(Hooks):
            def phrase(self, question, text):
                return text.replace("3.40%", "3.50%")

        text = "12 Month: 3.40% APY."
        self.assertEqual(safe_phrase(Rewords(), "q", text), "Good news! " + text)
        self.assertEqual(safe_phrase(ChangesValue(), "q", text), text)


class TextHelpers(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(find_dates("June 18, 2024"), ["2024-06-18"])
        self.assertEqual(effective_date("Rates are effective as of 06/02/2026 and are"), "2026-06-02")
        self.assertEqual(effective_date("Rates are effective as of 1/1/2026."), "2026-01-01")


if __name__ == "__main__":
    unittest.main()
