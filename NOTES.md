Set ANTHROPIC_API_KEY for best results; uses Claude via the Anthropic API.

# NOTES

**API keys: one, optional: `ANTHROPIC_API_KEY`** (the same variable the kit's scorer reads for its
`--judge` mode). With it, the system calls **Claude Haiku 5.5 (`claude-haiku-5-5`) through the
Anthropic API**, a paid API, once per question to understand oddly worded questions (see "AI
question understanding" below); it prints `AI steps: ON (...)` when it loads. Without it, it prints
`AI steps: OFF (no key found ...)` and answers with rules alone, exactly as the previous stage did.
Either way the site's own snapshots are the only source of facts: no call goes to the credit union's
website, and the model never sees or chooses a figure. Dependencies: `snowballstemmer` (pure Python)
and the official `anthropic` SDK.

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
the answer straight from the winning rows. Without a key the same question gives byte-identical text
and sources every time (checked across processes with different hash seeds); with a key, every time
within a run (see "AI question understanding").

## How it decides what is current (the truth rules)

1. **Different identities are different facts.** A different term, balance tier, loan amount,
   model-year slot, product or account kind is never a contradiction, and a row's rate and APY are
   compared metric by metric. Every kind a question names is required: an IRA, business, jumbo or
   refinance question is answered only by facts of that kind (no Roth IRA money market exists, so
   that question is refused instead of answered with certificate rates; "after the intro period"
   asks for the ongoing rate, not the intro one). A product word the question names that the site
   uses ("Bright", "1st Rate", "special") selects that product (an ordinal is one name word however
   it is spelled: "First Rate" is "1st Rate", "Second Chance" is "2nd Chance"); a plainer product
   beats a sibling whose extra name words the question did not use ("12-month certificate" is the
   regular one, not the Student one). When the question names no product, the product of the most
   official page among the close candidates is meant (Investor's Choice from the rate sheet, not a
   promotion's money market).
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
  change with the term" compare products and get today's values; "lower", "higher", "go up",
  "change", "compare" or "different" count as a change only beside a time word ("did", "was",
  "since", "ago", "last", a month or a year, but not a model year); "trend" always asks for the
  history. With a model year in the question, each crawl's slot is the one whose printed years
  contained it, so "a 2023 car since May" correctly goes from the May "2023-26" tier (5.19%) to
  today's "2021-23" tier (5.64%).
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

## AI question understanding (key on)

The rules answer well when a question uses the site's words, and refuse (or misread) slang, typos,
indirect phrasing, head-to-head comparisons and "the auto loan rate in 2025" (a calendar year read as a
vehicle model year). AI is used only for that reading step, and never as the judge of which number is
true:

1. **Restate.** For each question, Claude Haiku 5.5 gets a short instruction (`truthledger/understand_prompt.txt`)
   plus the site's own product names, built from the ledger's fact identities after each ingest (no
   figures). It returns JSON: the question restated in the plain wording the rule parser reads ("whats the
   apy on ur 1 yr cd rn" -> "What is the APY on the 12-month certificate?"), one standalone question per
   product for a comparison, and whether a year is a calendar year or a vehicle model year. The reply is
   validated here: malformed JSON, more than three questions, a year the member never wrote, or any number
   the member did not write (other than a term in years written in months or the reverse, "a year" ->
   12 months, and $15k -> $15,000) and the restatement is thrown away. That last check caught the model
   turning "30-year" into "20-year".
2. **Gate.** The rules answer the member's own words while the AI call is in flight. The restatement
   replaces that answer only when the rules could not read the original: it is a comparison; the rules
   would read a calendar year as a model year; the restatement is a change-over-time question (only when
   the member's own words mention time or change, such as "since May", "did" or "went up") or a rate
   question while the rules read the original as something else; or the rules found nothing. It must keep what the
   rules did read (product family, account kinds such as IRA or jumbo, term, balance or loan amount,
   vehicle model year), it never turns a confident answer into "I don't know" (except a calendar year
   before the first crawl), and it may overrule a refusal only with a rate-table answer (prose matching
   is too loose to beat a refusal: "a 7-year CD?" restated as "an 84-month certificate?" would otherwise
   match a boat-loan sentence). The first unguarded version answered every restatement
   and lowered the public set from 0.920 to 0.796; the gate is why key-on never scores below key-off.
3. **The ledger answers.** The rules answer the restated question(s) exactly as they answer any other:
   the same identities, winners, timelines and truth rules. A comparison is each product answered on its
   own (without the jumbo sibling), then joined, with "The X has the higher APY" when both sides are single
   facts of the same measure. **Final check:** every number in a joined answer must be one the rule-built
   answers stated, otherwise the original question's rule answer is used. The model never writes answer
   text (the `phrase` hook stays off), so no figure in any answer comes from the model.

**Speed.** Thinking off, effort low, the prompt cached (about 2,600 tokens, read from the cache after the
first call), plain JSON instead of the API's structured-output mode (which measured about 0.8 s slower per
call; the reply is validated here instead), no SDK retries, a hard deadline of 1.9 s per question, and
the AI call runs while the rules answer the original wording rather than after it. A request still
unanswered after 0.9 s is sent a second time and the first reply wins (a hedged request), which cuts the
slow tail that decides p95. A check call while loading (untimed) opens the connection
and switches the AI step off with a clear message if the key or network does not work; five failed calls
in a row during answering also switch it off for the run. Measured with the key on over all seven sets
(223 questions, two traced runs): the AI step itself takes 0.83-0.85 s at p50, 1.28-1.39 s at p95, at most
1.91 s; 2-3% of questions hit the deadline and get the rules' answer. The harness p95 per set is
1.01-1.92 s.

**Stability.** Haiku 5.5 does not accept a `temperature` setting (sampling parameters return a 400), so
determinism comes from elsewhere: every question's AI result (including a fallback) is cached for the
life of the process, so a repeat gives the identical answer (repeat consistency 1.00 on every set), and
the rules, not the model, produce every answer. Across separate runs the model can word a restatement
differently; three full key-on runs gave identical scores on every set but v6 (one question, see
Scores).

**Cost.** About $0.02 for a full run of all seven sets (223 questions, about 1.4 calls per question
including hedges and the load-time checks: about 8k uncached input, 16k output and 820k cached input
tokens), about $0.003-0.004 for the 40 public questions, at Haiku 5.5's $0.10 / $0.50 per million
input / output tokens.

**Stage 3 (AI reading at load) was not added.** The questions still missed with the key on are not
table-reading problems: they are prose questions whose right sentence the lexical matcher does not
rank first (the main phone number, mobile wallets, the Platinum Visa late fee, contact-information
updates, Forbes recognition), plus two the model restates too loosely ("helok apr?" loses the product
word, so the refusal stands; "a checking account that actually pays interest" becomes a generic
checking question). Reading every page with a model at load would mean about 1,500 calls per ingest
against the graders' rate limits and would not change how passages are ranked.

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
- `hooks.py` is the seam for optional AI steps (no-op by default); `ai.py` is the Claude
  implementation (question understanding, reply validation, deadline and hedging), with its
  instructions in `understand_prompt.txt`; `system.py` gates its use, answers comparisons and runs
  the final number check.

**The lexical matcher** is a BM25 index over each row's own words (full weight), its FAQ question or
question-like heading (0.8) and its heading/caption/page title (0.4), with Snowball stemming, a
6-letter prefix form for long words, number words ("twelve month" -> 12) and a small generic banking
thesaurus ("CD" -> certificate, "mortgage" -> home loan). Capitalised names in a question must appear
on the answering row or its page. It only finds candidates; the identity card says which fact a
candidate is, and the truth rules say which copy of that fact is true.

## Scores

Run from the repository root after copying the kit's `snapshots_zip/` there (see README.md). All
numbers below are accuracy (`qa_score`) from the kit's own `harness/run_eval.py`; no conflicts gold
ships with the kit, so `final_score` equals accuracy when speed and consistency are 1.0.

The acceptance bar for the AI step: with the key on, beat key-off on the public set and the held-out
reworded sets without lowering any set, with zero stale numbers. With the key off, every answer on the
public set and v1-v5 is byte-identical to the previous stage.

| Question set | Questions | Key off (rules only) | Key on (AI understanding) | p95 latency off / on |
|---|---|---|---|---|
| Public practice set (kit) | 40 | 0.920 | 0.920 | 0.04 / 1.05-1.92 s |
| Held-out v1 (reworded + new) | 40 | 0.967 | 0.967 | 0.05 / 1.58-1.73 s |
| Held-out v2 (rewordings of the public questions) | 25 | 1.000 | 1.000 | 0.04 / 1.15-1.91 s |
| Held-out v3 (new phrasings and facts) | 25 | 0.972 | 0.972 | 0.02 / 1.62-1.91 s |
| Held-out v4 (what changed, relative dates, renamed tiers, IRA / money market) | 34 | 1.000 | 1.000 | 0.07 / 1.01-1.34 s |
| Held-out v5 (fresh rewordings) | 27 | 1.000 | 1.000 | 0.05 / 1.10-1.13 s |
| Held-out v6 (slang, typos, indirect phrasing, comparisons, calendar vs model year) | 32 | 0.344 | **0.844-0.875** | 0.08 / 1.05-1.06 s |
| Kit selftest fixture (with its conflicts gold) | 5 | 1.000 (final 1.000) | 1.000 (final 1.000) | <0.01 / 0.94 s |

Key-on figures are from three full runs of the final code: identical scores on every set except v6,
where one run lost one question (the model wrote "Regular IRA" for "Roth IRA", the faithfulness check
rejected the restatement, and the rules refused the original wording rather than answer a different
tier); the latency range covers all three. Zero stale-trap hits and zero errors on every set, consistency 1.00 on every
set. Ingest of all five snapshots takes about 17 s without a key and about 22 s with one (a check
call after each snapshot).

**The kit's judge mode** (`--judge`, Claude Opus 5.5 grading each answer against the full reference,
public set): accuracy 0.914 with the key on and 0.914 with it off (the gate leaves the public answers
unchanged). In judge mode the kit's runner times the judge's own call inside each question's latency,
so its p95 (4.2 s) is not the system's latency; final score 0.839 with the key on, 0.847 off (speed is
the only difference), with the 9-item conflicts guess list.

**v6 was written for this stage before any AI code** and run blind: **0.3125 with the key off** (no
trap hits; slang and typos refused, comparisons answered on one side or refused, "the auto loan rate in
2025" answered with today's model-year rates) and **0.859 with the key on** on the first, ungated
version. The key-off v6 score is now 0.344 because the generic "before the first crawl" refusal no
longer prints a date (a refusal with a number fails the kit's refusal check). Later key-on runs are
not blind: the gate, the reply checks and the prompt's wording were tuned on these sets, and the
remaining v6 misses are listed under Stage 3 above.

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

- **Free-text paraphrase in prose questions.** Matching is lexical plus a thesaurus, and the AI step
  does not change prose answers the rules already give (see the gate). "What recognition has Dupaco
  received from Forbes?" lands on press-release boilerplate instead of "the fifth time Forbes recognized
  Dupaco" (the reworded "How many times has Forbes recognized Dupaco?" is answered correctly); "How can
  I update my contact information?", "What is Dupaco's main phone number?", "Which digital wallets can
  I load my cards into?" and the Platinum Visa late fee (inside a long fine-print sentence) return a
  related passage instead, with or without the key.
- **Without a key**, slang, typos, comparisons and a calendar year next to a vehicle word ("the auto loan
  rate in 2025" is read as a model year) are handled as in the previous stage; with the key, the AI step
  covers them (v6: 0.344 off, 0.844-0.875 on).
- **AI step variance.** Across separate runs the model may word a restatement differently; within a run
  every answer is cached. A question whose AI call misses the 1.9 s deadline (2-3% measured) gets the
  rules' answer to the original wording.
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

## Other AI seams (not used)

`truthledger/hooks.py` also defines `extra_rows(page)` (turn messy prose or tables into extra ledger rows
at ingest; they would get identity cards and go through the same truth rules) and `phrase(question,
text)` (reword the final answer, kept only if it states exactly the same figures as the rule-built
answer). Neither is switched on: see "Stage 3" above, and rule-built wording keeps answers fast and
identical.

## Data handling

The snapshots and the assignment PDF are not committed (`snapshots/0*/` and `snapshots_zip/` are
git-ignored). The held-out question files and the conflicts guess list contain only questions and the
short values they check.
