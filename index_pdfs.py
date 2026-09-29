"""
index_pdfs.py -- index the PDFs in data/uploads/ into chroma_db ahead of time.

The retriever does this itself the first time it's used (src/retriever.py: index_new_pdfs), so
this is only a convenience for building the store up front and seeing what happened. PDFs already
in the store (matched by filename) are skipped, and so are byte-identical copies of a file in the
same run, so it's safe to re-run. Nothing is ever deleted.

    uv run python index_pdfs.py --dry-run                                  # list what would be indexed, no embedding calls
    uv run python index_pdfs.py                                            # index every PDF in data/uploads not yet in chroma_db
    uv run python index_pdfs.py --pdf "data/uploads/physics_Energy.pdf"    # just this one

Run from the project root.
"""

import argparse

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from src.retriever import COLLECTION, DB_DIR, EMBEDDING_MODEL, index_new_pdfs


def main():
    parser = argparse.ArgumentParser(description=f"Add PDFs to {DB_DIR} (skips ones already indexed).")
    parser.add_argument("--pdf", action="append", default=[], help="index just this PDF (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="only list what would be indexed")
    args = parser.parse_args()

    # open the store directly (not load_store()) so an empty store is fine here
    store = Chroma(
        persist_directory=DB_DIR,
        embedding_function=OpenAIEmbeddings(model=EMBEDDING_MODEL),
        collection_name=COLLECTION,
    )
    before = store._collection.count()

    if args.dry_run:
        todo = index_new_pdfs(store, args.pdf, dry_run=True)
        print(f"\n{len(todo)} PDF(s) would be indexed into {DB_DIR} ({before} chunks now)")
        for path in todo:
            print(f"  - {path.name}")
        return

    added = index_new_pdfs(store, args.pdf)
    print(f"\nindexed {len(added)} PDF(s); {DB_DIR} went from {before} to {store._collection.count()} chunks")


if __name__ == "__main__":
    main()
