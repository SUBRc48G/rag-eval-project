"""
Check that your real Tuitor pipeline connects and answers. No scoring, about 1 cent.

    uv run python tuitor_eval/check_tuitor.py

The first time, it also makes a private copy of Tuitor's PDF data in tuitor_eval/data/chroma_db (your live Tuitor data is
only read). It then asks three study questions plus three safety probes and prints the answers.
"""

import json

import common                                   # must be first: isolates this run inside tuitor_eval/


def main():
    args = common.parse_args("Connection check for Tuitor's pipeline.", limit=False)
    rag = common.build_rag(args)

    load = lambda name: json.load(open(common.GOLDENS / name, encoding="utf-8"))
    study = load("generator_goldens.json")[:3]
    scope = {g["id"]: g for g in load("scope_goldens_pdf.json")}
    leak = {g["id"]: g for g in load("leakage_goldens.json")}
    probes = [("study question", g["query"]) for g in study] + [
        ("off-topic request (should decline)", scope["scope_06"]["input"]),
        ("prompt-extraction attack (should refuse)", leak["leak_prompt_01"]["input"]),
        ("asks for another student's contact details (should refuse)", leak["leak_pii_04"]["input"]),
    ]

    print(f"\nAsking Tuitor {len(probes)} questions (no scoring):\n")
    for kind, question in probes:
        result = rag.invoke(question)
        print(f"[{kind}]\nQ: {question[:140]}")
        print(f"   chunks retrieved: {len(result['context'])} | answer: {' '.join(result['answer'].split())[:230]}\n")
    print("Connected: Tuitor's pipeline answered. Next: run one part on its own, e.g. "
          "`uv run python tuitor_eval/eval_safety.py` (see tuitor_eval/README.md).")


if __name__ == "__main__":
    main()
