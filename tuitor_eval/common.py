"""
common.py -- the small amount of setup every Tuitor eval file shares, so each eval file stays tiny.

Import it FIRST in each file: importing it isolates the run inside tuitor_eval/ (see below).

What the isolation does
  - runs from inside this folder, so DeepEval keeps its cache here and (because it only reads .env.local from the folder
    it starts in) never sees the project's Confident AI key -- nothing is uploaded
  - nothing is written to the project's tracker, baselines or reports; results go to tuitor_eval/reports/
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

os.chdir(HERE)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

# A normal judge call takes seconds. When one question hangs (it did, twice, on the mitochondria question), waiting longer
# doesn't help -- it just wastes minutes -- so give a call 2 minutes and a whole question ~7 before giving up on it.
# (Only applied if you haven't set your own values in this terminal.)
os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "120")
os.environ.setdefault("DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE", "400")

GOLDENS = ROOT / "goldens"


def golden(filename):
    """Absolute path of a question file in the project's goldens/ (we changed folder, so relative paths would break)."""
    return str(GOLDENS / filename)


# ------------------------------------------------------------------ metric names (identical to the main project's run_suite.py)
def slug(name):
    return name.strip().lower().replace(" ", "_")


def flatten_nested(namespace, summary):
    """{metric: {stat: value}} -> {'pipeline.contextual_relevancy.avg_score': value}"""
    return {f"{namespace}.{slug(metric)}.{stat}": value for metric, stats in summary.items() for stat, value in stats.items()}


# ------------------------------------------------------------------ command line
def make_parser(description, limit=True):
    parser = argparse.ArgumentParser(description=description + " Everything stays inside tuitor_eval/.")
    if limit:
        parser.add_argument("--limit", type=int, default=None, help="score only the first N questions (cheaper)")
    parser.add_argument("--refresh", action="store_true", help="re-copy Tuitor's vector store (use after you add PDFs in Tuitor)")
    parser.add_argument("--no-prechecks", action="store_true", help="skip Tuitor's wellbeing and privacy pre-checks")
    return parser


def parse_args(description, limit=True):
    return make_parser(description, limit).parse_args()


def limit_goldens(main_eval, n):
    """--limit N: make a main-project eval read only the first N questions (in memory only)."""
    if n:
        original = main_eval.load_goldens
        main_eval.load_goldens = lambda path: original(path)[:n]


# ------------------------------------------------------------------ safety rails
def upload_is_off():
    """True if this run cannot upload to Confident AI. Stops the run instead of uploading by accident."""
    from deepeval.confident.api import is_confident
    if not is_confident():
        return True
    if os.getenv("DEEPEVAL_DEFAULT_SAVE"):          # clearing the key could then be written to a file; don't risk it
        return False
    from deepeval.confident.api import set_confident_api_key
    set_confident_api_key(None)                     # this run only, in memory
    return not is_confident()


def resilient(evaluate_fn, attempts=2, max_concurrent=6):
    """Wrap DeepEval's evaluate() for this test only:
      - fewer judge calls at once (fewer timeouts)
      - ignore_errors: if the judge cannot score one question (it hangs or times out), that result is recorded as an
        ERROR and every other question still finishes, instead of one bad question sinking the whole run
      - one automatic retry for a failure that still escapes. A retry repeats only the judging; Tuitor's answers were
        already collected, so they are not asked for again."""
    from deepeval.evaluate.configs import AsyncConfig, ErrorConfig

    def wrapper(*args, **kwargs):
        kwargs.setdefault("async_config", AsyncConfig(max_concurrent=max_concurrent))
        kwargs.setdefault("error_config", ErrorConfig(ignore_errors=True))
        for attempt in range(1, attempts + 1):
            try:
                return evaluate_fn(*args, **kwargs)
            except Exception as e:      # a slow or derailed judge call is not a Tuitor problem; try once more, then stop loudly
                print(f"[tuitor] the judge failed (attempt {attempt}/{attempts}): {type(e).__name__}: {str(e)[:120]}")
                if attempt == attempts:
                    raise
    return wrapper


def unscored_note(rows):
    """Plain-words note about results the judge could not score (they show as ERROR in the Cases sheet)."""
    errors = [r for r in rows if r["result"] == "ERROR"]
    if not errors:
        return "Every result was scored."
    lines = [f"\nNOTE: the judge could not score {len(errors)} result(s) (it timed out). They are marked ERROR in the Cases sheet, "
             f"are left out of the averages above, and count as 'not passed' in the pass rates:"]
    lines += [f"   - {r['metric']}: {r['input'][:80]!r}" for r in errors[:6]]
    return "\n".join(lines)


# ------------------------------------------------------------------ building Tuitor and running an eval
def build_rag(args):
    if not upload_is_off():
        sys.exit("Stopping: this run would upload to Confident AI, and it is meant to stay local. "
                 "Unset DEEPEVAL_DEFAULT_SAVE / CONFIDENT_API_KEY in this terminal and try again.")
    print("Confident AI upload: OFF (this test stays on your computer)")
    from tuitor_pipeline import TuitorPipeline
    return TuitorPipeline(refresh_store=args.refresh, prechecks=not args.no_prechecks)


def run_eval(args, label, modules, body, targets=None, rag=None, extra_cases=None):
    """The wrapper every eval file uses: build Tuitor, make judging resilient, capture every case, run `body(rag)` (which
    returns {metric_id: number}), then write the Excel report.
       modules     = [(group, main_project_eval_module)]   (empty for evals that use no judge, like ops)
       rag         = a ready-made Tuitor to test (the prod/ evals pass answers recorded from the live app); default: the local copy.
                     If it has a describe() method, its rows replace the default 'what was tested' rows in the report.
       extra_cases = for a no-judge eval that still has real per-question results (e.g. a deterministic text match):
                     rows in the same shape report_xlsx.py's Cases sheet expects, so they show up there too, same as a
                     judged eval's cases -- see tuitor_eval/prod/eval_wellbeing.py for the shape."""
    from evals.report_xlsx import CaseCollector, write_suite_report

    rag = rag or build_rag(args)
    for _, module in modules:
        module.evaluate = resilient(module.evaluate)          # in memory only; the eval files themselves are untouched
    judge = modules[0][1].JUDGE_MODEL if modules else "none (this eval uses no judge)"
    if modules:
        print(f"Judge: {judge}. Judge settings: up to {os.environ['DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE']}s per call, "
              f"6 at a time, 1 automatic retry. Scoring can take a few minutes.")

    collector = CaseCollector()
    with collector.capturing(modules):
        metrics = body(rag)
    collector.rows = collector.rows + list(extra_cases or [])
    if modules:                                                # the unscored-judge note only means something where a judge ran
        print(unscored_note(collector.rows))

    what = rag.describe() if hasattr(rag, "describe") else [
        ("What was tested", "Tuitor's real Chat Tutor pipeline (see tuitor_eval/tuitor_pipeline.py)"),
        ("Retrieval", f"MMR, k={rag.retriever.top_k}, fetch_k={rag.retriever.fetch_k}, lambda 0.7 (copied from Tuitor.py)"),
        ("Answer model", f"{rag.llm.model_name} at temperature {rag.llm.temperature}"),
        ("Wellbeing / privacy pre-checks", "on" if rag.prechecks else "off"),
    ]
    if any(getattr(module, "TUITOR_STYLE", False) for _, module in modules):
        from tuitor_style import STYLE_SUMMARY
        what = what + [("Style judge", STYLE_SUMMARY)]
    info = what[:1] + [("Part run", label)] + what[1:] + [
        ("Judge model", judge),
        ("Confident AI upload", "OFF - this test stays on your computer"),
        ("Run finished", datetime.now().strftime("%Y-%m-%d %H:%M:%S (local)")),
    ]
    path = HERE / "reports" / f"tuitor_{label}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
    write_suite_report(str(path), metrics, collector.rows, info, targets=targets or {})
    print(f"\nExcel report -> {path}")
    if collector.rows:                          # a judge ran, OR a no-judge eval supplied its own per-case rows (extra_cases)
        print("Open it and look at the 'Cases' sheet: each question, the app's answer, its result and why. "
              "(The 'Ops report' sheet is empty here on purpose; this test has no latency/cost/reliability numbers.)")
    else:                                       # no judge, no per-case rows -- the ops-style eval (latency/cost/reliability)
        print("Open it and look at the 'Ops report' sheet: latency / reliability / cost, with PASS/FAIL against the targets. "
              "(The 'Cases' sheet is empty here on purpose; this test has no individual pass/fail cases.)")
    return metrics
