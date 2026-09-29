"""
Tuitor retriever eval -- the main project's evals/eval_retriever.py, pointed at your real Tuitor search.
Scores what Tuitor looks up (8 chunks, MMR search) against the expected answers:
    Contextual Recall      did it find what's needed to answer?
    Contextual Precision   is the useful text ranked near the top?

    uv run python tuitor_eval/eval_retriever.py --limit 5
    uv run python tuitor_eval/eval_retriever.py

Metric ids are the same as the main project's: retriever.contextual_recall.avg_score, ...
"""

import common                                   # must be first: isolates this run inside tuitor_eval/
from evals import eval_retriever as main_eval

GROUP = "retriever"


def modules():
    return [(GROUP, main_eval)]


def run(rag, args):
    from evals.harness import print_summary
    main_eval.GOLDEN_PATH = common.golden("testset.json")
    common.limit_goldens(main_eval, args.limit)
    summary = main_eval.run(rag.retriever)
    print_summary("Tuitor retriever", summary)
    return common.flatten_nested(GROUP, summary)


def main():
    args = common.parse_args("Tuitor retriever eval (Contextual Recall / Precision).")
    common.run_eval(args, GROUP, modules(), lambda rag: run(rag, args))


if __name__ == "__main__":
    main()
