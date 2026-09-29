"""
Tuitor safety evals -- the main project's evals/eval_safety.py, pointed at your real Tuitor pipeline.

    uv run python tuitor_eval/eval_safety.py                       # scope + leakage + toxicity
    uv run python tuitor_eval/eval_safety.py --only scope          # any one, or two
    uv run python tuitor_eval/eval_safety.py --only scope toxicity

    scope      does it stay a tutor and decline off-topic requests and jailbreaks?
    leakage    does it keep its instructions private and never leak contact details? (judge + a no-judge email/phone check)
    toxicity   does it avoid harmful language? (lower is better)

Metric ids are the same as the main project's: safety.scope.pass_rate, safety.leakage.pii_regex_avg_score, ...
"""

import common                                   # must be first: isolates this run inside tuitor_eval/
from evals import eval_safety as main_eval

GROUP = "safety"
PARTS = ["scope", "leakage", "toxicity"]


def modules():
    return [(GROUP, main_eval)]


def run(rag, args):
    main_eval.SCOPE_GOLDEN_PATH = common.golden("scope_goldens_pdf.json")
    main_eval.LEAKAGE_GOLDEN_PATH = common.golden("leakage_goldens.json")
    main_eval.TOXICITY_GOLDEN_PATH = common.golden("toxicity_goldens.json")
    snapshot = main_eval.run_safety(rag, verbose=True, only=getattr(args, "safety_only", None) or PARTS)
    return {f"{GROUP}.{key}": value for key, value in snapshot.items()}


def main():
    parser = common.make_parser("Tuitor safety evals (scope, leakage, toxicity).", limit=False)
    parser.add_argument("--only", nargs="+", choices=PARTS, help="which safety eval(s) to run (default: all three)")
    args = parser.parse_args()
    args.safety_only = args.only
    label = GROUP if not args.only else "-".join(args.only)
    common.run_eval(args, label, modules(), lambda rag: run(rag, args))


if __name__ == "__main__":
    main()
