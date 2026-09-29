"""
Live-app application eval -- scores the answers the robot RECORDED from your live Tuitor. It never contacts the live app.
It is the same eval as tuitor_eval/eval_application.py (same three judges, same metric names), fed with recorded answers:
    Correctness    are the facts right?
    Completeness   does it cover the key points of the reference answer?
    Style          is it a friendly, plain-language teaching voice?

    uv run python tuitor_eval/prod/eval_application.py --show     free: only lists what would be scored
    uv run python tuitor_eval/prod/eval_application.py            scores it with the judge (a few cents)
    uv run python tuitor_eval/prod/eval_application.py --answers tuitor_eval/prod/answers/application_2026-09-27_090202.json

Only test-set questions that have a recorded answer are scored. Metric ids are the same as the other evals: application.correctness_[geval].avg_score ...
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so `import common` (in tuitor_eval/) is found from this sub-folder
import common                                   # must be first of our own imports: isolates this run inside tuitor_eval/
from recorded import PROD_PDFS, RecordedTuitor, newest_answers, norm, save_last_metrics

GROUP = "application"


def parse():
    parser = argparse.ArgumentParser(description="Score answers recorded from the LIVE Tuitor, offline (the live app is not contacted). "
                                                 "Everything stays inside tuitor_eval/.")
    parser.add_argument("--answers", nargs="+", metavar="FILE", help="recorded-answers file(s) to score (default: the newest application_*.json)")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N recorded questions (cheaper)")
    parser.add_argument("--show", action="store_true", help="only list the questions, recorded answers and reference answers; nothing is scored (free)")
    return parser.parse_args()


def answer_files(args):
    if args.answers:
        paths = [Path(a) if Path(a).is_absolute() else common.ROOT / a for a in args.answers]     # relative paths count from the project folder
    else:
        newest = newest_answers(GROUP)
        if newest is None:
            sys.exit("No recorded answers yet. Record some first:\n"
                     "  uv run --no-project --with playwright python tuitor_eval/prod/collect_prod.py --set application")
        paths = [newest]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        sys.exit(f"Cannot find the recorded-answers file: {', '.join(missing)}")
    return paths


def recorded_goldens(rag, limit=None):
    """The test-set questions (with reference answers) that the robot has a recorded answer for, in test-set order."""
    goldens = json.loads(Path(common.golden("testset.json")).read_text(encoding="utf-8"))
    matched = [g for g in goldens if norm(g["question"]) in rag.records]
    return matched[:limit] if limit else matched


def show(rag, goldens):
    print(f"\nRecorded from the LIVE app ({rag.meta.get('url', '?')}) at {rag.meta.get('collected_at', '?')}.")
    print(f"{len(goldens)} question(s) would be scored. Nothing is sent anywhere by --show.\n")
    for i, g in enumerate(goldens, start=1):
        rec = rag.records[norm(g["question"])]
        seconds = f" ({rec['seconds']}s)" if rec.get("seconds") else ""
        answer = rec["answer"] if rec.get("ok", True) else f"[NO ANSWER: {rec.get('error', 'unknown')}]"
        print(f"[{i}] Q: {g['question']}")
        print(f"    Live app{seconds}: {' '.join(answer.split())}")
        print(f"    Reference:  {' '.join(g['ground_truth'].split())}\n")


def run(rag, args, main_eval):
    from evals.harness import print_summary
    from tuitor_style import use_tuitor_style
    use_tuitor_style(main_eval)                 # judge Style by Tuitor's own voice, not the main project's lecture voice (in memory only)
    main_eval.GOLDEN_PATH = common.golden("testset.json")
    original = main_eval.load_goldens
    main_eval.load_goldens = lambda path: [g for g in original(path) if norm(g["question"]) in rag.records]   # only what was recorded
    common.limit_goldens(main_eval, args.limit)
    summary = main_eval.run(rag)
    print_summary("Live Tuitor application", summary)
    return common.flatten_nested(GROUP, summary)


def main():
    args = parse()
    rag = RecordedTuitor(*answer_files(args))
    goldens = recorded_goldens(rag, args.limit)
    if not goldens:
        sys.exit("None of the recorded questions match the test set (goldens/testset.json), so there is nothing to score. "
                 "Record the application set again with collect_prod.py --set application.")
    print(f"Recorded file(s): {', '.join(rag.sources)}  ->  {len(goldens)} question(s) matched the test set.")
    print(f"(Books the test expects on the live shelf: {', '.join(PROD_PDFS)}.)")
    if args.show:
        show(rag, goldens)
        return
    from evals import eval_application as main_eval
    metrics = common.run_eval(args, "prod_application", [(GROUP, main_eval)], lambda r: run(r, args, main_eval), rag=rag)
    if not args.limit:                        # a --limit run is a cheaper trial, not the real thing -- never let it become the snapshot
        save_last_metrics("application", metrics)
    else:
        print(f"\n(--limit {args.limit} was used, so this run is NOT saved for a baseline/candidate snapshot -- run without --limit for that.)")


if __name__ == "__main__":
    main()
