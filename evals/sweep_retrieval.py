"""
sweep_retrieval.py -- compare retrieval settings (fetch_k, top_k) on the metrics they trade off.

Why: top_k=2 raised Contextual Relevancy (less unrelated text in the context) but it cannot answer questions whose
answer is spread over several chunks -- e.g. "the five layers of the atmosphere", where no single chunk names all
five and one layer's best chunk ranks 11th, outside the default fetch_k of 10. This runs the SAME pipeline under
several (fetch_k, top_k) pairs and puts the results side by side.

Per variant it runs
    application eval  -> Correctness, Completeness, Style      (does the whole app answer well?)
    pipeline eval     -> Contextual Relevancy, Faithfulness, Answer Relevancy   (how noisy is the context?)
    retriever eval    -> Contextual Recall, Contextual Precision                (skip with --no-retriever)
and reports whether two watched questions (the atmosphere layers, ATP vs gravity) were answered correctly.

    uv run python -m evals.sweep_retrieval                          # (10,2) (10,5) (20,5) (20,6)
    uv run python -m evals.sweep_retrieval --variants 10,2 15,4     # your own fetch_k,top_k pairs
    uv run python -m evals.sweep_retrieval --no-retriever           # cheaper: skip the retriever eval

Every variant is also logged to reports/eval_tracker.xlsx as its own run (sweep_f<fetch>_k<top>), so the columns
sit next to your baseline. Costs roughly $0.10-0.15 per variant (mostly the pipeline eval's judge calls). Each
variant is scored once and the judge wobbles by a few hundredths, so treat differences under ~0.05 as noise.
"""

import argparse

from dotenv import load_dotenv

from src.rag_pipeline import RagPipeline
from evals import eval_application, eval_rag_pipeline, eval_retriever
from evals.report_xlsx import CaseCollector
from evals.tracker import log_quality

load_dotenv()

DEFAULT_VARIANTS = [(10, 2), (10, 5), (20, 5), (20, 6)]     # (10, 2) is the current setting = the baseline to beat

# Questions this sweep is meant to fix. Matched by a phrase of the question; reported via the application eval's Correctness verdict.
WATCH = {
    "atmosphere layers": "five layers of the atmosphere",
    "ATP vs gravity": "ATP in mitochondria",
}


def parse_variants(items):
    if not items:
        return list(DEFAULT_VARIANTS)
    out = []
    for item in items:
        fetch_k, top_k = (int(x) for x in item.split(","))
        out.append((fetch_k, top_k))
    return out


def _avg(summary, prefix):
    """Average score of the metric whose name starts with `prefix` (e.g. 'Correctness' matches 'Correctness [GEval]')."""
    for name, stats in summary.items():
        if name.startswith(prefix):
            return stats["avg_score"]
    return float("nan")


def run_variant(rag, fetch_k, top_k, with_retriever=True):
    rag.retriever.fetch_k, rag.retriever.top_k = fetch_k, top_k
    collector = CaseCollector()
    with collector.capturing([("application", eval_application), ("pipeline", eval_rag_pipeline)]):
        app = eval_application.run(rag)
        pipe = eval_rag_pipeline.run(rag)
    ret = eval_retriever.run(rag.retriever) if with_retriever else {}

    watch = {}
    for label, needle in WATCH.items():
        rows = [r for r in collector.rows
                if r["group"] == "application" and r["metric"].startswith("Correctness") and needle in r["input"]]
        watch[label] = rows[0]["result"] if rows else "n/a"

    contexts = {r["case"]: len(r["context"]) for r in collector.rows if r["group"] == "pipeline"}   # one entry per case
    return {"application": app, "pipeline": pipe, "retriever": ret, "watch": watch,
            "ctx_chars": sum(contexts.values()) / len(contexts) if contexts else float("nan")}


def print_table(results):
    cols = [("Correct.", lambda r: _avg(r["application"], "Correctness")), ("Complete.", lambda r: _avg(r["application"], "Completeness")),
            ("CtxRelev.", lambda r: _avg(r["pipeline"], "Contextual Relevancy")), ("Faithful.", lambda r: _avg(r["pipeline"], "Faithfulness")),
            ("AnsRelev.", lambda r: _avg(r["pipeline"], "Answer Relevancy")), ("Recall", lambda r: _avg(r["retriever"], "Contextual Recall")),
            ("Precis.", lambda r: _avg(r["retriever"], "Contextual Precision"))]
    watched = list(WATCH)
    head = f"{'fetch_k,top_k':<14}" + "".join(f"{name:>10}" for name, _ in cols) + f"{'ctx chars':>10}" + "".join(f"  {w:<18}" for w in watched)
    print("\n" + "=" * len(head))
    print(head)
    print("-" * len(head))
    for (fetch_k, top_k), r in results.items():
        if isinstance(r, str):
            print(f"{f'{fetch_k},{top_k}':<14}FAILED: {r}")
            continue
        cells = "".join(f"{fn(r):>10.3f}" for _, fn in cols)
        print(f"{f'{fetch_k},{top_k}':<14}{cells}{r['ctx_chars']:>10,.0f}" + "".join(f"  {r['watch'][w]:<18}" for w in watched))
    print("=" * len(head))
    print("Averages of 0-1 judge scores. Each variant was scored once: differences under ~0.05 are noise.")
    print("'watch' columns = the application eval's Correctness verdict on that question (PASS / FAIL / ERROR).")


def main():
    parser = argparse.ArgumentParser(description="Compare (fetch_k, top_k) retrieval settings on the eval metrics they trade off.")
    parser.add_argument("--variants", nargs="+", metavar="FETCH,TOP", help="e.g. 10,2 20,5 (default: 10,2 10,5 20,5 20,6)")
    parser.add_argument("--no-retriever", action="store_true", help="skip the retriever eval (Recall / Precision) to save a little")
    args = parser.parse_args()

    variants = parse_variants(args.variants)
    print("Building pipeline (once)...")
    rag = RagPipeline()
    original = (rag.retriever.fetch_k, rag.retriever.top_k)

    results = {}
    try:
        for i, (fetch_k, top_k) in enumerate(variants, start=1):
            print(f"\n=== variant {i}/{len(variants)}: fetch_k={fetch_k}, top_k={top_k} ===")
            try:
                r = run_variant(rag, fetch_k, top_k, with_retriever=not args.no_retriever)
            except Exception as e:      # one flaky judge call must not lose the variants already scored
                results[(fetch_k, top_k)] = f"{type(e).__name__}: {str(e)[:100]}"
                print(f"variant failed: {results[(fetch_k, top_k)]}")
                continue
            results[(fetch_k, top_k)] = r
            run_id, label = f"sweep_f{fetch_k}_k{top_k}", f"retrieval sweep: fetch_k={fetch_k}, top_k={top_k}"
            for namespace in ("application", "pipeline", "retriever"):
                if r[namespace]:
                    log_quality(namespace, r[namespace], source="sweep", run_id=run_id, label=label)
    finally:
        rag.retriever.fetch_k, rag.retriever.top_k = original

    print_table(results)


if __name__ == "__main__":
    main()
