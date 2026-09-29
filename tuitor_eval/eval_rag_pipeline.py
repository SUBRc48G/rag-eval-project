"""
Tuitor pipeline eval -- the main project's evals/eval_rag_pipeline.py, pointed at your real Tuitor pipeline.
Each question goes through Tuitor's whole chain, then three checks score it:
    Contextual Relevancy   is the text Tuitor looked up actually about the question?
    Faithfulness           does the answer stick to what was looked up?
    Answer Relevancy       does the answer address the question?

    uv run python tuitor_eval/eval_rag_pipeline.py --limit 5     # small test
    uv run python tuitor_eval/eval_rag_pipeline.py               # all 34 questions

Metric ids are the same as the main project's: pipeline.contextual_relevancy.avg_score, ...
"""

import common                                   # must be first: isolates this run inside tuitor_eval/
from evals import eval_rag_pipeline as main_eval

GROUP = "pipeline"


def modules():
    return [(GROUP, main_eval)]


def run(rag, args):
    from evals.harness import print_summary
    main_eval.GOLDEN_PATH = common.golden("generator_goldens.json")
    common.limit_goldens(main_eval, args.limit)
    summary = main_eval.run(rag)
    print_summary("Tuitor pipeline", summary)
    return common.flatten_nested(GROUP, summary)


def main():
    args = common.parse_args("Tuitor pipeline eval (Contextual Relevancy / Faithfulness / Answer Relevancy).")
    common.run_eval(args, GROUP, modules(), lambda rag: run(rag, args))


if __name__ == "__main__":
    main()
