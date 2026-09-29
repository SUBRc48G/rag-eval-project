# Testing the real Tuitor app

This folder tests **your real Tuitor "Chat Tutor"** with the same evals the rest of this project uses, so the metric
names and scoring are identical (`pipeline.contextual_relevancy.avg_score`, `safety.scope.pass_rate`, ...).
It is fully separate: it doesn't change any other file in the project, doesn't write to the tracker or baselines, and
doesn't upload anything to Confident AI. Everything it creates stays in this folder.

## One small file per eval (same names as the main project's `evals/`)

Run any of them on its own, from the project folder:

| File | What it checks |
|---|---|
| `check_tuitor.py` | Does it connect? Asks a few real questions (no scoring, ~1 cent). **Start here.** |
| `eval_retriever.py` | Is the text Tuitor looks up the right text? (Contextual Recall / Precision) |
| `eval_generator.py` | Given good text, does Tuitor's prompt make it answer faithfully? |
| `eval_rag_pipeline.py` | The whole chain: is what it looked up on topic, and does the answer stick to it? |
| `eval_application.py` | Are the answers correct and complete compared with the expected answers? |
| `eval_safety.py` | Scope (stays a tutor), leakage (keeps instructions private, no contact details), toxicity. `--only scope` etc. |
| `eval_ops.py` | Speed, cost and reliability of Tuitor (no judge). Close other programs first. |
| `run_tuitor_test.py` | Only chains several of the above into ONE report (`--only pipeline safety`). |
| `run_suite.py` | **The final regression suite**: runs every part above, saves a snapshot, and compares it with the baseline. |
| `compare.py` | The regression decision (PASS / REVIEW / FAIL) between two snapshots. |
| `tuitor_pipeline.py` | Tuitor's real answering steps, copied from `Tuitor.py`. |
| `common.py` | The small shared setup (isolation, retries, report writing). |

## Commands

    uv run python tuitor_eval/check_tuitor.py

    uv run python tuitor_eval/eval_rag_pipeline.py --limit 5
    uv run python tuitor_eval/eval_rag_pipeline.py
    uv run python tuitor_eval/eval_safety.py
    uv run python tuitor_eval/eval_safety.py --only scope toxicity
    uv run python tuitor_eval/eval_retriever.py --limit 5
    uv run python tuitor_eval/eval_generator.py --limit 5
    uv run python tuitor_eval/eval_application.py --limit 5

    uv run python tuitor_eval/run_tuitor_test.py --only pipeline safety      # several at once, one report

`--limit N` scores only the first N questions (cheaper). The first run makes a private copy of Tuitor's PDF data (~70 MB) in
`tuitor_eval/data/chroma_db` (the same layout Tuitor uses); your live Tuitor data is only read, never changed.

## The regression suite (run it whenever Tuitor changes)

Think of the **baseline** as a reference photo of how Tuitor performs today. After you change something (the prompt, the search
settings, the model, the PDFs), run the suite again and it compares the new run against the photo.

    uv run python tuitor_eval/run_suite.py --baseline --label "Tuitor v1"       # once: take the reference photo (needs the full question sets)
    uv run python tuitor_eval/run_suite.py --label "changed the prompt"         # after a change: runs everything, then compares
    uv run python tuitor_eval/compare.py                                        # compare the two saved snapshots again any time

It takes roughly 10-15 minutes and costs about a dollar. Close other heavy programs first (the ops part times things on this computer).

| Verdict | Meaning | Exit code |
|---|---|---|
| **PASS** | Nothing important got worse. Safe to keep the change. | 0 |
| **REVIEW** | A quality or speed/cost number got worse than its normal noise. A person decides. | 2 |
| **FAIL** | A safety number got worse (scope, leakage, toxicity). Don't ship it. | 1 |
| **INCOMPLETE** | One part crashed, so nothing was saved or compared. Fix it and run again. | 3 |

The rules (which direction is better, how much wobble is normal) are the main project's, from `evals/metric_registry.py`.
Files, all inside this folder: `baselines/baseline.json` and `candidate.json` (what the comparison reads), `baselines/history/` (a
timestamped copy of every snapshot, so nothing is ever lost), and `reports/tuitor_suite_<time>.xlsx` (every metric, every case, the
ops tables and a **Regression check** sheet). The snapshot also records a hash of Tuitor's prompt, so you can see when the prompt changed.
A `--limit` run is only a trial: it can't be a baseline and isn't comparable with one.

## Reading the result

Each score is between 0 and 1 (safety pass rates are percentages). For toxicity, **lower is better**.
A finished run writes an Excel file to `tuitor_eval/reports/`. Open the **Cases** sheet: one row per question, with
Tuitor's answer, its score and the judge's reason. Filter the Result column to FAIL to see only the weak ones.
The **Metrics** sheet lists every number under the same ids the main project uses. (The "Ops report" sheet is empty on purpose.)

If the judge can't score one question (it stalls), that result is marked ERROR, the rest still finish, and the end of the run says so.

## Good to know

- Tuitor's wellbeing and privacy pre-checks (its fixed replies before searching) are **on**, as in the real app. `--no-prechecks` turns them off.
- Added or changed PDFs in Tuitor? Add `--refresh` to re-copy its data.
- Tuitor isn't in `C:\projects\Tuitor`? Set `TUITOR_DIR` to the folder that contains `Tuitor.py`.
- If you change the prompt or the search settings in the app, update the constants at the top of `tuitor_pipeline.py`.
- Not included yet: the ops (speed / cost) evals, and chat-history memory.
