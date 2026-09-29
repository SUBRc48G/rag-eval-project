"""
tuitor_eval/prod/compare.py -- the regression decision for the LIVE app, using exactly the main project's rules
(evals/metric_registry.py -- the same file the local suite uses, since every metric id here matches the local
suite's naming on purpose). Fully separate from tuitor_eval/compare.py: its own baselines/ folder, its own snapshots.

    PASS    no gate blocked, no guardrail regressed. Safe to consider this a non-event.
    REVIEW  a guardrail regressed beyond tolerance (quality, speed, cost). A human decides.
    FAIL    a gate regressed (a safety number -- scope, leakage, toxicity, or wellbeing). Take it seriously.

Exit code: PASS=0, FAIL=1, REVIEW=2 -- same convention as the local suite, so a script can act on it.

    uv run python tuitor_eval/prod/compare.py                                # baseline.json vs candidate.json
    uv run python tuitor_eval/prod/compare.py --baseline a.json --candidate b.json --all

LEARNED TOLERANCES (see learned_spread() and use_prod_tolerances() below): a fixed, hand-typed tolerance turned out
to need revising twice in the first day of real use (scope.avg_score: 0.02 -> 0.03 -> 0.05, each guess undershooting
the next real sample). Rather than keep hand-tuning numbers, every metric's tolerance is now LEARNED from its own
history in baselines/history/ -- every snapshot this project has ever saved is a real, free data point about how
much that metric naturally wobbles with nothing changed. Full story and the worked numbers: tuitor_eval/prod/README.md, §10.
"""

import argparse
import json
import shutil
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so `import common` (in tuitor_eval/) is found from this sub-folder
import common                                   # must be first of our own imports: isolates this run inside tuitor_eval/
import evals.compare as _evals_compare          # needed as a module (not just its names) so use_prod_tolerances() can patch
                                                 # its OWN 'rule_for' binding -- see that function for why
from evals.compare import EXIT_CODE, compare, load, print_report

BASELINE_NAME, CANDIDATE_NAME = "baseline.json", "candidate.json"

MIN_HISTORY_FOR_LEARNING = 3    # fewer real past readings than this and a computed spread is just noise about noise;
                                # fall back to the shared registry's hand-set default until there's enough evidence.
MARGIN_STD_DEVS = 3             # "3-sigma": if wobble behaves roughly normally, an unchanged metric crosses this by
                                # chance only about once in 370 comparisons -- generous enough to stop crying wolf.
MAX_LEARNED_GATE_TOL = 0.08     # a safety GATE's learned tolerance is never allowed past this, however wild its
                                # history looks -- a hard gate that can drift arbitrarily loose isn't a gate anymore.


def baselines_dir():
    return common.HERE / "prod" / "baselines"


def history_values(metric_id):
    """Every value this metric has ever had, across every snapshot (baseline AND candidate) ever saved in
    baselines/history/. This is the raw material for learning how much a metric naturally wobbles run to run --
    nothing new needs recording; it's just the snapshots you already have, read back."""
    values = []
    for path in sorted((baselines_dir() / "history").glob("*.json")):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        v = snap.get("metrics", {}).get(metric_id)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values.append(v)
    return values


def learned_spread(metric_id):
    """(mean, stdev, n) from this metric's own real history, or None if there isn't yet enough history
    (< MIN_HISTORY_FOR_LEARNING readings) to trust a computed number over the registry's hand-set default."""
    values = history_values(metric_id)
    if len(values) < MIN_HISTORY_FOR_LEARNING:
        return None
    return statistics.mean(values), statistics.stdev(values), len(values)


def use_prod_tolerances():
    """Replace every metric's fixed tolerance with one LEARNED from its own history (see module docstring), once
    enough history exists: tol = MARGIN_STD_DEVS x standard deviation of its past readings. Below
    MIN_HISTORY_FOR_LEARNING readings, the shared registry's own hand-set default is used unchanged -- exactly like
    before this feature existed. A GATE's learned tolerance is capped at MAX_LEARNED_GATE_TOL.

    Patches evals.compare's OWN 'rule_for' name, not evals.metric_registry.rule_for: 'from X import Y' copies a
    reference at import time, so patching the ORIGINAL wouldn't reach the call already inside evals.compare.compare()
    that uses ITS OWN local 'rule_for' name. Runs once, automatically, the first time this file is imported. Only
    ever changes what the LIVE APP's comparisons use -- the shared registry file, and the local suite's own
    tolerances, are never touched."""
    if getattr(_evals_compare, "PROD_TOLERANCES", False):
        return
    real_rule_for = _evals_compare.rule_for

    def wrapped(metric_id):
        rule = dict(real_rule_for(metric_id))
        spread = learned_spread(metric_id)
        if spread is not None:
            _, stdev, n = spread
            learned_tol = MARGIN_STD_DEVS * stdev
            if rule["kind"] == "gate":
                learned_tol = min(learned_tol, MAX_LEARNED_GATE_TOL)
            rule["tol"], rule["learned_from_n"] = learned_tol, n
        return rule

    _evals_compare.rule_for = wrapped
    _evals_compare.PROD_TOLERANCES = True


use_prod_tolerances()   # applied as soon as this module is imported, whether by this file's own main() or by run_suite.py


def historical_reference(raw_metrics):
    """raw_metrics, with each metric's value replaced by the AVERAGE of its own history where there's enough of it
    (same MIN_HISTORY_FOR_LEARNING threshold as the tolerance) -- so the comparison's reference point is a steady
    typical level, not whichever single run happened to be blessed as --baseline (which can itself be a lucky or
    unlucky sample, as scope.avg_score's first baseline turned out to be). Returns (adjusted_metrics, notes) where
    notes lists exactly which metrics were adjusted and by how much, for an honest printout -- never a silent swap."""
    adjusted, notes = {}, []
    for mid, raw_value in raw_metrics.items():
        spread = learned_spread(mid)
        if spread is not None and isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            mean, _, n = spread
            adjusted[mid] = mean
            if abs(mean - raw_value) > 1e-9:
                notes.append(f"{mid}: using {n}-run history average {mean:.4g} instead of the saved baseline's raw {raw_value:.4g}")
        else:
            adjusted[mid] = raw_value
    return adjusted, notes


def compare_with_learning(baseline, candidate):
    """The real comparison to use everywhere (run_suite.py and this file's own main()): same evals.compare.compare(),
    but the baseline side is smoothed by historical_reference() first. Returns (verdict, rows, notes) -- print notes
    so a smoothed reference value is always visible, never a silent substitution."""
    adjusted_metrics, notes = historical_reference(baseline.get("metrics", {}))
    smoothed_baseline = {**baseline, "metrics": adjusted_metrics}
    verdict, rows = compare(smoothed_baseline, candidate)
    return verdict, rows, notes


def save_snapshot(snapshot, name):
    """Write baselines/<name>.json, plus a timestamped copy in baselines/history/, so overwriting never loses an earlier run."""
    folder = baselines_dir()
    (folder / "history").mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    shutil.copy2(path, folder / "history" / f"{name}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json")
    return str(path)


def describe(snapshot, fallback):
    meta = snapshot.get("metadata", {})
    return f"{meta.get('label') or fallback}   (parts: {', '.join(meta.get('parts', [])) or '?'}, {meta.get('created_at', '?')})"


def add_regression_sheet(xlsx_path, verdict, rows):
    """Append a 'Regression check' sheet to an Excel report: verdict, then every metric with baseline, candidate, delta and status."""
    from openpyxl import load_workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = load_workbook(xlsx_path)
    ws = wb.create_sheet("Regression check", 1)
    head_fill, head_font = PatternFill("solid", fgColor="1F3864"), Font(name="Arial", bold=True, color="FFFFFF", size=10)
    ws["A1"], ws["B1"] = "Verdict", verdict
    ws["A1"].font = Font(name="Arial", bold=True, size=12)
    ws["B1"].font = Font(name="Arial", bold=True, size=12,
                         color={"PASS": "375623", "REVIEW": "7F6000", "FAIL": "9C0006"}[verdict])
    ws["A2"] = ("Compared with the baseline using the rules in evals/metric_registry.py (gate = must not regress, guardrail = "
                "regression is flagged for review, info = shown only), tolerances learned from history where enough exists. "
                "Delta is candidate minus baseline (the baseline shown may be a history average, not the raw saved value).")
    ws["A2"].font = Font(name="Arial", italic=True, size=9, color="595959")
    for i, h in enumerate(["Metric id", "Kind", "Better", "Baseline", "Candidate", "Delta", "Status"], start=1):
        c = ws.cell(4, i, h)
        c.font, c.fill, c.alignment = head_font, head_fill, Alignment(horizontal="center")
    order = {"blocked": 0, "regressed": 1, "dropped": 2, "new": 3, "improved": 4, "flat": 5, "info": 6}
    for r, row in enumerate(sorted(rows, key=lambda x: (order.get(x["status"], 9), x["id"])), start=5):
        to_cell = lambda v: (int(v) if isinstance(v, bool) else v)
        for c, v in enumerate([row["id"], row["kind"], row["direction"], to_cell(row["baseline"]), to_cell(row["candidate"])], start=1):
            ws.cell(r, c, v).font = Font(name="Arial", size=10)
        ws.cell(r, 6, f'=IF(AND(ISNUMBER(D{r}),ISNUMBER(E{r})),E{r}-D{r},"")')
        ws.cell(r, 7, row["status"]).font = Font(name="Arial", size=10, bold=True)
        for c in (4, 5, 6):
            ws.cell(r, c).number_format = "0.000"
    last = 4 + len(rows)
    bad, good = PatternFill("solid", bgColor="F8CBAD"), PatternFill("solid", bgColor="C6E0B4")
    for word in ("blocked", "regressed"):
        ws.conditional_formatting.add(f"G5:G{last}", CellIsRule(operator="equal", formula=[f'"{word}"'], fill=bad, font=Font(name="Arial", bold=True, color="9C0006")))
    ws.conditional_formatting.add(f"G5:G{last}", CellIsRule(operator="equal", formula=['"improved"'], fill=good, font=Font(name="Arial", bold=True, color="375623")))
    for col, w in zip("ABCDEFG", [46, 11, 8, 12, 12, 11, 11]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A5"
    wb.save(xlsx_path)


def main():
    parser = argparse.ArgumentParser(description="Compare two live-app snapshots and return a verdict (same rules as evals/metric_registry.py, tolerances learned from history).")
    parser.add_argument("--baseline", default=None, help=f"default: tuitor_eval/prod/baselines/{BASELINE_NAME}")
    parser.add_argument("--candidate", default=None, help=f"default: tuitor_eval/prod/baselines/{CANDIDATE_NAME}")
    parser.add_argument("--all", action="store_true", help="also show info metrics")
    args = parser.parse_args()

    baseline_path = args.baseline or str(baselines_dir() / BASELINE_NAME)
    candidate_path = args.candidate or str(baselines_dir() / CANDIDATE_NAME)
    baseline, candidate = load(baseline_path), load(candidate_path)
    print(f"baseline  : {describe(baseline, baseline_path)}")
    print(f"candidate : {describe(candidate, candidate_path)}")
    verdict, rows, notes = compare_with_learning(baseline, candidate)
    if notes:
        print(f"\n{len(notes)} metric(s) compared against a history-learned typical value instead of the raw saved baseline:")
        for note in notes:
            print(f"   - {note}")
    print_report(verdict, rows, show_all=args.all)
    sys.exit(EXIT_CODE[verdict])


if __name__ == "__main__":
    main()
