"""
check_relevancy_judge.py -- does a stronger judge score Answer Relevancy more sensibly?

Background: DeepEval's Answer Relevancy splits an answer into statements, has the judge label each one
yes / idk / no against the QUESTION, and scores (statements not labelled "no") / (all statements). It has two known
misfires in our runs:
  1. a correct answer to a yes/no question scored 0.36 -- the judge called the statements explaining "no, he
     interpreted existing laws" irrelevant to "did he create new laws?" (golden t09)
  2. the fixed abstain sentence scores 0.00 in some runs and 1.00 in others (0 statements extracted -> 1.0 by default)

This scores the SAME answers several times under each judge model, so you can see whether the judge model matters
(and how much it wobbles) before touching the eval:

    uv run python -m evals.check_relevancy_judge
    uv run python -m evals.check_relevancy_judge --models gpt-4o-mini gpt-4.1-mini gpt-4.1 --repeats 5

Cases (answers generated once and shared by every judge, so the comparison is like-for-like):
    t09      a correct, faithful answer to a yes/no question        (the case that scored 0.36)
    control  a plain explanatory answer that should score high      (cell wall vs cell membrane)
    abstain  the generator's fixed "I don't have enough information..." sentence

Cost: about 90 small judge calls + 2 generator calls, roughly $0.02. This uses metric.measure() directly and turns
off Confident AI logging, so nothing here is uploaded.
"""

import argparse
import json

from dotenv import load_dotenv

from deepeval.metrics import AnswerRelevancyMetric
from deepeval.test_case import LLMTestCase

from src.generator import generate

load_dotenv()

GOLDEN_PATH = "goldens/generator_goldens.json"
ABSTAIN = "I don't have enough information in the course material to answer that."      # the generator prompt's fixed refusal
THRESHOLD = 0.7
DEFAULT_MODELS = ["gpt-4o-mini", "gpt-4.1-mini"]      # gpt-4o-mini = the old suite default, gpt-4.1-mini = the current one (harness.JUDGE_MODEL)


def _find(goldens, needle):
    return next(g for g in goldens if needle.lower() in g["query"].lower())


def build_cases():
    goldens = json.load(open(GOLDEN_PATH, encoding="utf-8"))
    t09 = _find(goldens, "entirely new laws")
    control = _find(goldens, "cell wall")
    layers = _find(goldens, "five layers of the atmosphere")
    return [
        ("t09 (yes/no, correct answer)", t09["query"], generate(t09["query"], t09["ideal_context"])),
        ("control (should score high)", control["query"], generate(control["query"], control["ideal_context"])),
        ("abstain (fixed refusal)", layers["query"], ABSTAIN),
    ]


def score_once(model, question, answer):
    """One Answer Relevancy measurement -> (score, statements, statements the judge labelled 'no')."""
    metric = AnswerRelevancyMetric(threshold=THRESHOLD, model=model, include_reason=False, async_mode=False)
    metric.measure(LLMTestCase(input=question, actual_output=answer), _show_indicator=False, _log_metric_to_confident=False)
    statements, verdicts = list(metric.statements), list(metric.verdicts)
    irrelevant = [s for s, v in zip(statements, verdicts) if str(v.verdict).strip().lower() == "no"]
    return metric.score, len(statements), irrelevant


def main():
    parser = argparse.ArgumentParser(description="Score the same answers repeatedly under different judge models.")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    print("Generating the answers once (shared by every judge)...")
    cases = build_cases()
    print(f"\nt09 answer used ({len(cases[0][2])} chars):\n{cases[0][2]}\n")

    print("=" * 96)
    print(f"{'case':<32}{'judge':<16}{'scores over ' + str(args.repeats) + ' runs':<38}{'mean':>6}{'pass@0.7':>10}{'stmts':>7}")
    print("-" * 96)
    shown = []
    for name, question, answer in cases:
        for model in args.models:
            scores, n_statements, first_irrelevant = [], None, None
            for _ in range(args.repeats):
                try:
                    score, n, irrelevant = score_once(model, question, answer)
                except Exception as e:      # a flaky judge call shouldn't lose the rest of the table
                    scores.append(f"ERR({type(e).__name__[:10]})")
                    continue
                scores.append(round(float(score), 2))      # float(): DeepEval returns an int 1 / 0 when it finds no statements
                if n_statements is None:
                    n_statements, first_irrelevant = n, irrelevant
            numeric = [s for s in scores if isinstance(s, float)]
            mean = f"{sum(numeric) / len(numeric):.2f}" if numeric else "-"
            passed = f"{sum(s >= THRESHOLD for s in numeric)}/{len(scores)}"
            print(f"{name:<32}{model:<16}{str(scores):<38}{mean:>6}{passed:>10}{n_statements if n_statements is not None else '-':>7}")
            shown.append((name, model, first_irrelevant))
    print("=" * 96)

    print("\nStatements each judge labelled IRRELEVANT ('no') in its first run:")
    for name, model, irrelevant in shown:
        if irrelevant:
            print(f"\n  {name} / {model}:")
            for s in irrelevant[:6]:
                print(f"     - {' '.join(str(s).split())[:140]}")
    print("\nHow to read it: for t09 and control, a good judge scores high and steadily. For abstain, 0.00 or 1.00 flipping between")
    print("runs means the metric cannot be trusted on refusals whichever judge you use. Each cell is one measurement, so a few")
    print("hundredths of difference between judges is noise; look for a consistent gap and for fewer 'irrelevant' statements.")


if __name__ == "__main__":
    main()
