import hashlib
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.chunking import chunk_documents
from src.pdf_loader import load_pdf_with_ocr_fallback
from src.pii_scan import redact_pii

load_dotenv()  # loads OPENAI_API_KEY from .env

UPLOADS_DIR = "data/uploads"   # drop the PDFs to index here (same layout as Tuitor)
DB_DIR = "chroma_db"
COLLECTION = "pdf_documents"
EMBEDDING_MODEL = "text-embedding-3-small"   # same as Tuitor; changing it means rebuilding DB_DIR


def _key(name):
    # letters+digits only, so stray spaces / odd whitespace in a filename can't cause a false "not indexed"
    return re.sub(r"[^a-z0-9]", "", name.lower())


# 1. SYNC ---- index every PDF in the uploads folder that the store doesn't have yet.
#    Each PDF is read page by page (OCR fallback for scanned ones), then chunked exactly
#    like the Tuitor app (clean scan debris, 500-char sentence-aware chunks).
def index_new_pdfs(store, paths=None, dry_run=False):
    paths = [Path(p) for p in paths] if paths else sorted(Path(UPLOADS_DIR).glob("*.pdf"))

    existing = {
        _key(Path(m["source"]).name)
        for m in store.get(include=["metadatas"])["metadatas"]
        if m and m.get("source")
    }

    todo, seen = [], {}
    for path in paths:
        if _key(path.name) in existing:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            print(f"skip    {path.name}  (identical file to {seen[digest]})")
            continue
        seen[digest] = path.name
        todo.append(path)

    if dry_run:
        return todo

    added = []
    for path in todo:
        try:
            pages, used_ocr = load_pdf_with_ocr_fallback(path)
        except RuntimeError as e:
            print(f"FAILED  {path.name}: {e}")
            continue

        chunks = chunk_documents(pages)
        if not chunks:
            print(f"EMPTY   {path.name}: no text left after cleaning")
            continue

        # Contact info left in a PDF (e.g. a filled-in worksheet) is redacted, not indexed -- same as
        # Tuitor's upload. Only the matched span is replaced; the chunk itself is kept.
        pii_n, redacted = 0, []
        for chunk in chunks:
            clean, found = redact_pii(chunk.page_content)
            if found:
                pii_n += 1
                chunk = Document(page_content=clean, metadata=chunk.metadata)
            redacted.append(chunk)
        chunks = redacted

        store.add_documents(chunks, ids=[f"{path.name}_{i}" for i in range(len(chunks))])
        notes = (" (via OCR)" if used_ocr else "") + (f" -- {pii_n} chunk(s) had contact info redacted" if pii_n else "")
        print(f"indexed {path.name}: {len(chunks)} chunks{notes}")
        added.append(path)

    return added


# 2. OPEN ---- open the persisted store, embedding only PDFs that are new since last time
def load_store():
    store = Chroma(
        persist_directory=DB_DIR,
        embedding_function=OpenAIEmbeddings(model=EMBEDDING_MODEL),
        collection_name=COLLECTION,
    )

    index_new_pdfs(store)

    # Chroma silently creates an empty store if the folder is missing, and an empty
    # retriever quietly returns nothing -- fail loudly instead.
    if store._collection.count() == 0:
        raise RuntimeError(
            f"{DB_DIR}/ has no chunks. Put PDFs in {UPLOADS_DIR}/ "
            f"(and check the messages above for any that failed to index)."
        )

    return store


def build_retriever():
    return load_store().as_retriever(search_kwargs={"k": 5})


# 3. TRY IT ---- python src/retriever.py
if __name__ == "__main__":

    retriever = build_retriever()

    results = retriever.invoke("what is regression testing?")

    for r in results:
        print(f"[{Path(r.metadata['source']).name} p.{r.metadata['page']}] {r.page_content[:150]}...\n")
