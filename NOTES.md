# NOTES

**API keys: none.** The system runs entirely on the machine, makes no network calls (not to the
credit union's site, not to any model service), and downloads nothing at ingest or answer time. Its
only dependency is `snowballstemmer` (pure Python). Optional AI steps are planned for later stages
(see the end of this file); today the system works on rules alone.

## The idea: identity first, then a timeline, then the truth rules

While loading, every page of every snapshot is turned into **ledger rows**: one row per rate-table
row (label, caption, the in-page tab it sits under, qualifiers such as term, balance tier, loan
amount or model-year range, every value column such as dividend rate / APY / rate / APR / fee, and
the "Rates are effective as of" date printed under the table), one row per "Label: value" line, one
row per branch-hours block, and one row per prose sentence or bullet (kept with its heading path and
page). Each row remembers its page, the page kind, the page's byline date (news, blog) and the
snapshot it came from. Nothing is thrown away: the site *as it stood on a date* is the newest version
of every page at or before that date.

Then, still at load time, three things are computed once for the whole ledger:

1. **A fixed identity card for every rate fact** (`identity.py`): the catalogue family (certificate,
   savings, money market, checking, mortgage, auto loan, credit card, ... from a generic
   credit-union catalogue in `catalogue.py`), the product's distinctive name words inside that
   family ("share", "bright", "special", "platinum", "investor choice", "1st"; none for the plain
   product such as regular certificates), the account kinds that make it a different product (IRA,
   Roth, Traditional, business, refinance, intro, jumbo), the term, and the tier (a normalised
   balance or loan-amount band, or a model-year *slot*). Exact rules come first: the row's own label
   names the product when it names a family ("Bright Savings"), else the nearest caption ("Rewards
   Visa" over a row labelled "Standard", "Investor's Choice" over "$25,000+"), else the tab; the
   nearest caption decides the family before the tab, so "High-Yield Savings" under a "Money Market
   Account Rates" tab is savings and a table captioned only "Regular" under "Term-Share Certificate
   Rates" is a certificate. Looser rules come second: a table whose caption names no product inherits
   the name its own page printed for the same table in another crawl (the May sheet's unnamed
   checking table is September's "1st Rate Checking"); a shorter name joins the one fuller name of
   its family that contains it; an unnamed balance-tier table joins the one named product that prints
   all of its dollar bands (a landing page's "Checking Accounts" table is 1st Rate Checking).
   **Model-year tiers are aligned by position, not by their printed years:** the newest-years row of
   a table is slot 0 in every crawl, so "2023-26 Model Year" renamed to "2024-27 Model Year" stays one
   fact, and a calculator still printing "2023-26" is an older copy of that same fact. A question's
   model year is mapped onto the slot whose *current, winning* row prints it.
2. **Winners per snapshot** (`timeline.py`): for every identity and every crawl, the truth rules
   below pick one row from *every* live row of that identity in the site as it stood then. The
   winner never depends on which rows a question happens to pull up, so rewordings cannot group
   copies differently.
3. **A timeline per identity**: consecutive crawls whose winners state the same figures form one span,
   valid from the date the winning page prints ("effective 06/02/2026"), else from the crawl that
   first showed it, until the next span starts or still current. "What changed since May?" and "what
   did it pay before it went up?" are lookups on these histories.

Answering never asks anything to "pick the right number". It parses the question onto the same
fields (family, product words, kinds, term, model year, amount, metric, the date asked about), uses
the lexical matcher only to *find* candidate identities, takes each identity's winner, and phrases
the answer straight from the winning rows. The same question gives byte-identical text and sources
every time (checked across processes with different hash seeds).

## How it decides what is current (the truth rules)

1. **Different identities are different facts.** A different term, balance tier, loan amount,
   model-year slot, product or account kind is never a contradiction, and a row's rate and APY are
   compared metric by metric. Every kind a question names is required: an IRA, business, jumbo or
   refinance question is answered only by facts of that kind (no Roth IRA money market exists, so
   that question is refused instead of answered with certificate rates; "after the intro period"
   asks for the ongoing rate, not the intro one). A product word the question names that the site
   uses ("Bright", "1st Rate", "special") selects that product (an ordinal is one name word however
   it is spelled: "First Rate" is "1st Rate", "Second Chance" is "2nd Chance"); a plainer product beats a sibling
   whose extra name words the question did not use ("12-month certificate" is the regular one, not the
   Student one). When the question names no product, the product of the most official page among
   the close candidates is meant (Investor's Choice from the rate sheet, not a promotion's money
   market).
2. **Ended things are not current.** Rows from a page that says the offer has ended, and rows that
   state their own past end date, drop out of current answers; they still answer "as of" questions
   about the time they were live. A fact that no crawl lists any more is reported as no longer
   offered, with its last value only when the question asks for it.
3. **Authority.** An official page with a dated rate table beats an official product page, which
   beats a promotion landing page, a downloaded document (PDF), a press release, a blog post, and
   finally a calculator (calculators embed rate tables that are rarely refreshed).
4. **Recency among equals.** The date printed on the page wins ("Rates are effective as of ...", or
   a press release's byline date); then a copy the crawl itself saw beats one carried forward from an
   older crawl; then, among equally official and dated copies, the page about that product family
   (the home-loans page for a mortgage row) is cited before a page that embeds the same table.
5. **Repeated statements.** The same sentence printed with different figures (press-release
   boilerplate such as "It has over 600 employees and N branch office locations", or "the fifth time
   Forbes recognized Dupaco") is one fact; the newest dated copy wins. The same statement about
   *different years* ("294 employees used VTO in 2023", "256 ... in 2024") is separate facts: the year
   is a qualifier, and a question about a year the site does not cover ("last year" = 2025) gets each
   year it does state, newest first.
6. **Say so when the site does not.** If no row covers the question (its names, terms and subject
   words), the answer is a digit-free "that isn't listed ... please contact Dupaco" with
   `confident=False`, instead of the nearest guess.

## What the answers contain

- **Complete facts for the identity asked.** A current answer about a named special states its jumbo
  version too ("17 Month Fixed Certificate Special 4.10% APY; Jumbo 4.20% APY"), unless the jumbo
  figure equals one the asked special itself showed at another time. "As of" answers state the asked
  size only, because old sibling figures read like the asked product's other dated values. Tiered
  products list every tier the question did not narrow ("the 30-year rate" gives all four loan-amount
  tiers); an IRA question gets both the Roth and the Traditional version.
- **What changed / before / since / still offered.** "How has the 17-month special changed since June
  2026?" follows the facts asked for through every crawl and says "3.85% APY (effective June 2, 2026),
  then 3.95% (effective July 14, 2026), then 4.10% (effective September 1, 2026), which is the
  current rate"; "before its most recent increase", "when did it last change", "between May and
  September" and "is it still offered" are read from the same history. A superseded figure is always
  stated as a past one, with the date it changed. Only wording about time makes a question a
  change question: "lower than the jumbo", "does it go up with a bigger balance" or "how does it
  change with the term" compare products and get today's values; "lower", "go up" or "change" count
  as a change only beside a time word ("did", "was", "since", "ago", "last", a year). With a model year in the question, each crawl's
  slot is the one whose printed years contained it, so "a 2023 car since May" correctly goes from the
  May "2023-26" tier (5.19%) to today's "2021-23" tier (5.64%).
- **Relative dates** count back from the newest crawl (28 Sep 2026): "last month" is 31 Aug, "two
  months ago" is 28 Jul, "this past May" or "in May" is 31 May 2026, "last year" is 2025, and "over
  the past year" or "in the last year" is the twelve months up to the newest crawl, read as a history.
  A rate question about a year before the first crawl says the site is not known for that year.
- **Two passages joined.** An answer that defines an area only by name ("live or work within our
  charter area") is joined with the sentence on the same page that names the places ("You can find us
  in Iowa, Illinois and Wisconsin"). Membership-eligibility questions go straight to the section of the
  site that says who may join, read from its first line.

`conflicts()` uses the same identities and timelines: **one entry per fact identity** whose copies
disagree, on two pages at once or across crawls (a rate that changed over time is listed with its
winner). The values are only that identity's own figures, newest first; the resolution is the single
value-set the rules pick for the newest snapshot (for example "3.86% dividend rate / 3.90% APY"); the
reason names the rule that decided and the dated history of the winning figure. A copy that only
repeats part of another ("15.50%" of "15.50% - 21.00%") is not a contradiction. Identical histories
under twin products (a special and its IRA copy) are reported once. Boilerplate statements repeated on
five or more dated pages (or ordinal counts like "fourth"/"fifth" time) with different figures are
listed too. On the full data it reports 39 entries.

## Architecture

`solution/solution.py` builds `truthledger.system.LedgerKnowledge` (the public contract in
`harness/contract.py` is unchanged). The package is layered:

- `extract.py` (page -> rows, with table index and tab label) and `qualifiers.py` (term, years,
  amount, variants for rows *and* questions), `pages.py` (page kind, byline date).
- `catalogue.py` (generic credit-union product families and product name words) and `identity.py`
  (identity cards, lineage across crawls, model-year slots), computed once per ingest.
- `ledger.py` stores rows per page version and builds the "as of" views; `timeline.py` holds the
  per-snapshot winners and per-identity spans; `rules.py` holds the truth rules; `conflicts.py`
  builds the contradiction report.
- `query.py` parses a question (subject words, names, family, product words, kinds, qualifiers,
  metric, change intent and period, the year or date asked about, expected answer shape);
  `matcher.py` is the local lexical matcher; `answer.py` routes the question (rate table, change
  history, eligibility, prose, hours, link), applies the rules and phrases the result.
- `hooks.py` is the seam for optional AI steps (no-op by default).

**The lexical matcher** is a BM25 index over each row's own words (full weight), its FAQ question or
question-like heading (0.8) and its heading/caption/page title (0.4), with Snowball stemming, a
6-letter prefix form for long words, number words ("twelve month" -> 12) and a small generic banking
thesaurus ("CD" -> certificate, "mortgage" -> home loan). Capitalised names in a question must appear
on the answering row or its page. It only finds candidates; the identity card says which fact a
candidate is, and the truth rules say which copy of that fact is true.

## Scores

Run from the repository root after copying the kit's `snapshots_zip/` there (see README.md). All
numbers below are from the kit's own `harness/run_eval.py`; no conflicts gold ships with the kit, so
`final_score` equals accuracy when speed and consistency are 1.0.

The acceptance bar for this stage: beat the first version on the public practice set, no regression on
any held-out set, and zero stale numbers. Ties on v1, v2 and v3 meet it (v2 is already at the 1.000 ceiling).

| Question set | Questions | Merged first version | This stage | Consistency | p95 latency |
|---|---|---|---|---|---|
| Public practice set (kit) | 40 | 0.866 | **0.920** | 1.00 | 0.04 s |
| Held-out v1 (reworded + new) | 40 | 0.967 | 0.967 | 1.00 | 0.05 s |
| Held-out v2 (rewordings of the public questions) | 25 | 1.000 | 1.000 | 1.00 | 0.04 s |
| Held-out v3 (new phrasings and facts) | 25 | 0.972 | 0.972 | 1.00 | 0.02 s |
| Held-out v4 (what changed, relative dates, renamed tiers, IRA / money market) | 34 | 0.368 | 1.000 | 1.00 | 0.07 s |
| Held-out v5 (fresh rewordings, the final check) | 27 | not run | 1.000 | 1.00 | 0.04 s |
| Kit selftest fixture (with its conflicts gold) | 5 | 1.000 | 1.000 (final 1.000) | 1.00 | <0.01 s |

Zero stale-trap hits and zero errors on every set. Ingest of all five snapshots takes about 17 s.

**Read the held-out numbers with care: only the first run of each set is blind.** v1 scored 0.679 on
its first run, v2 0.663, v3 0.819 (all three against the first version, in PR 1). v4 was written for
this stage *before* any of its code and scored **0.368** blind on the merged first version (3 trap
hits: it could not answer what-changed, relative-date or renamed-tier questions). v5 was written
after the redesign and before its last fixes, and scored **0.815** blind (1 trap hit, "after the
intro period" read as asking for the intro rate). The fixes those runs prompted are generic (filler
verbs such as "advertise", the official product first when none is named, the negated intro, the
eligibility route) and were kept only when they lowered no other set, but the later v4 and v5
numbers are no longer blind. Expect the hidden set to land between the blind first runs and the
public score.

The conflicts pillar cannot be scored locally against the real gold. Against `eval/conflicts_guess.json`,
a 9-item guess list written from the practice questions (60- and 84-month auto, 30-year mortgage,
branch count, Forbes, 10-month jumbo special, 12-month certificate, 17-month special, 1st Rate
Checking), all 9 are matched and 8 have the right winner; the miss is the scorer pairing the 84-month
guess with the 72-month entry through one shared figure plus a topic word. Precision against the real
gold is unknown (39 entries submitted).

## Known gaps

- **Free-text paraphrase.** Matching is lexical plus a thesaurus. Questions whose words the site does
  not use still miss: "What recognition has Dupaco received from Forbes?" lands on press-release
  boilerplate instead of "the fifth time Forbes recognized Dupaco" (the reworded "How many times has
  Forbes recognized Dupaco?" is answered correctly); "How can I update my contact information?",
  "What is Dupaco's main phone number?", "Which digital wallets can I load my cards into?" and the
  Platinum Visa late fee (inside a long fine-print sentence) return a related passage instead. An AI
  question-understanding step is the next stage's fix.
- **Source credit.** Some correct answers cite a page other than the one the graders list (the
  routing number from the contact page rather than the cash-management page; general lobby hours
  from branch press releases rather than the home page).
- **List answers** stop at 12 rows, and a change history at 8 lines; a "what changed with mortgage
  rates since May?" answer lists every purchase tier's full history, which is complete but long.
- **"As of" answers do not add the jumbo sibling** (see above), so a judge grading against a reference
  that mentions both sizes for a past date would give partial credit.
- **Extraction artefacts** remain in a few long value cells (the May HELOC intro cell reads as a
  sentence with a footnote digit), and a few blog tables get junk identity cards; they hold no rate
  figures that compete with official ones.
- **Branch hours** exist only inside press releases about individual branches, so general hours are
  summarised from those; a branch with no published hours is refused rather than guessed.
- The practice item "Get me the rate for the certificate of accounts" has no `must_include`, so it
  scores 0 for any answer under the kit's scorer (it lists the personal certificate rates).

## Optional AI steps (later stages)

`truthledger/hooks.py` defines where an LLM can help without ever judging which number is true:
`rewrite_question(question)` restates oddly worded questions onto the identity fields,
`extra_rows(page)` turns messy prose or tables into extra ledger rows at ingest (they get identity
cards and go through the same truth rules), and `phrase(question, text)` rewords the final answer,
which is only kept if it states exactly the same figures as the rule-built answer. Nothing ships yet;
each step will be kept only if it raises the score on the public and the held-out sets, using the
graders' Anthropic key when one is set and falling back to these rules when it is not.

## Data handling

The snapshots and the assignment PDF are not committed (`snapshots/0*/` and `snapshots_zip/` are
git-ignored). The held-out question files and the conflicts guess list contain only questions and the
short values they check.
