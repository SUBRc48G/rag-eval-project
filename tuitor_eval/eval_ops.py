"""
Tuitor ops eval -- speed, cost and reliability of your real Tuitor pipeline. No judge, no scoring: just measurements.
The main project's evals/eval_ops.py can only time this project's own generator, so this measures Tuitor's real steps
(wellbeing + privacy check -> search -> Tuitor's prompt on gpt-4o-mini -> redaction) and reuses its statistics, prices,
targets and report printing, so the metric ids match: ops.latency.e2e_p95_ms, ops.cost.cost_per_query_usd, ...

    uv run python tuitor_eval/eval_ops.py

    latency       how long a student waits (p50 / p95 / p99), split into check / search / answer
    cost          tokens x price per question, and a monthly projection
    reliability   how many requests succeed (with retries)

Close other heavy programs first: the timing is measured on this computer. Takes about 3-4 minutes and costs a few cents.
The real app does not stream its answer, so there is no "time to first token": the student waits for the whole answer.
Cost counts the answer call only (Tuitor's small wellbeing classifier call is not included).
"""

import time

import common                                   # must be first: isolates this run inside tuitor_eval/
from evals import eval_ops as main_ops

GROUP = "ops"


def modules():
    return []                                    # no judge, so nothing to wrap


def targets():
    return {"slo_e2e_p95_ms": main_ops.SLO_P95_MS, "slo_ttft_p95_ms": main_ops.SLO_TTFT_P95_MS,
            "cost_budget_usd": main_ops.COST_BUDGET_PER_QUERY_USD}


def latency(rag):
    print(f"[latency] warming up ({main_ops.LAT_WARMUP_RUNS} runs, discarded)...")
    for i in range(main_ops.LAT_WARMUP_RUNS):
        rag.invoke(main_ops.QUESTIONS[i % len(main_ops.QUESTIONS)])
    print("[latency] measuring...")
    res = {"total": [], "precheck": [], "retrieval": [], "generation": [], "ttft": [], "answer_len": []}
    for question in main_ops.QUESTIONS:
        for _ in range(main_ops.LAT_REPEATS):
            start = time.perf_counter()
            m = rag.measure(question)
            res["total"].append((time.perf_counter() - start) * 1000)
            res["precheck"].append(m["precheck_ms"])
            res["retrieval"].append(m["retrieval_ms"])
            res["generation"].append(m["generation_ms"])
            res["answer_len"].append(len(m["answer"]))
    main_ops.lat_report(res)

    total = main_ops.lat_summarize(res["total"])
    metrics = {"avg_answer_len": sum(res["answer_len"]) / len(res["answer_len"]), "slo_e2e_pass": total["p95"] <= main_ops.SLO_P95_MS}
    for stage, key in (("total", "e2e"), ("precheck", "precheck"), ("retrieval", "retrieval"), ("generation", "generation")):
        s = main_ops.lat_summarize(res[stage])
        for stat in ("mean", "p50", "p95", "p99", "min", "max"):
            metrics[f"{key}_{stat}_ms"] = s[stat]
    return metrics


def cost(rag):
    print("[cost] measuring token usage...")
    rows = []
    for question in main_ops.QUESTIONS:
        for _ in range(main_ops.COST_REPEATS):
            m = rag.measure(question)
            c = main_ops.cost_usd(m["input_tokens"], m["output_tokens"], m["cached_tokens"])
            rows.append({"input": m["input_tokens"], "output": m["output_tokens"], "cached": m["cached_tokens"],
                         **{f"cost_{k}": v for k, v in c.items()}})
    main_ops.cost_report(rows)
    avg = main_ops.col_avg(rows, "cost_total")
    out_share = 100 * main_ops.col_avg(rows, "cost_output") / avg if avg else 0.0
    return {"cost_per_query_usd": avg, "cost_per_query_inr": avg * main_ops.USD_TO_INR,
            "avg_input_tokens": main_ops.col_avg(rows, "input"), "avg_output_tokens": main_ops.col_avg(rows, "output"),
            "avg_cached_tokens": main_ops.col_avg(rows, "cached"), "output_cost_share_pct": out_share,
            "monthly_usd": avg * main_ops.QUERIES_PER_DAY * 30, "budget_pass": avg <= main_ops.COST_BUDGET_PER_QUERY_USD}


def reliability(rag):
    print("[reliability] measuring...")
    rel = main_ops.Reliability()
    for question in main_ops.QUESTIONS:
        for _ in range(main_ops.REL_REPEATS):
            main_ops.call_with_retries(lambda q=question: rag.invoke(q), rel)
    main_ops.rel_report(rel)
    calls = rel.calls or 1
    return {"total_requests": rel.calls, "success_rate": 100 * rel.successes / calls,
            "error_rate": 100 * rel.failures / calls, "retry_rate": 100 * rel.retries / calls}


def run(rag, args):
    metrics = {}
    for name, fn in (("latency", latency), ("cost", cost), ("reliability", reliability)):
        metrics.update({f"{GROUP}.{name}.{key}": value for key, value in fn(rag).items()})
    return metrics


def main():
    args = common.parse_args("Tuitor ops eval (latency / cost / reliability).", limit=False)
    common.run_eval(args, GROUP, modules(), lambda rag: run(rag, args), targets=targets())


if __name__ == "__main__":
    main()
