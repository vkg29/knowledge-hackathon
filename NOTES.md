# NOTES

**API keys: none.** The system runs entirely on the machine, makes no network calls (not to the
credit union's site, not to any model service), and downloads nothing at ingest or answer time. Its
only dependency is `snowballstemmer` (pure Python). Optional AI steps are a planned follow-up (see
the end of this file); without them, and today, the system works on rules alone.

## The idea: the ledger is the judge

While loading, every page of every snapshot is turned into **ledger rows**: one row per rate-table
row (product, caption, qualifiers such as term, balance tier, loan amount, model-year range or
variant, every value column such as dividend rate / APY / rate / APR / fee / minimum to open, and the
"Rates are effective as of" date printed under the table), one row per "Label: value" line, one row
per branch-hours block, and one row per prose sentence or bullet (kept with its heading path, FAQ
question and page). Each row remembers its page, the page kind, the page's printed byline date
(news, blog) and the snapshot it came from. History is never thrown away: rows are stored once per
distinct page version, and the site *as it stood on a date* is the newest version of every page at or
before that date (a page that drops out of a later crawl keeps its last content).

Answering never asks anything to "pick the right number". It finds the rows that are about the
question, lets the written truth rules below decide which of them is true, and phrases the answer
straight from the winning row(s), quoting nothing else. The same question therefore gives
byte-identical text and sources every time (checked across processes with different hash seeds).

## How it decides what is current (the truth rules)

1. **Qualifiers first.** Different term, balance tier, loan amount, model-year range, variant
   (jumbo, IRA, Roth/Traditional, business, refinance, intro) or metric (rate vs APY vs APR) are
   *different facts*, never a contradiction. A question that names a term, model year or dollar
   amount only matches rows that carry a matching one (the narrowest tier that contains the
   amount); rows with variants the question did not ask for are demoted, so "17-month special" means
   the regular special, not the jumbo or IRA one.
2. **Ended things are not current.** Rows from a page that says the offer has ended, and rows that
   state their own past end date ("offer ended 2025-09-30"), drop out of current answers. They still
   answer "as of" questions about the time they were live.
3. **Authority.** An official page with a dated rate table beats an official product page, which
   beats a promotion landing page, a downloaded document (PDF), a press release, a blog post, and
   finally a calculator (calculators embed rate tables that are rarely refreshed).
4. **Recency among equals.** The date printed on the page wins: "Rates are effective as of …" for
   tables, the byline date for press releases and blog posts. Then the crawl that last saw it.
5. **Repeated statements.** The same sentence printed with different figures (press-release
   boilerplate such as "It has over 600 employees and N branch office locations", or "the fifth time
   Forbes recognized Dupaco") is one fact; the newest dated copy wins.
6. **Say so when the site does not.** If no row covers the question (its names, terms and subject
   words), the answer is a digit-free "that isn't listed … please contact Dupaco" with
   `confident=False`, instead of the nearest guess.

`conflicts()` uses the same rules: one entry per product-and-term whose same tier was printed with
different values (across pages in one crawl or across crawls), plus boilerplate statements repeated
on five or more dated pages (or ordinal counts like "fourth"/"fifth" time) with different figures. Each
entry lists every value seen, the value the rules pick for the newest snapshot, and why (which rule
decided). Tiers and rate-vs-APY columns are never reported. On the full data it reports 28 conflicts.

## Architecture

`solution/solution.py` builds `truthledger.system.LedgerKnowledge`. The package is small and
layered:

- `extract.py` (page → rows) and `qualifiers.py` (term, years, amount, variants for rows *and*
  questions, so both sides speak one vocabulary), `pages.py` (page kind, byline date).
- `ledger.py` stores rows per page version and builds the "as of" views; `rules.py` holds the truth
  rules; `conflicts.py` builds the contradiction report.
- `query.py` parses a question (subject words, names, qualifiers, metric, and intent: rate, list,
  superlative, hours, link, expected answer shape such as an address, phone, routing number or date);
  `matcher.py` is the local meaning matcher; `answer.py` routes the question (rate table, prose,
  hours, link), applies the rules and phrases the result.
- `hooks.py` is the seam for optional AI steps (no-op by default).

**The meaning matcher** is a BM25 index over each row's own words (full weight), its FAQ question
(0.8) and its heading/caption/page title (0.4), with Snowball stemming, a 6-letter prefix form for
long words ("recognition" ↔ "recognized"), number words ("twelve month" → 12), and a small generic
banking thesaurus ("CD" → certificate, "mortgage" → home loan, "shut" → close). Capitalised names in
a question (places, brands, programs) must appear on the answering row or its page. All of this runs
in well under a tenth of a second per question.

I compared it against a hybrid that adds a local embedding model (BAAI/bge-small-en-v1.5, ONNX via
`fastembed`, no key, one-time ~67 MB download): embedding all 25,668 distinct row texts took 358 s
of ingest, and with the embedding boost at two weights the scores were **unchanged** on every set
(public 0.8655, held-out v1 0.9669, held-out v2 1.0 at both weights) while p95 latency rose from
0.06 s to 0.10 s. A static model2vec model was worse on spot checks. Because it did not raise either
score, the embedding model is not shipped.

## Scores

Run from the repository root after copying the kit's `snapshots_zip/` there:

```bash
python harness/run_eval.py --submission solution --eval eval/public_eval.jsonl
python harness/run_eval.py --submission solution --eval eval/heldout_eval.jsonl
python harness/run_eval.py --submission solution --eval eval/heldout_v2_eval.jsonl
python harness/run_eval.py --submission solution --eval eval/heldout_v3_eval.jsonl
python -m unittest discover -s solution/tests -v
```

| Question set | Questions | Accuracy (qa_score) | Consistency | p95 latency |
|---|---|---|---|---|
| Public practice set (kit) | 40 | **0.866** | 1.00 | 0.06 s |
| Held-out v1 (ours: reworded + new) | 40 | 0.967 | 1.00 | 0.05 s |
| Held-out v2 (ours: rewordings of the public questions) | 25 | 1.000 | 1.00 | 0.04 s |
| Held-out v3 (ours: new phrasings and facts) | 25 | 0.972 | 1.00 | 0.02 s |
| Kit selftest fixture (with its conflicts gold) | 5 | 1.000 (final 1.000) | 1.00 | <0.01 s |

Ingest of all five snapshots takes about 15 s. Without a conflicts gold file the harness reports
`final_score` = accuracy (0.8655 on the public set), since speed and consistency are both 1.0.

**Read the held-out numbers with care.** Each held-out set was written before the fixes it later
guided, and its *first* run is the honest blind number: v1 scored 0.679 on first run, v2 0.663,
v3 0.819 (v3 was written last, after all other tuning). The fixes those runs prompted were generic
(intent wording, number words, acronym names, scaled amounts like "$2 million", answer shapes), and
were kept only when they did not lower any other set, but the later numbers above are no longer blind.
Expect the hidden set to land between the blind first-run figures and the public score.

The conflicts pillar cannot be scored locally (no gold list ships with the kit). Against a 6-item
guess list I wrote from the practice questions (60-month auto, 30-year mortgage, branch count,
Forbes, 10-month jumbo special, 12-month certificate), all 6 match with the right winner; precision
against the real gold is unknown.

Score progression during development: public 0.68 → 0.74 → 0.78 → 0.84 → 0.87 as the extractor,
truth rules and question routing improved (details in the commit history).

## Known gaps by question category

- **conflict_resolution / eligibility:** "Who can join?" answers with the join page's list ("live
  or work within our charter area …") but the states (Iowa, Illinois, Wisconsin) live on other pages;
  joining two passages is not implemented.
- **current_value:** "How can I update my contact information?" has no direct answer on the site; the
  system returns a related passage instead of the phone number the graders expect. Fine print with
  many clauses in one sentence (e.g. the Platinum Visa late fee) is hard to match lexically.
- **temporal_as_of:** relative dates in the question text ("last year") are not resolved; only
  explicit "as of"/"in May 2026" style dates and the `as_of` argument are used.
- **stale_trap:** an open question like "What recognition has Dupaco received from Forbes?" can land
  on press-release boilerplate instead of the newest Forbes release (a reworded "How many times has
  Forbes recognized Dupaco?" is answered correctly).
- **list_completeness:** the practice item "Get me the rate for the certificate of accounts" has no
  `must_include`, so it scores 0 for any answer under the kit's scorer.
- **Paraphrase in general:** matching is lexical plus a thesaurus. The blind first-run scores show
  that unseen wordings still miss sometimes; an AI question-rewriting step is the planned fix.
- **Branch hours** exist only inside press releases about individual branches, so general hours are
  summarised from those; a branch with no published hours is refused rather than guessed.

## Optional AI steps (planned, measured follow-up)

`truthledger/hooks.py` defines where an LLM can help without ever judging which number is true:
`extra_rows(page)` turns messy prose into extra ledger rows at ingest (they go through the same truth
rules), `rewrite_question(question)` restates oddly worded questions, and `phrase(question, text)`
rewords the final answer, which is only kept if it states exactly the same figures as the rule-built
answer. No implementation ships yet because no API key was available to measure one; each step will
be kept only if it raises the score on both the public and the held-out sets, and graders would
switch it on with their own key (named in this file when added).

## Data handling

The snapshots and the assignment PDF are not committed (`snapshots/0*/` and `snapshots_zip/` are
git-ignored). The held-out question files contain only questions and the short values they check.
