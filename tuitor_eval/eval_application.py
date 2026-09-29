"""
Tuitor application eval -- the main project's evals/eval_application.py, pointed at your real Tuitor pipeline.
Compares Tuitor's answers with the expected answers:
    Correctness    are the facts right?
    Completeness   does it cover the key points?
    Style          is it a friendly, plain-language teaching voice?

    uv run python tuitor_eval/eval_application.py --limit 5
    uv run python tuitor_eval/eval_application.py

Metric ids are the same as the main project's: application.correctness_[geval].avg_score, ...
"""

import common                                   # must be first: isolates this run inside tuitor_eval/
from evals import eval_application as main_eval

GROUP = "application"


def modules():
    return [(GROUP, main_eval)]


def run(rag, args):
    from evals.harness import print_summary
    from tuitor_style import use_tuitor_style
    use_tuitor_style(main_eval)                 # judge Style by Tuitor's own voice, not the main project's lecture voice (in memory only)
    main_eval.GOLDEN_PATH = common.golden("testset.json")
    common.limit_goldens(main_eval, args.limit)
    summary = main_eval.run(rag)
    print_summary("Tuitor application", summary)
    return common.flatten_nested(GROUP, summary)


def main():
    args = common.parse_args("Tuitor application eval (Correctness / Completeness / Style).")
    common.run_eval(args, GROUP, modules(), lambda rag: run(rag, args))


if __name__ == "__main__":
    main()
