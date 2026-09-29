"""
Tuitor generator eval -- the main project's evals/eval_generator.py, using Tuitor's own prompt and model.
Gives Tuitor's answering step known-good text (no searching) and checks the answer:
    Faithfulness         does it stick to the text it was given?
    Answer Relevancy     does it address the question?

    uv run python tuitor_eval/eval_generator.py --limit 5
    uv run python tuitor_eval/eval_generator.py

Metric ids are the same as the main project's: generator.faithfulness.avg_score, ...
"""

import common                                   # must be first: isolates this run inside tuitor_eval/
from evals import eval_generator as main_eval

GROUP = "generator"


def modules():
    return [(GROUP, main_eval)]


def run(rag, args):
    from evals.harness import print_summary
    main_eval.GOLDEN_PATH = common.golden("generator_goldens.json")
    main_eval.generate = rag.generate                 # Tuitor's own prompt and model, not the project's generator
    common.limit_goldens(main_eval, args.limit)
    summary = main_eval.run()
    print_summary("Tuitor generator", summary)
    return common.flatten_nested(GROUP, summary)


def main():
    args = common.parse_args("Tuitor generator eval (Faithfulness / Answer Relevancy on given text).")
    common.run_eval(args, GROUP, modules(), lambda rag: run(rag, args))


if __name__ == "__main__":
    main()
