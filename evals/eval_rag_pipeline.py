# eval_rag_pipeline.py
from dotenv import load_dotenv

from deepeval import evaluate
from deepeval.evaluate.configs import CacheConfig
from deepeval.test_case import LLMTestCase
from deepeval.metrics import (
    FaithfulnessMetric,
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
)

from src.rag_pipeline import RagPipeline
from evals.harness import JUDGE_MODEL, load_goldens, summarize_by_metric, print_summary

load_dotenv()

GOLDEN_PATH = "goldens/generator_goldens.json"   # reuse the PDF queries (contexts here come from the pipeline)
THRESHOLD = 0.7


def run(rag):
    # 1. LOAD queries (we only need the queries --- context comes from the pipeline now)
    goldens = load_goldens(GOLDEN_PATH)

    # 2. RUN THE INJECTED PIPELINE per query, build a test case from LIVE output
    test_cases = []
    for g in goldens:
        result = rag.invoke(g["query"])          # retrieve -> rerank -> generate

        test_cases.append(
            LLMTestCase(
                input=g["query"],
                actual_output=result["answer"],       # what the generator produced
                retrieval_context=result["context"],  # what the RETRIEVER returned
            )
        )

    # 3. THE THREE TRIAD METRICS
    metrics = [
        ContextualRelevancyMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
        FaithfulnessMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
        AnswerRelevancyMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
    ]

    # 4. EVALUATE
    result = evaluate(
        test_cases=test_cases,
        metrics=metrics,
        cache_config=CacheConfig(write_cache=False, use_cache=False),   # the disk cache needs pywin32 on Windows
    )
    return summarize_by_metric(result)


def run_local():
    """Standalone convenience: build the pipeline, then run."""
    return run(RagPipeline())


if __name__ == "__main__":
    from evals.tracker import log_quality
    summary = run_local()
    print_summary("rag_pipeline", summary)
    log_quality("pipeline", summary)          # 'pipeline' = the namespace run_suite uses for these metrics