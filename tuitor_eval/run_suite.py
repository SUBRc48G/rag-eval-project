"""
Tuitor run_suite.py -- the FINAL regression suite for your real Tuitor pipeline.

Runs every part (retriever, generator, pipeline, application, safety, ops) against Tuitor ONCE, saves a snapshot, and -- when a
baseline exists -- compares it with the baseline using the main project's rules and prints PASS / REVIEW / FAIL.

    uv run python tuitor_eval/run_suite.py --baseline --label "Tuitor v1"     # bless this run as the baseline (do it once, on the full sets)
    uv run python tuitor_eval/run_suite.py --label "changed the prompt"       # a candidate: runs, then compares with the baseline
    uv run python tuitor_eval/compare.py                                      # compare the two snapshots again any time

Files (all inside tuitor_eval/):
    baselines/baseline.json, candidate.json     the snapshots the comparison reads (gates + guardrails; --full also keeps info metrics)
    baselines/history/                          a timestamped copy of every snapshot -- nothing is ever overwritten for good
    reports/tuitor_suite_<time>.xlsx            the full report: every metric, every case, the ops tables, and a 'Regression check' sheet

Exit code: PASS=0, FAIL=1 (a safety gate regressed), REVIEW=2, INCOMPLETE=3 (a part crashed, so nothing was saved).
To run one part on its own, use its own file (eval_safety.py, eval_rag_pipeline.py, eval_ops.py, ...).
Takes roughly 10-15 minutes and costs about a dollar. Close other heavy programs first: the ops part times things on this computer.
"""

import hashlib
import sys
import time
from datetime import datetime, timezone

import common                                   # must be first: isolates this run inside tuitor_eval/
import compare as tuitor_compare
import eval_application, eval_generator, eval_ops, eval_rag_pipeline, eval_retriever, eval_safety
from evals.compare import EXIT_CODE, compare as compare_snapshots, load, print_report
from evals.metric_registry import rule_for
from evals.report_xlsx import CaseCollector, write_suite_report

PARTS = [eval_retriever, eval_generator, eval_rag_pipeline, eval_application, eval_safety, eval_ops]


def build_snapshot(rag, args, metrics, seconds, judge):
    import tuitor_pipeline as tp
    kept = metrics if args.full else {k: v for k, v in metrics.items() if rule_for(k)["kind"] != "info"}
    return {
        "metadata": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": args.label, "git_sha": "n/a", "judge_model": judge,
            "prompt_hash": hashlib.sha256(tp.TEMPLATE.encode()).hexdigest()[:12],     # changes whenever Tuitor's prompt changes
            "suite_seconds": round(seconds, 1), "full": args.full, "n_metrics": len(kept), "limit": args.limit,
            "config": {"retrieval": f"MMR k={tp.K} fetch_k={tp.FETCH_K} lambda={tp.LAMBDA_MULT}", "answer_model": f"{tp.CHAT_MODEL} t={tp.TEMPERATURE}",
                       "embedding_model": tp.EMBED_MODEL, "prechecks": rag.prechecks, "store_chunks": rag.store._collection.count()},
        },
        "metrics": kept,
    }


def main():
    parser = common.make_parser("Run the full Tuitor regression suite.")
    parser.add_argument("--baseline", action="store_true", help="save this run as the baseline (needs the full question sets: no --limit)")
    parser.add_argument("--label", default="", help="describe what this run is (shown in the comparison)")
    parser.add_argument("--full", action="store_true", help="also keep the info metrics in the snapshot")
    parser.add_argument("--no-excel", action="store_true", help="skip the Excel report")
    args = parser.parse_args()
    if args.baseline and args.limit:
        sys.exit("A baseline must use the full question sets: run again without --limit.")
    args.safety_only = None                                       # every safety eval

    rag = common.build_rag(args)
    modules = [pair for part in PARTS for pair in part.modules()]
    for _, module in modules:
        module.evaluate = common.resilient(module.evaluate)       # in memory only; the eval files are untouched
    judge = modules[0][1].JUDGE_MODEL
    print(f"Judge: {judge}. Suite: {', '.join(p.GROUP for p in PARTS)}. This takes a while.")

    collector, metrics, failed, started = CaseCollector(), {}, [], time.perf_counter()
    with collector.capturing(modules):
        for i, part in enumerate(PARTS, start=1):
            print(f"\n=== [{i}/{len(PARTS)}] {part.GROUP} ===")
            try:
                metrics.update(part.run(rag, args))
            except Exception as e:            # keep going so the report shows everything else, but never bless an incomplete run
                failed.append(part.GROUP)
                print(f"[suite] the '{part.GROUP}' part crashed: {type(e).__name__}: {str(e)[:150]}")
    seconds = time.perf_counter() - started
    print(common.unscored_note(collector.rows))

    snapshot = build_snapshot(rag, args, metrics, seconds, judge)
    xlsx = None
    if not args.no_excel:
        info = [("What was tested", "Tuitor's real Chat Tutor pipeline: the full regression suite"), ("Label", args.label or "(none)"),
                ("Parts", ", ".join(p.GROUP for p in PARTS)), ("Parts that crashed", ", ".join(failed) or "none"),
                ("Retrieval / model / prechecks", f"{snapshot['metadata']['config']['retrieval']} | {snapshot['metadata']['config']['answer_model']} | prechecks {'on' if rag.prechecks else 'off'}"),
                ("Prompt hash", snapshot["metadata"]["prompt_hash"]), ("Judge model", judge), ("Suite duration (s)", snapshot["metadata"]["suite_seconds"]),
                ("Confident AI upload", "OFF - this test stays on your computer"), ("Finished", datetime.now().strftime("%Y-%m-%d %H:%M:%S (local)"))]
        xlsx = common.HERE / "reports" / f"tuitor_suite_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.xlsx"
        write_suite_report(str(xlsx), metrics, collector.rows, info, targets=eval_ops.targets())
        print(f"\nExcel report -> {xlsx}")

    if failed:
        print(f"\nINCOMPLETE: {', '.join(failed)} crashed, so no snapshot was saved and nothing was compared.")
        sys.exit(3)
    unscored = [r for r in collector.rows if r["result"] == "ERROR"]
    if unscored and args.baseline:          # a reference photo with missing scores would leave the comparisons partly blind
        print(f"\nINCOMPLETE: the judge could not score {len(unscored)} result(s), so this run can't be the baseline. "
              f"Run it again (see the Cases sheet for which ones); nothing was saved.")
        sys.exit(3)
    if unscored:
        print(f"\nWARNING: {len(unscored)} result(s) were not scored (see above). Their averages leave them out, so check the Cases sheet before trusting a PASS.")
    if args.limit:
        print(f"\nNOTE: --limit {args.limit} was used, so these numbers are only a trial and are not comparable with a full baseline.")

    name = "baseline" if args.baseline else "candidate"
    print(f"\nsnapshot saved -> {tuitor_compare.save_snapshot(snapshot, name)}   ({snapshot['metadata']['n_metrics']} metrics)")
    if args.baseline:
        print("This run is now the baseline. Later runs (without --baseline) are compared against it.")
        return

    baseline_path = tuitor_compare.baselines_dir() / tuitor_compare.BASELINE_NAME
    if not baseline_path.exists():
        print("\nNo baseline yet, so nothing to compare with. Make one with:  uv run python tuitor_eval/run_suite.py --baseline")
        return
    baseline = load(str(baseline_path))
    print(f"\nbaseline  : {tuitor_compare.describe(baseline, 'baseline.json')}\ncandidate : {tuitor_compare.describe(snapshot, 'candidate')}")
    verdict, rows = compare_snapshots(baseline, snapshot)
    print_report(verdict, rows)
    if xlsx:
        tuitor_compare.add_regression_sheet(xlsx, verdict, rows)
        print(f"('Regression check' sheet added to {xlsx.name})")
    sys.exit(EXIT_CODE[verdict])


if __name__ == "__main__":
    main()
