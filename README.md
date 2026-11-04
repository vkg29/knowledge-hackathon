# knowledge-hackathon

A knowledge system that answers credit-union member questions from dated website snapshots with the
**current** value, the right tier, a source, and an honest "I don't know" when the site does not say.
It is a submission for the interface.ai take-home *"Build a knowledge system that knows what's true now"*.

- **The submission** is [`solution/`](solution/): `solution.py` exposes `build()` following
  [`harness/contract.py`](harness/contract.py); the code lives in [`solution/truthledger/`](solution/truthledger/).
- **How it decides what is current, its scores and known gaps:** [`NOTES.md`](NOTES.md).
- No API key is needed; the only dependency is a small pure-Python stemmer.

## Run it

The website snapshots are not in this repository (they are not ours to redistribute). Copy the kit's
`snapshots_zip/` folder (the five `0*.zip` files) into the repository root, then:

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python harness/run_eval.py --submission solution --eval eval/public_eval.jsonl
python harness/run_eval.py --submission solution --eval eval/heldout_eval.jsonl      # our held-out set 1
python harness/run_eval.py --submission solution --eval eval/heldout_v2_eval.jsonl   # set 2: rewordings of the public questions
python harness/run_eval.py --submission solution --eval eval/heldout_v3_eval.jsonl   # set 3: new phrasings and facts
python harness/run_eval.py --submission solution --eval eval/heldout_v4_eval.jsonl   # set 4: what changed, relative dates, renamed tiers
python harness/run_eval.py --submission solution --eval eval/heldout_v5_eval.jsonl   # set 5: fresh rewordings
python -m unittest discover -s solution/tests -v                                     # unit tests
```

The first eval run unpacks the zips into `snapshots/` (ignored by git). Add
`--conflicts-gold <file>` to score `conflicts()` against a gold list, for example
`--conflicts-gold eval/conflicts_guess.json` (our 9-item guess; the kit ships no gold for the real data),
and run the kit's own fixture with
`python harness/run_eval.py --submission solution --snapshots harness/selftest/snapshots --eval harness/selftest/eval.jsonl --conflicts-gold harness/selftest/conflicts_gold.json`.

CI (`.github/workflows/ci.yml`) runs the unit tests and `ruff check .` on Python 3.11; the eval sets
need the snapshots, so they run locally only.

## Layout

```
solution/            the submission (solution.py, truthledger/ package, tests/, requirements.txt)
harness/             the kit's scorer and contract (unchanged)
eval/                public_eval.jsonl (kit) + heldout_eval.jsonl, heldout_v2..v5_eval.jsonl and conflicts_guess.json (ours)
starter/             the kit's naive starter (unchanged)
snapshots/README.md  what each snapshot is (the snapshot folders themselves are not committed)
```

---

## Participant kit README (as shipped with the kit)

### Hackathon: Build a Knowledge System That Knows What's True *Now*

#### The problem in one minute

Our assistant answers credit-union members' questions ("What's the 6-month certificate rate?", "How much is a lost debit card?") using the credit union's own website.

We download (crawl) that website again and again over time. Each download is a **snapshot**. Websites are messy:

- A rate changes, but an old page or PDF still shows the old number.
- A promotion ends, but its page is never taken down.
- One page lists the **rate**, another the **APY**, and they look like they disagree.
- A table has tiers (by balance, by term). Read it carelessly and every tier looks like a conflict.
- Old newsletters and press releases describe offers from years ago.

When we combined these snapshots, our system **gave members wrong or out-of-date answers**. Every eval question in this kit comes from a real problem like that.

**Your mission:** build a system that reads the snapshots in date order and answers questions the way a careful human expert would: with the **current** value, the **right** tier, a **source**, and an honest "I don't know" when the site doesn't say.

#### What's in the box

```
snapshots_zip/           the five website downloads, one zip per date (oldest first)
snapshots/README.md      what each download is and what changed between them
eval/public_eval.jsonl   40 practice questions with expected answers
starter/solution.py      a working (but naive) solution to copy from
harness/                 the scorer we will use (same one you can run)
```

**You don't need to unzip anything.** The first time you run the scorer (below), it unpacks the zips into `snapshots/`, with one folder per download, each holding a `manifest.json` (url, title, date) and the pages as `pages/*.md`. After that the folders are there to browse. If you'd rather look first, unzip them yourself into `snapshots/`; the scorer leaves existing folders alone.

#### What you must hand in

A folder with a file called `solution.py`, containing a `build()` function that returns an object with **three methods**:

| Method | What it does |
|---|---|
| `ingest(snapshot)` | We call it once per snapshot, oldest first. Learn from it. |
| `answer(question, as_of=None)` | Answer a member's question. Without `as_of`, answer from the newest snapshot. With a date, answer as the website stood on that day. |
| `conflicts()` | List every place where the website contradicts itself, which value you believe is right, and why. |

The exact shapes are defined in `harness/contract.py`; `starter/solution.py` already follows them. **Don't rename anything.** We plug your folder straight into our scorer.

You can use any tools you like inside: search, embeddings, a graph, an LLM, plain rules. Add a `requirements.txt` if you need packages, and say in a short `NOTES.md` which API keys (if any) you need.

#### How to check your work

From the top folder of this kit, run:

```bash
python harness/run_eval.py --submission starter --eval eval/public_eval.jsonl
```

The first run takes a few extra seconds while it unpacks the snapshots. Then it loads all five downloads into the starter, asks the 40 practice questions, and prints your score. To test your own solution, point `--submission` at your folder.

The starter scores low on purpose. Watch your score go up as you improve. Needs Python 3.9 or newer.

#### How you will be scored

We run a **larger, hidden question set** with the same format and the same scorer. Four things count:

| | Weight | What we look for |
|---|---|---|
| **Accuracy** | 55% | The current, correctly-tiered value is in your answer. Repeating an old or wrong value scores **zero** for that question, even if the right value is also there, so hedging by listing every number doesn't pay. Citing the right page earns a little extra. When the site doesn't say, the right answer is "I don't know". |
| **Conflicts** | 15% | How many real contradictions you list in `conflicts()`, and whether you picked the right winner. |
| **Consistency** | 15% | The same question asked in different words gets the same answer, and asking twice gives the same answer. |
| **Speed** | 15% | How quickly you answer. Most answers in 2 seconds or less earns full marks; it drops to zero at 20 seconds. |

Speed and consistency are **multiplied by your accuracy**: a fast or consistent system that's wrong doesn't earn those points. Ingest (loading the snapshots) isn't timed for scoring, but must finish within 30 minutes.

Hidden questions include re-worded versions of the public ones, "as of" date questions, and questions about things that *changed* between snapshots.

#### A simple way to approach it

1. **Read the snapshots yourself first.** (Run the scorer once to unpack them, or unzip `snapshots_zip/`.) Pick five topics (a rate, a fee, branch hours) and follow them across the dates. You'll quickly see the kinds of mess.
2. **Keep history, not just the latest copy.** Remember *when* you saw each fact and *where*.
3. **Decide what "current" means.** Is the newer page right? Does an official rate sheet beat a blog post? What about an expired promotion? Write your rules down.
4. **Respect qualifiers.** "6-month" vs "12-month", "rate" vs "APY", "Prime Checking" vs "Prime Business Checking". These are different facts, not conflicts.
5. **Be honest.** If the website doesn't say, don't guess.
6. **Run the public eval often.** Read the questions you miss, not just the score.

#### Rules

- Use only the snapshots provided to answer questions. No live website calls.
- Each `answer` call must finish within 60 seconds.
- The data is a real credit union's public website, shared for this event only. Don't redistribute it.
