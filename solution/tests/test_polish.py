"""Tests for the polish rules on small synthetic websites (no real snapshot text): the newest dated
statement wins for prose facts, account-service questions go to the site's own instructions, the most
official page that states a value is cited first, rate links point at the product page's rates section,
and conflicts() lists each repeated claim once.

Run from the repository root:  python -m unittest discover -s solution/tests -v
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "solution"))
os.environ.pop("ANTHROPIC_API_KEY", None)   # unit tests never call the real API

from contract import Page, Snapshot  # noqa: E402
from truthledger.query import parse  # noqa: E402
from truthledger.text import counts, states_count  # noqa: E402

from solution import build  # noqa: E402

SITE = "https://cu.example"
FOOTER = ("ABOUT THE CREDIT UNION\n\nThe credit union is a member-owned financial cooperative. "
          "It is an Acme Magazine Best-in-State Credit Union and a Register Top Workplace.")


def release(date, title, *paragraphs, footer=True):
    body = "\n\n".join(paragraphs) + ("\n\n" + FOOTER if footer else "")
    return f"2 min read\n\n{date}\n\n# {title}\n\n{body}\n"


def snap(sid, date, pages, titles=None):
    titles = titles or {}
    return Snapshot(sid, date, [Page(url=SITE + path, title=titles.get(path, path.strip("/") or "home"), markdown=md,
                                     fetched_at=date + "T00:00:00Z") for path, md in pages.items()])


def system(*snapshots):
    ks = build()
    for s in snapshots:
        ks.ingest(s)
    return ks


def paths(ans):
    return [s.url[len(SITE):] for s in ans.sources]


RANKING_2023 = release(
    "July 18, 2023", "Acme Magazine ranks the credit union a top credit union",
    "SPRINGFIELD, Iowa—National business magazine Acme Magazine again named the credit union one of the top-rated "
    "credit unions in America in its ranking of in-state financial institutions. It was the fourth time Acme Magazine "
    "recognized the credit union.",
    "Only institutions receiving the highest ratings were awarded the title Best-In-State, according to Acme Magazine.")
RANKING_2024 = release(
    "June 18, 2024", "Acme Magazine ranks the credit union a top credit union",
    "SPRINGFIELD, Iowa—National business magazine Acme Magazine again named the credit union one of the top-rated "
    "credit unions in America in its ranking of in-state financial institutions. This was the fifth time Acme Magazine "
    "recognized the credit union.",
    "Only institutions receiving the highest ratings were awarded the title Best-In-State, according to Acme Magazine.")
WORKPLACE = release("February 1, 2023", "Credit union receives best place to work recognition",
                    "The credit union received a best place to work recognition from a regional employer group.")


class NewestStatement(unittest.TestCase):
    """Prose facts (awards, counts, programs): the newest dated statement of a claim wins."""

    @classmethod
    def setUpClass(cls):
        dividends = {f"/newsroom/dividend-{y}": release(f"October 19, {y}", f"Credit union shares a bonus dividend in {y}",
                                                        f"This is the {o} consecutive year a participation dividend has been paid.",
                                                        footer=False)
                     for y, o in ((2021, "sixth"), (2022, "seventh"), (2023, "eighth"))}
        pages = {"/newsroom/ranking-2023": RANKING_2023, "/newsroom/ranking-2024": RANKING_2024,
                 "/newsroom/workplace": WORKPLACE, **dividends}
        cls.ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))

    def test_ordinal_count_from_the_newest_release_wins(self):
        ans = self.ks.answer("An older release says Acme Magazine recognized the credit union for the fourth time. "
                             "What is the latest count?")
        self.assertIn("fifth", ans.text)
        self.assertNotIn("fourth", ans.text)

    def test_count_written_as_an_ordinal_answers_a_how_many_question(self):
        ans = self.ks.answer("For how many consecutive years has a participation dividend been paid?")
        self.assertTrue(ans.confident)
        self.assertIn("eighth", ans.text)
        self.assertNotIn("seventh", ans.text)

    def test_article_titled_with_the_name_asked_answers_from_its_lead(self):
        # The footer names Acme Magazine on an unrelated release whose title matches "recognition"; the
        # newest release titled with the name answers, from its lead paragraph.
        ans = self.ks.answer("What recognition has the credit union received from Acme Magazine?")
        self.assertIn("fifth", ans.text)
        self.assertNotIn("fourth", ans.text)
        self.assertEqual(paths(ans)[0], "/newsroom/ranking-2024")

    def test_same_figures_on_a_newer_release_are_read_from_it(self):
        pages = {"/newsroom/april": release("April 23, 2026", "New vice president named", "The credit union named a vice president.",
                                            "It serves residents in 118 counties. Membership has grown to more than 181,000.",
                                            footer=False),
                 "/newsroom/may": release("May 5, 2026", "Helping homebuyers", "The credit union helps homebuyers.",
                                          "It serves residents in 118 counties. Membership has grown to more than 179,000.",
                                          footer=False)}
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))
        ans = ks.answer("How many counties does the credit union serve?")
        self.assertIn("118", ans.text)
        self.assertNotIn("181,000", ans.text)
        self.assertEqual(paths(ans)[0], "/newsroom/may")


class ProseRates(unittest.TestCase):
    """A figure stated only in prose is read from its most official copy, as the matching sentence."""

    def test_official_copy_of_the_best_statement_outranks_a_promotion(self):
        pages = {"/personal/direct-deposit-switch-lp": "# Switch your employer direct deposit\n\nRouting Number: 273974581\n\n"
                                                       "Get $300 with code CHECK300.\n",
                 "/about/contact-us": "# Contact Us\n\nCall us at 800-555-0100.\n\nRouting Number: 273974581\n"}
        titles = {"/personal/direct-deposit-switch-lp": "Switch your employer direct deposit", "/about/contact-us": "Contact Us"}
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages, titles))
        ans = ks.answer("Which routing number should I give my employer for direct deposit?")
        self.assertIn("273974581", ans.text)
        self.assertNotIn("CHECK300", ans.text)

    def test_same_line_on_another_product_page_is_not_a_rival(self):
        older = {"/loans/personal-loans": "# Personal Loans\n\nRates as low as 8.99% APR.\n",
                 "/loans/auto-loans": "# Auto Loans\n\nRates as low as 5.49% APR.\n"}
        newer = {**older, "/loans/auto-loans": "# Auto Loans\n\nRates as low as 5.24% APR.\n"}
        titles = {"/loans/personal-loans": "Personal Loans", "/loans/auto-loans": "Auto Loans"}
        ks = system(snap("01_2026-05-12", "2026-05-12", older, titles), snap("02_2026-09-28", "2026-09-28", newer, titles))
        ans = ks.answer("What rate do personal loans have?")
        self.assertIn("8.99%", ans.text)
        self.assertNotIn("5.24%", ans.text)

    def test_same_line_on_a_more_official_page_about_another_product_is_not_a_rival(self):
        # A strictly more official copy of the best sentence could outrank it, but only a copy about the same
        # product: the auto-loans page's line is another product's figure.
        pages = {"/personal-loan-offer-lp": "# Personal Loans\n\nRates as low as 8.99% APR.\n",
                 "/loans/auto-loans": "# Auto Loans\n\nRates as low as 5.24% APR.\n"}
        titles = {"/personal-loan-offer-lp": "Personal Loans", "/loans/auto-loans": "Auto Loans"}
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages, titles))
        ans = ks.answer("What rate do personal loans have?")
        self.assertIn("8.99%", ans.text)
        self.assertNotIn("5.24%", ans.text)

    def test_same_line_in_a_newer_article_about_another_product_is_not_a_rival(self):
        # A newer dated copy wins only when it is about the same product; the article's headline names the
        # product a rate sentence under it belongs to.
        pages = {"/newsroom/personal-loan-rates": release("March 2, 2026", "Credit union lowers personal loan rates",
                                                          "Rates as low as 8.99% APR.", footer=False),
                 "/newsroom/auto-loan-rates": release("June 1, 2026", "Credit union lowers auto loan rates",
                                                      "Rates as low as 5.24% APR.", footer=False)}
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))
        ans = ks.answer("What rate do personal loans have?")
        self.assertIn("8.99%", ans.text)
        self.assertNotIn("5.24%", ans.text)

    def test_article_lead_does_not_bring_in_another_products_rate(self):
        # The lead paragraph of the article titled with the name asked says what the news is, but a sentence in
        # it that quotes another product's rate is left out.
        pages = {"/newsroom/acme-top-lender": release(
            "August 3, 2026", "Acme Magazine names the credit union a top lender",
            "SPRINGFIELD, Iowa - Acme Magazine recognized the credit union as one of the top lenders in the state. "
            "Its auto loan rates start as low as 5.24% APR.",
            "The recognition from Acme Magazine praised the credit union for its member service and community giving.",
            footer=False)}
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages,
                         {"/newsroom/acme-top-lender": "Acme Magazine names the credit union a top lender"}))
        ans = ks.answer("What recognition has the credit union received from Acme Magazine?")
        self.assertTrue(ans.confident)
        self.assertIn("top lenders", ans.text)
        self.assertNotIn("5.24%", ans.text)

    def test_rate_question_on_an_article_is_not_led_by_its_first_paragraph(self):
        pages = {"/newsroom/holiday-club": release(
            "October 1, 2026", "Holiday Club returns for members",
            "Last year 1,250 members saved a combined $2.4 million for the holidays, an average of $1,920 each.",
            "The Holiday Club pays 2.00% APY on every balance.", footer=False)}
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))
        ans = ks.answer("What APY does the Holiday Club pay?")
        self.assertIn("2.00%", ans.text)
        self.assertNotIn("1,250", ans.text)


CONTACT = """# We're Here to Help

{lead}

**Phone:** 800-555-0100

**Routing Number:** 123456789
""".format(lead="For general questions, use our form and we'll reply Monday–Friday, 8:30 a.m.–5:30 p.m. Have a specific "
                "account question? Use the Contact Us tab inside Online Banking. Or if you have an urgent need, like a lost "
                "card, please call us immediately at 800-555-0100.")

MOVING_BLOG = release("September 12, 2025", "Your guide to moving",
                      "When you move, updating your contact information is critical. Start with the postal service and "
                      "request your mail to be forwarded to your new address.", footer=False)

LOGIN_HELP = """# Online Banking Login Help

Browser extensions and privacy settings can sometimes block parts of online banking, causing login issues.

## Still having trouble?

If those steps didn't resolve your issue, please call us at 800-555-0100 for further assistance.
"""

CASH_MANAGEMENT = """# Cash Management

## What information is required for incoming transfers?

- Our routing number: Provide the payer with our ABA number (123456789).
"""

TAX_BLOG = release("February 6, 2026", "Get your tax refund faster",
                   "Routing number: Our routing number is 123456789 for direct deposit of your refund.", footer=False)

BRANCH_NEWS = release("June 1, 2025", "Credit union opens a new branch", "The new branch opened this week.",
                      "Lobby\n\nMonday-Friday: 8:30 a.m.-5:30 p.m.\n\nSaturday: 8:30 a.m.-12:30 p.m.", footer=False)


class ServiceQuestions(unittest.TestCase):
    """'How do I update my details / I lost my card / I can't sign in': the site's own instructions."""

    @classmethod
    def setUpClass(cls):
        pages = {"/contact": CONTACT, "/learn/blog/moving": MOVING_BLOG, "/online-banking/login-help": LOGIN_HELP}
        titles = {"/contact": "Contact Us | Credit Union", "/online-banking/login-help": "Online Banking Login Help"}
        cls.ks = system(snap("01_2026-09-28_only", "2026-09-28", pages, titles))

    def test_updating_contact_details_is_answered_by_the_contact_page_lead(self):
        ans = self.ks.answer("How can I update my contact information?")
        self.assertIn("800-555-0100", ans.text)
        self.assertNotIn("postal service", ans.text)
        self.assertEqual(paths(ans)[0], "/contact")

    def test_lost_card_gets_the_phone_number(self):
        ans = self.ks.answer("I lost my debit card. What should I do?")
        self.assertIn("800-555-0100", ans.text)

    def test_page_titled_with_the_task_answers_and_its_phone_number_is_added(self):
        ans = self.ks.answer("I can't log in to online banking. What should I do?")
        self.assertEqual(paths(ans)[0], "/online-banking/login-help")
        self.assertIn("login issues", ans.text)
        self.assertIn("800-555-0100", ans.text)


class OfficialSourceFirst(unittest.TestCase):
    """Among the pages that state the answering value, the most official kind is cited first."""

    @classmethod
    def setUpClass(cls):
        pages = {"/contact": CONTACT, "/business/cash-management": CASH_MANAGEMENT, "/learn/blog/tax-refund": TAX_BLOG,
                 "/newsroom/new-branch": BRANCH_NEWS}
        cls.ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))

    def test_routing_number_cites_every_official_page_that_states_it_before_the_blog(self):
        ans = self.ks.answer("Which routing number should I give my employer for direct deposit?")
        self.assertIn("123456789", ans.text)
        cited = paths(ans)
        self.assertIn("/business/cash-management", cited)
        self.assertIn("/contact", cited)
        if "/learn/blog/tax-refund" in cited:
            self.assertGreater(cited.index("/learn/blog/tax-refund"), cited.index("/contact"))

    def test_general_hours_cite_the_official_page_with_the_same_weekday_schedule_first(self):
        ans = self.ks.answer("What are the general lobby hours?")
        self.assertIn("8:30", ans.text)
        self.assertEqual(paths(ans)[0], "/contact")


SAVINGS_PAGE = """# Savings

[Compare rates](https://cu.example/personal/savings-account#rates)

Certificates

| Certificate | Dividend Rate | APY |
| --- | --- | --- |
| 12 Month | 3.37% | 3.40% |
| 6 Month | 3.27% | 3.30% |

Rates are effective as of 06/02/2026.
"""

RATE_SHEET = """# Rates

Certificates

| Certificate | Dividend Rate | APY |
| --- | --- | --- |
| 12 Month | 3.37% | 3.40% |
| 6 Month | 3.27% | 3.30% |

Rates are effective as of 06/02/2026. [See today's rates](https://cu.example/personal/savings-account#rates)
"""

OLD_LINK_BLOG = release("March 1, 2021", "Certificate basics",
                        "Open a [term-share certificate](https://cu.example/personal/savings-accounts/certificates/) "
                        "to earn certificate rates higher than savings.", footer=False)


class Links(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pages = {"/personal/savings-account": SAVINGS_PAGE, "/rates": RATE_SHEET, "/learn/blog/certificate-basics": OLD_LINK_BLOG}
        cls.ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))

    def test_rates_link_is_the_product_page_rates_section_the_site_links_to(self):
        ans = self.ks.answer("What link should I use to see certificate rates?")
        self.assertIn("https://cu.example/personal/savings-account#rates", ans.text)
        self.assertNotIn("savings-accounts/certificates", ans.text)
        self.assertEqual(paths(ans)[0], "/personal/savings-account")

    def test_where_on_the_website_is_a_link_question(self):
        ans = self.ks.answer("Where on the website can I find certificate rates?")
        self.assertIn("savings-account#rates", ans.text)


def boilerplate(date, sentence):
    return release(date, f"Community news from {date}", "The credit union shared community news.", sentence, footer=False)


SPECIALS = """# Rates

Special Certificates

| Certificate | Dividend Rate | APY | Min. to Open |
| --- | --- | --- | --- |
| 17 Month Fixed Certificate Special | {r}% | {a}% | $1,000.00 |

Special Business Certificates

| Certificate | Dividend Rate | APY | Min. to Open |
| --- | --- | --- | --- |
| 17 Month Fixed Certificate Special | {r}% | {a}% | $1,000.00 |

Rates are effective as of {eff}.
"""


class ConflictList(unittest.TestCase):
    def test_one_claim_in_two_wordings_is_one_contradiction_settled_by_the_newest_copy(self):
        old = [("January 5, 2021", 129), ("March 5, 2021", 131), ("May 5, 2021", 133), ("July 5, 2021", 136),
               ("September 5, 2021", 141)]
        new = [("January 5, 2026", 175), ("February 5, 2026", 176), ("March 5, 2026", 178), ("April 5, 2026", 181),
               ("May 5, 2026", 179)]
        pages = {f"/newsroom/old-{i}": boilerplate(d, f"Chartered in 1948, membership totals more than {n},000 with "
                                                      f"assets exceeding $2.{i} billion.") for i, (d, n) in enumerate(old)}
        pages.update({f"/newsroom/new-{i}": boilerplate(d, f"Founded in 1948 by ten employees, membership has grown to more "
                                                         f"than {n},000 with assets exceeding $3.{i} billion.")
                      for i, (d, n) in enumerate(new)})
        ks = system(snap("01_2026-09-28_only", "2026-09-28", pages))
        claims = [c for c in ks.conflicts() if "membership" in c.topic.lower()]
        self.assertEqual(len(claims), 1)
        self.assertIn("179,000", claims[0].resolution)
        self.assertTrue(any("141,000" in v for v in claims[0].values))
        self.assertTrue(any("181,000" in v for v in claims[0].values))

    def test_twin_products_with_the_same_history_are_one_contradiction(self):
        ks = system(snap("01_2026-05-12_a", "2026-05-12", {"/rates": SPECIALS.format(r="3.81", a="3.85", eff="05/01/2026")}),
                    snap("02_2026-09-28_b", "2026-09-28", {"/rates": SPECIALS.format(r="4.06", a="4.10", eff="09/01/2026")}))
        conflicts = ks.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertIn("4.10%", conflicts[0].resolution)
        self.assertNotIn("Business", conflicts[0].topic)

    def test_a_stale_copy_still_online_is_listed_before_a_change_over_time(self):
        calc = """# Certificate calculator

Certificates

| Certificate | Rate | APY |
| --- | --- | --- |
| 6 Month | 3.17% | 3.20% |
"""
        first = {"/rates": SAVINGS_PAGE, "/calculators/certificate": calc}
        second = {"/rates": SAVINGS_PAGE.replace("3.37%", "3.47%").replace("3.40%", "3.50%"), "/calculators/certificate": calc}
        ks = system(snap("01_2026-05-12_a", "2026-05-12", first), snap("02_2026-09-28_b", "2026-09-28", second))
        topics = [c.topic for c in ks.conflicts()]
        self.assertEqual(len(topics), 2)
        self.assertIn("6 Month", topics[0])    # the calculator still shows 3.20% next to the current 3.30%
        self.assertIn("12 Month", topics[1])   # 3.40% -> 3.50% between crawls


class QuestionReading(unittest.TestCase):
    def test_how_many_asks_for_a_count_not_a_rate(self):
        self.assertFalse(parse("How many years has a participation dividend been paid?").rate_intent)
        self.assertTrue(parse("What dividend rate does Share Savings pay?").rate_intent)

    def test_fax_number_needs_a_phone_shaped_answer(self):
        self.assertEqual(parse("What is the fax number?").answer_type, "phone")

    def test_where_on_the_website_and_which_page_ask_for_a_link(self):
        self.assertTrue(parse("Where on the website can I see mortgage rates?").wants_url)
        self.assertTrue(parse("Which page lists the auto loan rates?").wants_url)
        self.assertFalse(parse("Where can I get a car loan?").wants_url)

    def test_ordinal_counts_are_figures(self):
        self.assertEqual(counts("This was the fifth time; it has 22 branches."), ["fifth", "22"])
        self.assertTrue(states_count("This is the eighth consecutive year a dividend has been paid."))
        self.assertFalse(states_count("First-time homebuyers can apply for a grant."))


if __name__ == "__main__":
    unittest.main()
