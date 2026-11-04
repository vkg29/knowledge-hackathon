"""Identity, timeline, what-changed and contradiction tests on small synthetic websites.

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
from truthledger.catalogue import family_of, product_words, question_family  # noqa: E402
from truthledger.query import parse  # noqa: E402
from truthledger.text import relative_date  # noqa: E402

from solution import build  # noqa: E402

SITE = "https://cu.example"


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


def key_of(ks, url, label, tier=None):
    """The identity card of the newest row with this label (and printed tier) on this page."""
    ids = ks.ledger.identities()
    rows = [r for r in ks.ledger.rows if r.kind == "table" and r.page.url == SITE + url and r.label == label
            and (tier is None or tier in r.qual_text)]
    return ids[rows[-1].rid]


AUTO = """# Rates

Auto & Motorcycle Loans

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| {new} Model Year | 60 Months | {a}% APR |
| {mid} Model Year | 60 Months | {b}% APR |

Rates are effective as of {eff}. All loans are subject to approval.
"""

AUTO_CALCULATOR = """# Vehicle affordability calculator

Auto Loan Rates

| Loan Program | Term | APR |
| --- | --- | --- |
| 2023-26 Model Year | 60 Months | 5.34% APR |
| 2021-22 Model Year | 60 Months | 5.59% APR |
"""

SPECIALS = """# Rates

Special Certificates

| Term | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| {term} Month Fixed Jumbo Certificate Special | {jd}% | {ja}% |
| {term} Month Fixed Certificate Special | {d}% | {a}% |

Rates are effective as of {eff} and are subject to change without notice.
"""

OLD_CHECKING = """# Rates

- [Checking Account Rates](https://cu.example/rates#)

Checking Account Rates

| Balance | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| $25,000+ | 0.10% | 0.10% |
| $5,000 - $24,999.99 | 2.96% | 3.00% |
| $0.01 - $4,999.99 | 0.00% | 0.00% |

Rates are effective as of 03/03/2026 and are subject to change without notice.
"""

NEW_CHECKING = """# Rates

| Account | Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- | --- |
| 1st Rate Checking | $25,000+ | 0.10% | 0.10% |
| 1st Rate Checking | $5,000 – $24,999.99 | 2.47% | 2.50% |
| 1st Rate Checking | $0.01 – $4,999.99 | 0.00% | 0.00% |

Rates are effective as of 06/02/2026 and are subject to change without notice.
"""

CHECKING_PROMO = """# Checking offer

Checking Accounts

| Balance | Dividend Rate | APY |
| --- | --- | --- |
| $25,000+ | 0.10% | 0.10% |
| $5,000 - $24,999.99 | 2.96% | 3.00% |
| $0.01 - $4,999.99 | 0.00% | 0.00% |

Rates are effective as of 07/01/2025.
"""

# The May-August sheet layout: tables sit under in-page tab links, and some captions name no product.
TABBED = """# Rates

- [Savings Account Rates](https://cu.example/rates#)

Share Savings Account

| Account Type | Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- | --- |
| Share Savings | $25,000+ | 0.20% | 0.20% |
| Share Savings | $0.00 - $24,999.99 | 0.10% | 0.10% |

Rates are effective as of 03/03/2026 and are subject to change without notice.

- [Money Market Account Rates](https://cu.example/rates#)

Investor’s Choice

| Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| $25,000+ | 0.35% | 0.35% |
| $0.01 - $24,999.99 | 0.25% | 0.25% |

Rates are effective as of 03/03/2026 and are subject to change without notice.

High-Yield Savings

| Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| $250,000+ | 2.03% | 2.05% |

Rates are effective as of 03/03/2026 and are subject to change without notice.

- [Term-Share Certificate Rates](https://cu.example/rates#)

Special

| Term | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| 25 Month Fixed Certificate Special | 3.57% | 3.60% |

Rates are effective as of 03/03/2026 and are subject to change without notice.

Regular

| Term | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| 12 Month | 2.88% | 2.90% |

Rates are effective as of 03/03/2026 and are subject to change without notice.

- [Retirement Account Rates](https://cu.example/rates#)

Regular IRAs

| Term | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| Roth: 18 Months | 2.68% | 2.70% |
| Traditional: 18 Months | 2.68% | 2.70% |

Rates are effective as of 03/03/2026 and are subject to change without notice.
"""

UNTABBED = """# Rates

Share Savings

| Account | Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- | --- |
| Share Savings | $25,000+ | 0.20% | 0.20% |
| Share Savings | $0 – $24,999.99 | 0.10% | 0.10% |

Rates are effective as of 06/15/2026 and are subject to change without notice.

Investor's Choice Money Market

| Account | Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- | --- |
| Investor's Choice Money Market | $25,000+ | 0.35% | 0.35% |
| Investor's Choice Money Market | $0.01 – $24,999.99 | 0.25% | 0.25% |

Rates are effective as of 06/02/2026 and are subject to change without notice.

Regular Certificates

| Certificate | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| 12 Month | 3.37% | 3.40% |

Rates are effective as of 06/02/2026 and are subject to change without notice.

Regular IRAs

| Term | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- |
| Roth: 18 Months | 2.68% | 2.70% |
| Traditional: 18 Months | 2.68% | 2.70% |

Rates are effective as of 06/02/2026 and are subject to change without notice.
"""

HELOC = """# Home equity

Home Equity Line of Credit (HELOC)

| Loan Program | Annual Percentage Rate (As Low As) |
| --- | --- |
| HELOC – Standard | 6.25% APR |

Rates are effective as of 08/01/2026.
"""

JOIN = """# Ready to become our newest member?

When you join, you become a member-owner with better rates and fewer fees.

#### Local Service

You can find us in Iowa, Illinois and Wisconsin.

##### WHO CAN JOIN

## You're Eligible to Join If Any of These Sound Like You

- You live or work within our charter area.
- You have an immediate family member who's a member.

To join, simply open a savings account with $25 or more.
"""

RELEASE = """2 min read

{date}

# Volunteering pays off

{n} employees used a paid benefit in {year} called Volunteer Time Off, allowing them to volunteer during the workday.
"""


class IdentityMatching(unittest.TestCase):
    def test_catalogue_maps_member_wording_onto_families(self):
        self.assertEqual(family_of("What is the rate on a 12-month CD?"), "certificate")
        self.assertEqual(family_of("Term-Share Certificate Rates"), "certificate")
        self.assertEqual(family_of("Purchase: 30-Year Fixed Conventional $300,000 or less"), "mortgage")
        self.assertEqual(family_of("Home Equity Line of Credit (HELOC)"), "heloc")
        self.assertEqual(family_of("ATV, Snowmobile & Jet Ski Loans"), "powersport loan")
        self.assertEqual(family_of("2025 car"), "auto loan")
        self.assertEqual(question_family("What are the home equity rates?"), "home equity")
        self.assertEqual(product_words("Share Savings", "savings"), frozenset({"share"}))
        self.assertEqual(product_words("12 Month Term-Share Certificate", "certificate"), frozenset())
        self.assertEqual(product_words("12 Month Student Certificate", "certificate"), frozenset({"student"}))
        self.assertEqual(product_words("1st Rate Checking", "checking"), frozenset({"1st"}))

    def test_renamed_model_year_tiers_keep_their_slot_across_crawls(self):
        ks = system(snap("01_2026-05-12_a", "2026-05-12", {"/rates": AUTO.format(new="2023-26", mid="2021-22", a="5.19", b="5.44", eff="5/1/2026")}),
                    snap("02_2026-09-28_b", "2026-09-28", {"/rates": AUTO.format(new="2024-27", mid="2021-23", a="5.39", b="5.64", eff="8/31/2026"),
                                                          "/calculators/vehicle-affordability": AUTO_CALCULATOR}))
        ids = ks.ledger.identities()
        rows = [r for r in ks.ledger.rows if r.kind == "table"]
        newest = {ids[r.rid] for r in rows if r.label in ("2023-26 Model Year", "2024-27 Model Year")}
        middle = {ids[r.rid] for r in rows if r.label in ("2021-22 Model Year", "2021-23 Model Year")}
        self.assertEqual(len(newest), 1, newest)   # the stale calculator copy is the same fact, an older copy
        self.assertEqual(len(middle), 1, middle)
        self.assertNotEqual(newest, middle)
        # a model year falls in the slot whose *current* winning row prints it
        self.assertEqual(numbers(ks.answer("What's the 60-month APR for a 2025 car?").text), ["5.39%"])
        self.assertEqual(numbers(ks.answer("What's the 60-month APR for a 2023 car?").text), ["5.64%"])
        self.assertEqual(numbers(ks.answer("What's the 60-month APR for a 2023 car?", as_of="2026-05-31").text), ["5.19%"])

    def test_unnamed_table_inherits_its_pages_name_and_landing_page_copy_joins_it(self):
        ks = system(snap("01_2026-05-12_a", "2026-05-12", {"/rates": OLD_CHECKING, "/checking-offer-lp": CHECKING_PROMO}),
                    snap("02_2026-09-28_b", "2026-09-28", {"/rates": NEW_CHECKING, "/checking-offer-lp": CHECKING_PROMO}))
        old = key_of(ks, "/rates", "$5,000 - $24,999.99")
        self.assertEqual(old, key_of(ks, "/rates", "1st Rate Checking", "$5,000"))   # the old row's label is its tier
        self.assertEqual(old, key_of(ks, "/checking-offer-lp", "$5,000 - $24,999.99"))
        self.assertEqual(old.product, frozenset({"1st"}))
        ans = ks.answer("How much does 1st Rate Checking pay on $10,000?")
        self.assertEqual(numbers(ans.text), ["2.47%", "2.50%"])   # the stale 3.00% landing page copy loses
        self.assertIn("promotion page", ans.conflict_note)

    def test_caption_names_the_product_before_the_tab_it_sits_under(self):
        ks = system(snap("01_2026-05-12_a", "2026-05-12", {"/rates": TABBED}),
                    snap("02_2026-09-28_b", "2026-09-28", {"/rates": UNTABBED}))
        self.assertEqual(key_of(ks, "/rates", "$250,000+").family, "savings")     # High-Yield Savings, not money market
        self.assertEqual(key_of(ks, "/rates", "12 Month").family, "certificate")  # 'Regular' under the certificate tab
        self.assertEqual(key_of(ks, "/rates", "$25,000+").product, frozenset({"investor", "choic"}))
        # as-of answers no longer depend on the tab text (the deferred PR 1 gaps)
        share = ks.answer("What did Share Savings pay on a $30,000 balance?", as_of="2026-05-15")
        self.assertEqual(numbers(share.text), ["0.20%", "0.20%"])
        mm = ks.answer("What did the Investor's Choice money market pay on $50,000?", as_of="2026-05-15")
        self.assertEqual(numbers(mm.text), ["0.35%", "0.35%"])

    def test_every_asked_kind_is_required(self):
        ks = system(snap("01_2026-09-28_only", "2026-09-28", {"/rates": UNTABBED, "/personal/loans/home-equity": HELOC}))
        for q in ("What is the Roth IRA money market rate?", "What is the business HELOC rate?",
                  "What is the 12-month IRA certificate rate?"):
            ans = ks.answer(q)
            self.assertFalse(ans.confident, q)
            self.assertEqual(numbers(ans.text), [], q)
        ira = ks.answer("What is the 18-month IRA certificate APY?")
        self.assertEqual(numbers(ira.text), ["2.70%", "2.70%"])   # Roth and Traditional, both
        self.assertIn("Roth", ira.text)
        self.assertIn("Traditional", ira.text)


class Timelines(unittest.TestCase):
    def setUp(self):
        self.ks = system(
            snap("01_2026-05-12_a", "2026-05-12", {"/rates": SPECIALS.format(term=25, jd="3.67", ja="3.70", d="3.57", a="3.60", eff="03/03/2026")}),
            snap("02_2026-06-09_b", "2026-06-09", {"/rates": SPECIALS.format(term=17, jd="3.91", ja="3.95", d="3.81", a="3.85", eff="06/02/2026")}),
            snap("03_2026-08-27_c", "2026-08-27", {"/rates": SPECIALS.format(term=17, jd="4.01", ja="4.05", d="3.91", a="3.95", eff="07/14/2026")}),
            snap("04_2026-09-28_d", "2026-09-28", {"/rates": SPECIALS.format(term=17, jd="4.15", ja="4.20", d="4.06", a="4.10", eff="09/01/2026")}))

    def test_spans_carry_the_printed_effective_dates(self):
        key = key_of(self.ks, "/rates", "17 Month Fixed Certificate Special")
        spans = self.ks.ledger.timeline().spans(key)
        self.assertEqual([s.valid_from for s in spans], ["2026-06-02", "2026-07-14", "2026-09-01"])
        self.assertEqual([(s.first, s.last) for s in spans], [(1, 1), (2, 2), (3, 3)])
        self.assertTrue(self.ks.ledger.timeline().current(key))
        self.assertFalse(self.ks.ledger.timeline().current(key_of(self.ks, "/rates", "25 Month Fixed Certificate Special")))

    def test_history_since_a_month(self):
        ans = self.ks.answer("How has the 17-month certificate special APY changed since June 2026?")
        self.assertEqual(numbers(ans.text)[-3:], ["3.85%", "3.95%", "4.10%"])
        self.assertIn("effective September 1, 2026", ans.text)
        self.assertIn("current rate", ans.text)
        self.assertNotIn("4.20%", ans.text)   # a history is about the asked size only

    def test_before_and_when(self):
        before = self.ks.answer("What did the 17-month special pay before its most recent rate increase?")
        self.assertRegex(before.text, r"paid 3\.95% APY; it now pays 4\.10% APY")
        when = self.ks.answer("When did the 17-month certificate special rate last change?")
        self.assertIn("September 1, 2026", when.text)
        self.assertIn("from 3.95% APY to 4.10% APY", when.text)

    def test_ended_special_is_never_presented_as_current(self):
        gone = self.ks.answer("Is the 25-month certificate special still available?")
        self.assertFalse(gone.confident)
        self.assertIn("no longer", gone.text)
        self.assertEqual(numbers(gone.text), [])
        paid = self.ks.answer("Is the 25-month certificate special still offered, and what did it pay when it was?")
        self.assertIn("no longer offered", paid.text)
        self.assertIn("3.60%", paid.text)
        self.assertEqual(self.ks.answer("What is the 25-month certificate special APY?").confident, False)

    def test_relative_dates_count_back_from_the_newest_crawl(self):
        today = "2026-09-28"
        self.assertEqual(relative_date("last month", today), "2026-08-31")
        self.assertEqual(relative_date("two months ago", today), "2026-07-28")
        self.assertEqual(relative_date("this past May", today), "2026-05-31")
        self.assertEqual(relative_date("in October", today), "2025-10-31")
        self.assertEqual(parse("What did the 17-month special pay last month?", today).as_of_hint, "2026-08-31")
        self.assertEqual(parse("How many employees volunteered last year?", today).year, 2025)
        last_month = self.ks.answer("What did the 17-month certificate special pay last month?")
        self.assertIn("3.95%", last_month.text)
        self.assertNotIn("4.10%", last_month.text)

    def test_current_special_answer_states_its_jumbo_version_but_as_of_answers_do_not(self):
        now = self.ks.answer("What does the 17-month certificate special pay?")
        self.assertEqual(sorted(set(numbers(now.text))), ["4.06%", "4.10%", "4.15%", "4.20%"])
        then = self.ks.answer("What did the 17-month certificate special pay?", as_of="2026-08-30")
        self.assertEqual(sorted(set(numbers(then.text))), ["3.91%", "3.95%"])
        jumbo = self.ks.answer("What is the 17-month jumbo special APY?")
        self.assertEqual(numbers(jumbo.text), ["4.20%"])


class Contradictions(unittest.TestCase):
    def test_one_entry_per_identity_with_a_single_winner(self):
        ks = system(snap("01_2026-05-12_a", "2026-05-12", {"/rates": AUTO.format(new="2023-26", mid="2021-22", a="5.19", b="5.44", eff="5/1/2026")}),
                    snap("02_2026-09-28_b", "2026-09-28", {"/rates": AUTO.format(new="2024-27", mid="2021-23", a="5.39", b="5.64", eff="8/31/2026"),
                                                          "/calculators/vehicle-affordability": AUTO_CALCULATOR}))
        conflicts = {c.topic: c for c in ks.conflicts()}
        self.assertEqual(len(conflicts), 2, list(conflicts))
        newest = next(c for t, c in conflicts.items() if "2024-27" in t)
        middle = next(c for t, c in conflicts.items() if "2021-23" in t)
        self.assertEqual(newest.resolution, "5.39% APR")
        self.assertEqual(sorted(newest.values), ["5.19% APR", "5.34% APR", "5.39% APR"])   # never a 2021-23 figure
        self.assertEqual(middle.resolution, "5.64% APR")
        self.assertEqual(sorted(middle.values), ["5.44% APR", "5.59% APR", "5.64% APR"])
        self.assertIn("outranks calculator page", newest.reason)
        self.assertIn("5.19% APR from May 1, 2026, then 5.39% APR from August 31, 2026", newest.reason)

    def test_tiers_and_partial_copies_are_not_contradictions(self):
        ks = system(snap("01_2026-09-28_only", "2026-09-28", {"/rates": NEW_CHECKING}))
        self.assertEqual(ks.conflicts(), [])


class JoinedPassages(unittest.TestCase):
    def test_eligibility_answer_names_the_places_of_the_charter_area(self):
        ks = system(snap("01_2026-09-28_only", "2026-09-28", {"/about/join": JOIN}))
        for q in ("Who is eligible to become a member?", "Who can join?"):
            ans = ks.answer(q)
            self.assertIn("charter area", ans.text, q)
            for state in ("Iowa", "Illinois", "Wisconsin"):
                self.assertIn(state, ans.text, q)

    def test_statements_about_different_years_are_separate_facts(self):
        ks = system(snap("01_2026-09-28_only", "2026-09-28", {
            "/newsroom/vto-2024": RELEASE.format(date="April 19, 2024", n=294, year=2023),
            "/newsroom/vto-2025": RELEASE.format(date="April 21, 2025", n=256, year=2024)}))
        self.assertIn("294", ks.answer("How many employees used Volunteer Time Off in 2023?").text)
        self.assertNotIn("256", ks.answer("How many employees used Volunteer Time Off in 2023?").text)
        self.assertNotIn("294", ks.answer("How many employees used Volunteer Time Off?").text)   # newest year by default
        last = ks.answer("How many employees used Volunteer Time Off last year?").text   # 2025: not stated
        self.assertIn("no figure for 2025", last)
        self.assertLess(last.index("256"), last.index("294"))


if __name__ == "__main__":
    unittest.main()
