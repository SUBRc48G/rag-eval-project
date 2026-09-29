"""
tuitor_eval/prod/run_suite.py -- runs ALL the live-app evals in one go: application, ops, safety (scope / leakage /
toxicity), wellbeing. It only SCORES what you have already recorded -- it never contacts the live app and never
records anything itself. Recording stays its own deliberate, per-set step with collect_prod.py, since the attack
and wellbeing sets need your explicit --allow-attacks.

    uv run python tuitor_eval/prod/run_suite.py                          score everything recorded so far (a few cents)
    uv run python tuitor_eval/prod/run_suite.py --show                    free preview of all four: lists what would be scored
    uv run python tuitor_eval/prod/run_suite.py --baseline --label "..."  bless this run as the reference photo (once)
    uv run python tuitor_eval/prod/run_suite.py --label "changed the prompt"   a candidate: runs, then compares with the baseline
    uv run python tuitor_eval/prod/compare.py                             compare the two saved snapshots again, any time

Think of the BASELINE as a reference photo of how the live app performs today. After you change something (the
prompt, a PDF, a model), record fresh answers (collect_prod.py) and run this again WITHOUT --baseline: that's the
CANDIDATE, and this file compares it against the photo and prints a verdict:

    PASS    nothing important got worse. REVIEW  a quality/speed/cost number got worse than its normal noise -- a
    person decides. FAIL  a safety number got worse (scope, leakage, toxicity, or wellbeing) -- don't treat this as OK.
    INCOMPLETE  a part was skipped or failed, so no snapshot was saved and nothing was compared.

Each part runs as its own separate command, exactly as if you had typed it yourself, with its output streamed live
(a judge run can take a few minutes -- this way it never looks frozen). That also means there's no risk of one
part's files clashing with another's: eval_safety.py, for example, exists in three different folders in this
project, and subprocess isolation keeps each one running as itself.

Before running a part, this file checks (for free, in under a second) whether you've actually recorded what that
part needs. Nothing recorded -> that part is SKIPPED with the exact recording command to fix it, instead of being
run and failing. Each part still writes its OWN Excel report (tuitor_prod_<part>_<time>.xlsx), same as running it
alone would -- this file only saves typing four separate commands and gives one combined summary at the end.

A --limit or --only run of an individual eval file deliberately does NOT update what this file gathers into a
snapshot (see save_last_metrics() calls in each eval's main()) -- only a full, real run counts.
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # so `import recorded` / `import compare` work from elsewhere
from recorded import load_last_metrics, newest_answers
import compare as prod_compare

PROD_DIR = Path(__file__).resolve().parent
ROOT = PROD_DIR.parent.parent

RECORD = "uv run --no-project --with playwright python tuitor_eval/prod/collect_prod.py"

# (script, label, snapshot key, [recorded sets it needs], [the commands to run if any of those are missing], has a --show flag)
# eval_ops.py has no --show: unlike the others it calls no judge at all, so there is no free/paid distinction to preview.
PARTS = [
    ("eval_application.py", "application", "application", ["application"], [f"{RECORD} --set application"], True),
    ("eval_ops.py", "ops", "ops", ["application"], [f"{RECORD} --set application"], False),
    ("eval_safety.py", "safety (scope/leakage/toxicity)", "safety", ["scope", "leakage", "toxicity"],
     [f"{RECORD} --set scope --allow-attacks", f"{RECORD} --set leakage --allow-attacks", f"{RECORD} --set toxicity --allow-attacks"], True),
    ("eval_wellbeing.py", "wellbeing", "wellbeing", ["wellbeing"], [f"{RECORD} --set wellbeing --allow-attacks"], True),
]


def missing_sets(needed):
    return [s for s in needed if newest_answers(s) is None]


def run_part(script, extra_args):
    """Run one eval exactly as the user would, with output streamed live (not buffered) -- a judge run can take
    minutes, and a silent wait for that long would look like a hang."""
    return subprocess.run([sys.executable, str(PROD_DIR / script)] + extra_args, cwd=str(ROOT)).returncode


def gather_snapshot(keys_and_start_times):
    """{metrics}, [problems]. A part only counts if its last_run/<key>.json exists AND was written AFTER this
    call started -- a leftover from an earlier --limit/--only run (which deliberately never updates that file)
    must never be silently folded into a fresh snapshot. Plain local wall-clock time throughout (both timestamps
    come from this same machine, in the same process's run, so there's nothing timezone-aware to get right)."""
    metrics, problems = {}, []
    for key, started_at in keys_and_start_times:
        last = load_last_metrics(key)
        if last is None:
            problems.append(f"{key}: never scored for real (only --show, or --limit/--only was used last time)")
            continue
        saved_at = datetime.strptime(last["saved_at"], "%Y-%m-%d %H:%M:%S (local)")
        if saved_at < started_at - timedelta(seconds=5):   # a few seconds' slack for clock rounding
            problems.append(f"{key}: its saved result is from before this run started -- stale, not from just now")
            continue
        metrics.update(last["metrics"])
    return metrics, problems


def main():
    parser = argparse.ArgumentParser(description="Run every live-app eval (application, ops, safety, wellbeing) against what you've already recorded.")
    parser.add_argument("--show", action="store_true", help="free preview of all four -- nothing is scored, no judge is called")
    parser.add_argument("--baseline", action="store_true", help="save this run as the reference photo everything else gets compared against (needs every part to run cleanly)")
    parser.add_argument("--label", default="", help="describe this run in a few words (shown later when comparing)")
    args = parser.parse_args()

    results, to_record, key_starts, started = [], [], [], time.perf_counter()
    for i, (script, label, key, needed, record_cmds, has_show) in enumerate(PARTS, start=1):
        gaps = missing_sets(needed)
        print(f"\n{'=' * 70}\n[{i}/{len(PARTS)}] {label}\n{'=' * 70}")
        if gaps:
            print(f"Skipping: nothing recorded yet for {', '.join(gaps)}.")
            results.append((label, "skipped (not recorded)"))
            to_record.append((label, record_cmds))
            continue
        if args.show and not has_show:
            print("(--show doesn't apply here: this part calls no judge at all, so it's already free either way -- running it for real.)")
        extra = ["--show"] if (args.show and has_show) else []
        part_started = datetime.now()
        code = run_part(script, extra)
        results.append((label, "ok" if code == 0 else "FAILED (see output above)"))
        if code == 0 and not args.show:
            key_starts.append((key, part_started))
    seconds = time.perf_counter() - started

    print(f"\n{'=' * 70}\nSUITE SUMMARY  ({seconds:.0f}s)\n{'=' * 70}")
    for label, status in results:
        print(f"  {label:<32} {status}")
    print("=" * 70)

    if to_record:
        print(f"\n{len(to_record)} part(s) skipped -- nothing recorded yet. Record them, then run this suite again:")
        for label, cmds in to_record:
            print(f"\n  {label}:")
            for cmd in cmds:
                print(f"    {cmd}")

    failed = [label for label, status in results if status.startswith("FAILED")]
    if args.show:
        return                                  # --show never produces real metrics, so there is nothing to snapshot or compare
    if failed or to_record:
        print(f"\nINCOMPLETE: {len(failed)} part(s) failed and {len(to_record)} part(s) were unrecorded, so no baseline/candidate "
              f"snapshot was saved and nothing was compared. Fix the above, then run this suite again.")
        sys.exit(3)                             # same convention as tuitor_eval/run_suite.py: PASS=0 FAIL=1 REVIEW=2 INCOMPLETE=3

    metrics, problems = gather_snapshot(key_starts)
    if problems:
        print("\nINCOMPLETE: " + "; ".join(problems) + ". No snapshot was saved. This usually means one part was run with "
              "--limit or --only very recently, outside this suite -- run the suite again cleanly.")
        sys.exit(3)

    snapshot = {"metadata": {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": args.label,
                            "parts": [k for k, _ in key_starts], "suite_seconds": round(seconds, 1)},
               "metrics": metrics}
    name = "baseline" if args.baseline else "candidate"
    path = prod_compare.save_snapshot(snapshot, name)
    print(f"\nsnapshot saved -> {path}   ({len(metrics)} metrics)")

    if args.baseline:
        print("This run is now the baseline. Later runs (without --baseline) are compared against it.")
        return

    baseline_path = prod_compare.baselines_dir() / prod_compare.BASELINE_NAME
    if not baseline_path.exists():
        print(f"\nNo baseline yet, so nothing to compare with. Make one with:  "
              f"uv run python tuitor_eval/prod/run_suite.py --baseline --label \"...\"")
        return
    baseline = prod_compare.load(str(baseline_path))
    print(f"\nbaseline  : {prod_compare.describe(baseline, 'baseline.json')}\ncandidate : {prod_compare.describe(snapshot, 'candidate')}")
    verdict, rows, notes = prod_compare.compare_with_learning(baseline, snapshot)
    if notes:
        print(f"\n{len(notes)} metric(s) compared against a history-learned typical value instead of the raw saved baseline:")
        for note in notes:
            print(f"   - {note}")
    prod_compare.print_report(verdict, rows)
    sys.exit(prod_compare.EXIT_CODE[verdict])


if __name__ == "__main__":
    main()
