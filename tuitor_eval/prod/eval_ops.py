"""
Live-app ops eval -- LATENCY, RELIABILITY and COST of your live Tuitor, measured from what the robot already recorded
(collect_prod.py). It never contacts the live app, calls no judge, and costs nothing to run.

    LATENCY       how long a student really waited for an answer (p50 / p95 / p99), timed by the robot's own stopwatch
    RELIABILITY   how many of the robot's questions got an answer vs failed
    COST          OUTPUT tokens only, counted for real from the recorded answer text (Tuitor's chat model is gpt-4o-mini,
                  same pricing as tuitor_eval/eval_ops.py). INPUT cost (the system prompt + the retrieved book text) is NOT
                  included: a live chat screen never shows what it retrieved, so that part cannot be measured this way.
                  For a full cost estimate (input + output, same model and prompt), run tuitor_eval/eval_ops.py against
                  your LOCAL copy of Tuitor instead.

    uv run python tuitor_eval/prod/eval_ops.py
    uv run python tuitor_eval/prod/eval_ops.py --answers tuitor_eval/prod/answers/application_2026-09-27_100133.json
    uv run python tuitor_eval/prod/eval_ops.py --set application --limit 10

By default it uses every answer in the NEWEST application_*.json (the file collect_prod.py --set application saved).
Only questions the live app actually answered (ok: true) count toward latency and cost; a failed question still counts
toward reliability. Metric ids match the main project's: ops.latency.e2e_p95_ms, ops.reliability.success_rate, ...
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so `import common` (in tuitor_eval/) is found from this sub-folder
import common                                   # must be first of our own imports: isolates this run inside tuitor_eval/
from recorded import ANSWERS_DIR, STUDY_SETS, newest_answers, save_last_metrics

from evals.eval_ops import PRICE_OUTPUT_PER_1M, SLO_P95_MS, USD_TO_INR, lat_print_row, lat_slo_line, lat_summarize, rel_report

GROUP = "ops"


def parse():
    parser = argparse.ArgumentParser(description="Measure latency / reliability / cost of the LIVE Tuitor from recorded answers "
                                                 "(collect_prod.py). No live contact, no judge, costs nothing. Everything stays inside tuitor_eval/.")
    parser.add_argument("--answers", nargs="+", metavar="FILE", help="recorded-answers file(s) to measure (default: the newest for --set)")
    parser.add_argument("--set", default="application", choices=STUDY_SETS, help="which recorded set to use when --answers is not given (default: application)")
    parser.add_argument("--limit", type=int, default=None, help="use only the first N recorded questions (smaller sample)")
    return parser.parse_args()


def answer_files(args):
    if args.answers:
        paths = [Path(a) if Path(a).is_absolute() else common.ROOT / a for a in args.answers]
    else:
        newest = newest_answers(args.set)
        if newest is None:
            sys.exit(f"No recorded '{args.set}' answers yet. Record some first:\n"
                     f"  uv run --no-project --with playwright python tuitor_eval/prod/collect_prod.py --set {args.set}")
        paths = [newest]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        sys.exit(f"Cannot find the recorded-answers file: {', '.join(missing)}")
    return paths


def load_records(paths, limit=None):
    records = []
    for p in paths:
        records += json.loads(p.read_text(encoding="utf-8"))["answers"]
    return records[:limit] if limit else records


# ------------------------------------------------------------------ the three measurements
def latency_metrics(records):
    """From the robot's own stopwatch (collect_prod.py's `seconds`: Send click -> the tutor's final, settled answer).
    Only answered questions have a time; a failed one has none."""
    ms = [r["seconds"] * 1000 for r in records if r.get("ok") and r.get("seconds") is not None]
    if not ms:
        return {}, None
    s = lat_summarize(ms)
    print("\n" + "=" * 70)
    print(f"LATENCY (milliseconds) -- {s['n']} answered question(s)")
    print("=" * 70)
    print(f"{'stage':<12} | {'samples':<5} {'mean':>11} {'p50':>11} {'p95':>11} {'p99':>11} {'min':>11} {'max':>11}")
    print("-" * 70)
    lat_print_row("end-to-end", s)
    print("-" * 70)
    lat_slo_line("full answer", s["p95"], SLO_P95_MS)
    print("=" * 70)
    metrics = {f"e2e_{stat}_ms": s[stat] for stat in ("mean", "p50", "p95", "p99", "min", "max")}
    metrics["slo_e2e_pass"] = s["p95"] <= SLO_P95_MS
    return metrics, s


def reliability_metrics(records):
    """Success / error rate over every recorded question (answered and failed). No retry_rate: collect_prod.py does not
    silently retry a single question -- a failure is recorded as a failure (use --fill-gaps to deliberately re-ask it)."""
    total = len(records)
    good = sum(1 for r in records if r.get("ok"))
    rel_report(type("Rel", (), {"calls": total, "successes": good, "failures": total - good, "retries": 0})())
    if not total:
        return {}
    return {"total_requests": total, "success_rate": 100 * good / total, "error_rate": 100 * (total - good) / total, "retry_rate": 0.0}


def cost_metrics(records):
    """OUTPUT tokens only -- counted for real from the recorded answer text with the same tokenizer/pricing as
    tuitor_eval/eval_ops.py. This is a LOWER BOUND on the real per-query cost: input tokens (system prompt + retrieved
    book text, both invisible from outside a chat screen) are not included."""
    import tiktoken
    enc = tiktoken.encoding_for_model("gpt-4o-mini")
    lens = [len(enc.encode(r["answer"])) for r in records if r.get("ok") and r.get("answer")]
    if not lens:
        return {}
    avg_out = sum(lens) / len(lens)
    cost_usd = avg_out / 1_000_000 * PRICE_OUTPUT_PER_1M
    print("\n" + "=" * 70)
    print(f"COST -- OUTPUT TOKENS ONLY (gpt-4o-mini @ ${PRICE_OUTPUT_PER_1M}/1M out); n={len(lens)}")
    print("=" * 70)
    print(f"avg output tokens      : {avg_out:8.0f}")
    print(f"output-only cost/query : ${cost_usd:.6f}   (Rs {cost_usd * USD_TO_INR:.4f})")
    print("-" * 70)
    print("NOT included: input tokens (system prompt + retrieved book text) -- not visible from a live chat screen.")
    print("Real per-query cost is HIGHER than this. For the full picture: uv run python tuitor_eval/eval_ops.py")
    print("=" * 70)
    return {"avg_output_tokens": avg_out, "output_only_cost_per_query_usd": cost_usd, "output_only_cost_per_query_inr": cost_usd * USD_TO_INR}


class Provenance:
    """Fed to common.run_eval as `rag`: it has no .invoke() (nothing is called), only .describe(), so common.run_eval skips
    building a real Tuitor pipeline and uses this for the report's 'what was tested' rows instead."""
    def __init__(self, files, records, lat_n):
        self.files, self.records, self.lat_n = files, records, lat_n

    def describe(self):
        good = sum(1 for r in self.records if r.get("ok"))
        return [("What was tested", "the LIVE production Tuitor app, timed by the robot while it recorded answers (collect_prod.py)"),
                ("Recorded file(s)", ", ".join(f.name for f in self.files)),
                ("Questions used", f"{good}/{len(self.records)} answered (latency/cost use the {self.lat_n or 0} answered ones; "
                                   f"reliability uses all {len(self.records)})"),
                ("Cost", "OUTPUT tokens only -- see the Metrics sheet; input tokens are not visible from a live chat screen")]


def run(records):
    lat, lat_stats = latency_metrics(records)
    rel = reliability_metrics(records)
    cost = cost_metrics(records)
    if not lat and not rel:
        sys.exit("Nothing to measure: the recorded file has no questions.")
    metrics = {}
    metrics.update({f"{GROUP}.latency.{k}": v for k, v in lat.items()})
    metrics.update({f"{GROUP}.reliability.{k}": v for k, v in rel.items()})
    metrics.update({f"{GROUP}.cost.{k}": v for k, v in cost.items()})
    return metrics, lat_stats


def main():
    args = parse()
    paths = answer_files(args)
    records = load_records(paths, args.limit)
    print(f"Recorded file(s): {', '.join(p.name for p in paths)}  ->  {len(records)} question(s).")
    metrics, lat_stats = run(records)
    common.run_eval(args, "prod_ops", [], lambda _: metrics, targets={"slo_e2e_p95_ms": SLO_P95_MS},
                    rag=Provenance(paths, records, lat_stats["n"] if lat_stats else 0))
    if not args.limit:
        save_last_metrics("ops", metrics)
    else:
        print(f"\n(--limit {args.limit} was used, so this run is NOT saved for a baseline/candidate snapshot -- run without --limit for that.)")


if __name__ == "__main__":
    main()
