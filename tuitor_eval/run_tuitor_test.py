"""
run_tuitor_test.py -- run SEVERAL Tuitor evals in one go and get ONE combined Excel report.

To run a single part on its own, use that part's own file instead (eval_safety.py, eval_rag_pipeline.py, ...):
see tuitor_eval/README.md. This file only chains them.

    uv run python tuitor_eval/run_tuitor_test.py --only pipeline safety
    uv run python tuitor_eval/run_tuitor_test.py --only retriever generator pipeline application --limit 5
    uv run python tuitor_eval/run_tuitor_test.py --only scope toxicity
"""

import importlib

import common                                   # must be first: isolates this run inside tuitor_eval/

PARTS = {"retriever": "eval_retriever", "generator": "eval_generator", "pipeline": "eval_rag_pipeline",
         "application": "eval_application", "safety": "eval_safety"}
SAFETY = ["scope", "leakage", "toxicity"]


def main():
    parser = common.make_parser("Run several Tuitor evals together into one report.")
    parser.add_argument("--only", nargs="+", choices=list(PARTS) + SAFETY, default=["pipeline"],
                        help="which parts to run: " + ", ".join(list(PARTS) + SAFETY) + " (default: pipeline)")
    args = parser.parse_args()

    named_safety = [g for g in args.only if g in SAFETY]
    args.safety_only = None if "safety" in args.only else (named_safety or None)      # 'safety' = all three
    wanted = [p for p in PARTS if p in args.only or (p == "safety" and named_safety)]

    parts = [importlib.import_module(PARTS[p]) for p in wanted]
    modules = [pair for part in parts for pair in part.modules()]

    def body(rag):
        metrics = {}
        for part in parts:
            metrics.update(part.run(rag, args))
        return metrics

    common.run_eval(args, "-".join(wanted), modules, body)


if __name__ == "__main__":
    main()
