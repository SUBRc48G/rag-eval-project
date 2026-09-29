"""
Live-app safety eval -- scores the answers the robot RECORDED from your live Tuitor. It never contacts the live app.
It is the same eval as tuitor_eval/eval_safety.py (same three judges, same metric names), fed with recorded answers:
    scope      did it stay a tutor and decline off-topic requests and jailbreaks?
    leakage    did it keep its instructions private and never leak contact details? (judge + a no-judge email/phone check)
    toxicity   did it avoid harmful language? (lower is better)

    uv run python tuitor_eval/prod/eval_safety.py --show              free: only lists what would be scored
    uv run python tuitor_eval/prod/eval_safety.py                     scores all three (a few cents)
    uv run python tuitor_eval/prod/eval_safety.py --only scope        just one (or --only scope leakage for two)

Reads the newest scope_*.json / leakage_*.json / toxicity_*.json in tuitor_eval/prod/answers/ (one recorded run per set --
collect_prod.py --set scope --allow-attacks, etc). Metric ids are the same as the other evals: safety.scope.pass_rate, ...
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so `import common` (in tuitor_eval/) is found from this sub-folder
import common                                   # must be first of our own imports: isolates this run inside tuitor_eval/
from recorded import NOT_APPLICABLE_IDS, RecordedTuitor, SAFETY_GOLDEN_FILES, newest_answers, norm, not_applicable_count, safety_goldens, save_last_metrics

# scope/leakage/toxicity: the three judge-based sets this file knows how to score (SAFETY_GOLDEN_FILES names exactly
# these three). 'wellbeing' is also in recorded.ATTACK_SETS (gated behind --allow-attacks like these are) but is
# scored completely differently -- deterministically, no judge -- by the separate eval_wellbeing.py, not this file.
SAFETY_SETS = list(SAFETY_GOLDEN_FILES)

GROUP = "safety"


def parse():
    parser = argparse.ArgumentParser(description="Score answers recorded from the LIVE Tuitor, offline (the live app is not contacted). "
                                                 "Everything stays inside tuitor_eval/.")
    parser.add_argument("--only", nargs="+", choices=SAFETY_SETS, help="which safety eval(s) to score (default: all three)")
    parser.add_argument("--answers", nargs="+", metavar="FILE", help="recorded-answers file(s) to use (only with a single --only part)")
    parser.add_argument("--show", action="store_true", help="only list the recorded questions and answers; nothing is scored (free)")
    return parser.parse_args()


def answer_files(parts, args):
    """{part: [recorded file, ...]}. Default: the newest file for each part. --answers overrides this, but only for one part at a time,
    since each set is recorded to its own file."""
    if args.answers:
        if len(parts) != 1:
            sys.exit("--answers needs exactly one --only part (each set is recorded to its own file), e.g.:\n"
                     "  --only scope --answers tuitor_eval/prod/answers/scope_2026-09-27_120000.json")
        return {parts[0]: [Path(a) if Path(a).is_absolute() else common.ROOT / a for a in args.answers]}

    files, missing = {}, []
    for part in parts:
        newest = newest_answers(part)
        if newest is None:
            missing.append(part)
        else:
            files[part] = [newest]
    if missing:
        cmds = "\n".join(f"  uv run --no-project --with playwright python tuitor_eval/prod/collect_prod.py --set {p} --allow-attacks" for p in missing)
        sys.exit(f"No recorded answers yet for: {', '.join(missing)}. Record them first (each sends attack prompts to the LIVE app):\n{cmds}")
    return files


def matched_goldens(part, rag):
    """The golden rows of one part (already narrowed to what fits Tuitor's real content) that have a recorded answer."""
    return [g for g in safety_goldens(part) if norm(g["input"]) in rag.records]


def show(part, rag):
    matched = matched_goldens(part, rag)
    skipped = not_applicable_count(part)
    print(f"\n--- {part} --- recorded from {', '.join(rag.sources)} at {rag.meta.get('collected_at', '?')}")
    print(f"{len(matched)}/{len(safety_goldens(part))} applicable question(s) have a recorded answer "
          f"({skipped} skipped: don't fit Tuitor's real content). Nothing is sent anywhere by --show.\n")
    for i, g in enumerate(matched, start=1):
        rec = rag.records[norm(g["input"])]
        answer = rec["answer"] if rec.get("ok", True) else f"[NO ANSWER: {rec.get('error', 'unknown')}]"
        tag = f" ({g['subtype']})" if "subtype" in g else ""
        print(f"[{i}] {g['id']}{tag}  expects: {g.get('expected_action', '-')}")
        print(f"    Asked : {g['input']}")
        print(f"    Answer: {' '.join(answer.split())}\n")


def use_filtered_goldens(main_eval):
    """Make main_eval.load_goldens() (called internally by run_scope/run_leakage/run_toxicity) skip the ids in
    NOT_APPLICABLE_IDS, same as safety_goldens() does for --show. Without this, --show and the printed 'N matched'
    counts would honor the filter but the actual judge run would not -- it would silently score all 48, not just
    the ones that fit Tuitor. In memory only; safe to call twice."""
    if getattr(main_eval, "PROD_FILTERED", False):
        return
    real_load_goldens = main_eval.load_goldens
    main_eval.load_goldens = lambda path: [g for g in real_load_goldens(path) if g["id"] not in NOT_APPLICABLE_IDS]
    main_eval.PROD_FILTERED = True


def run(rag, main_eval, parts):
    use_filtered_goldens(main_eval)
    main_eval.SCOPE_GOLDEN_PATH = common.golden(SAFETY_GOLDEN_FILES["scope"])
    main_eval.LEAKAGE_GOLDEN_PATH = common.golden(SAFETY_GOLDEN_FILES["leakage"])
    main_eval.TOXICITY_GOLDEN_PATH = common.golden(SAFETY_GOLDEN_FILES["toxicity"])
    snapshot = main_eval.run_safety(rag, verbose=True, only=parts)
    return {f"{GROUP}.{key}": value for key, value in snapshot.items()}


def main():
    args = parse()
    parts = args.only or SAFETY_SETS
    files = answer_files(parts, args)
    rag = RecordedTuitor(*[path for file_list in files.values() for path in file_list])

    total_matched = 0
    for part in parts:
        n, skipped = len(matched_goldens(part, rag)), not_applicable_count(part)
        total_matched += n
        print(f"{part}: {', '.join(f.name for f in files[part])}  ->  {n} question(s) matched"
              + (f" ({skipped} skipped: don't fit Tuitor's real content)" if skipped else "") + ".")
    if not total_matched:
        sys.exit("None of the recorded questions match their golden sets, so there is nothing to score. Re-record with collect_prod.py.")

    if args.show:
        for part in parts:
            show(part, rag)
        return

    from evals import eval_safety as main_eval
    label = "prod_safety" if not args.only else "prod_safety_" + "-".join(args.only)
    metrics = common.run_eval(args, label, [(GROUP, main_eval)], lambda r: run(r, main_eval, parts), rag=rag)
    if not args.only:                          # --only scope (etc) is a partial run -- never let it become the snapshot for 'safety' as a whole
        save_last_metrics("safety", metrics)
    else:
        print(f"\n(--only {' '.join(args.only)} was used, so this run is NOT saved for a baseline/candidate snapshot -- run the full set (no --only) for that.)")


if __name__ == "__main__":
    main()
