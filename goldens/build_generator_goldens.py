"""
build_generator_goldens.py -- give every testset.json question a golden context for the generator eval.

The generator eval feeds generate() a KNOWN-GOOD context (so a low score is the generator's fault, not
the retriever's). testset.json has questions + ground-truth answers but no context, so this builds it:
for each question, take the chunks in chroma_db nearest to the GROUND-TRUTH ANSWER (not the question --
the answer is what the context has to support), restricted to the PDF the question was written from.

That is a proxy for "the right chunks", not a certainty -- it's a DRAFT. Review the printed table (worst
matches first) and fix any context that doesn't actually support its answer before trusting the scores.

    uv run python goldens/build_generator_goldens.py
    -> goldens/generator_goldens.json   (id, query, ideal_context, ground_truth, source_pdfs, distances)
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retriever import UPLOADS_DIR, load_store
from evals.eval_retriever import NO_ANSWER_QUESTIONS

TESTSET_PATH = "goldens/testset.json"
OUT_PATH = "goldens/generator_goldens.json"
CONTEXT_CHUNKS = 3

# Questions whose answer is spread across several chunks. The 3 chunks nearest to the answer are not
# enough for these (e.g. no single chunk names all five layers of the atmosphere), so the context is
# extended until EVERY term below appears somewhere in it. Keyed by question text, not position.
# Terms are matched with letters only, so OCR spacing/punctuation can't hide a term.
REQUIRED_TERMS = {
    "What are the five layers of the atmosphere in order from the Earth's surface?":
        ["troposphere", "stratosphere", "mesosphere", "thermosphere", "exosphere"],
    "Compare the role of ATP in mitochondria with the role of gravity in determining an object's weight.":
        ["re:\\bATP\\b", "mitochondri", "gravity", "weight"],      # "re:" = a regex on the raw text (see _has)
}
MAX_CONTEXT_CHUNKS = 6     # never grow a context past this, however many terms are still missing


def _letters(s):
    return re.sub(r"[^a-z]", "", s.lower())


def _has(term, text):
    """Does the chunk contain the term? Long words: letters-only match (survives OCR spacing). A term written
    "re:<pattern>" is a regex on the raw text -- needed for SHORT terms like ATP, because stripping spaces glues
    words together ("...that persists..." -> "thatpersists" contains "atp")."""
    if term.startswith("re:"):
        return re.search(term[3:], text) is not None
    return _letters(term) in _letters(text)


def cover_terms(store, question, ground_truth, where, terms, start_hits):
    """Greedily pick chunks until every required term is present. Returns (hits, missing_terms)."""
    pool = list(start_hits)
    for query in [ground_truth] + [f"{t.removeprefix('re:').replace(chr(92) + 'b', '')} {question}" for t in terms]:   # also search per term: its chunk may rank low overall
        pool += (store.similarity_search_with_score(query, k=12, filter=where) if where
                 else store.similarity_search_with_score(query, k=12))
    seen, unique = set(), []
    for doc, dist in pool:                                                  # de-duplicate, keeping the first (closest) sighting
        if doc.page_content not in seen:
            seen.add(doc.page_content)
            unique.append((doc, dist))

    remaining, chosen = {t for t in terms}, []
    while remaining and len(chosen) < MAX_CONTEXT_CHUNKS:
        def gain(pair):
            return len({t for t in remaining if _has(t, pair[0].page_content)})
        best = max(unique, key=lambda p: (gain(p), -p[1]))                  # most new terms; ties -> closer chunk
        if gain(best) == 0:
            break
        chosen.append(best)
        unique.remove(best)
        remaining -= {t for t in remaining if _has(t, best[0].page_content)}
    chosen.sort(key=lambda p: (str(p[0].metadata.get("source")), p[0].metadata.get("page", 0)))   # read in page order
    return chosen, sorted(remaining)


def source_filter(pdf_field):
    """testset 'pdf' field -> Chroma metadata filter on the stored 'source' path (None = search everything)."""
    if pdf_field == "All PDFs":
        return None
    sources = [str(Path(UPLOADS_DIR) / name.strip()) for name in pdf_field.split(" + ")]
    return {"source": sources[0]} if len(sources) == 1 else {"source": {"$in": sources}}


def main():
    store = load_store()
    testset = json.load(open(TESTSET_PATH, encoding="utf-8"))

    rows = []
    for i, g in enumerate(testset):
        if g["question"] in NO_ANSWER_QUESTIONS:
            continue

        where = source_filter(g["pdf"])
        hits = store.similarity_search_with_score(g["ground_truth"], k=CONTEXT_CHUNKS, filter=where) if where else []
        fallback = not hits
        if fallback:      # "All PDFs", or the filter matched nothing -> search the whole store
            hits = store.similarity_search_with_score(g["ground_truth"], k=CONTEXT_CHUNKS)

        terms, missing = REQUIRED_TERMS.get(g["question"]), []
        if terms:
            hits, missing = cover_terms(store, g["question"], g["ground_truth"], where, terms, hits)

        rows.append({
            "id": f"t{i:02d}",
            "query": g["question"],
            "ideal_context": [doc.page_content for doc, _ in hits],
            "ground_truth": g["ground_truth"],
            "source_pdfs": sorted({Path(doc.metadata["source"]).name for doc, _ in hits}),
            "distances": [round(float(d), 3) for _, d in hits],
            "searched_all_pdfs": fallback,
            **({"required_terms": terms, "missing_terms": missing} if terms else {}),
        })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"wrote {len(rows)} goldens -> {OUT_PATH}  (skipped {len(testset) - len(rows)} refusal cases)")
    for r in rows:
        if "required_terms" in r:
            status = "ALL covered" if not r["missing_terms"] else f"STILL MISSING: {r['missing_terms']}"
            print(f"  {r['id']} spread-across-chunks question: {len(r['ideal_context'])} chunks from {r['source_pdfs']} -> {status}")
    print(f"\nREVIEW THESE FIRST -- weakest match to the ground-truth answer (lower distance = closer):")
    for r in sorted(rows, key=lambda r: r["distances"][0], reverse=True)[:8]:
        print(f"  {r['id']} best={r['distances'][0]:.3f} | {r['query'][:55]!r} -> {r['source_pdfs']}")


if __name__ == "__main__":
    main()
