# eval_retriever.py
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from deepeval import evaluate
from deepeval.evaluate.configs import CacheConfig
from deepeval.test_case import LLMTestCase
from deepeval.metrics import ContextualRecallMetric, ContextualPrecisionMetric

from src.reranker import RerankingRetriever
from src.retriever import EMBEDDING_MODEL
from evals.harness import JUDGE_MODEL, load_goldens, summarize_by_metric, print_summary

load_dotenv()

GOLDEN_PATH = "goldens/testset.json"   # questions about the PDFs in data/uploads

# Refusal goldens: the expected answer is "I can't answer this", so nothing in the corpus can
# support it and recall/precision are 0.00 by construction, whatever the retriever does. That
# behaviour belongs to the scope/refusal evals, not here. Matched by question text (not index)
# so reordering testset.json can't skip the wrong cases; the file itself is left untouched.
NO_ANSWER_QUESTIONS = {
    "Explain quantum entanglement in simple terms.",
    "What connects the ozone layer to the Delhi Sultanate?",
    "",   # empty input -> "Please provide a question"
}
THRESHOLD = 0.7


def run(retriever):
    # 1. LOAD the golden set --- the fixed, human-authored truth
    goldens = [g for g in load_goldens(GOLDEN_PATH) if g["question"] not in NO_ANSWER_QUESTIONS]

    # 2. RUN THE INJECTED RETRIEVER on each question to fill retrieval_context,
    #    then build one test case per golden.
    test_cases = []
    for g in goldens:
        retrieved = retriever.invoke(g["question"])
        retrieval_context = [doc.page_content for doc in retrieved]

        test_cases.append(
            LLMTestCase(
                input=g["question"],
                expected_output=g["ground_truth"],
                retrieval_context=retrieval_context,
                actual_output="(generator not evaluated in this run)",
            )
        )

    # 3. THE METRICS --- recall (did we miss?) and precision (ranked well?)
    metrics = [
        ContextualRecallMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
        ContextualPrecisionMetric(threshold=THRESHOLD, model=JUDGE_MODEL, include_reason=True),
    ]

    # 4. EVALUATE --- every metric on every case, batched + parallel, printed report.
    #    hyperparameters travel with the run so the report is tagged with the config.
    result = evaluate(
        test_cases=test_cases,
        metrics=metrics,
        hyperparameters={
            "retriever": "reranker",          # vs "reranked" when you swap it in
            "embedding_model": EMBEDDING_MODEL,
            "chunk_size": 500,       # chunking.py (same as Tuitor)
            "chunk_overlap": 100,
            "top_k": 2,              # RerankingRetriever default
            "judge_model": JUDGE_MODEL,
            "golden_set": GOLDEN_PATH,
        },
        cache_config=CacheConfig(write_cache=False, use_cache=False),
    )
    return summarize_by_metric(result)


def run_local():
    """Standalone convenience: build the retriever, then run."""
    return run(RerankingRetriever())


if __name__ == "__main__":
    from evals.tracker import log_quality
    summary = run_local()
    print_summary("retriever", summary)
    log_quality("retriever", summary)