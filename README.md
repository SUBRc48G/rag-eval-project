 # RAG Eval Project

RAG pipeline and evaluation suite for the course transcript data in `data/`.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. From the project root, create the environment and install the locked dependencies:

	```powershell
	uv sync
	```

3. Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.

The project targets Python 3.11, as specified by `.python-version`.

## Run the app

```powershell
uv run streamlit run src/app.py
```

Put the PDFs to index in `data/uploads/`. The first request builds the local
Chroma store in `chroma_db/` from every PDF there (and later requests add any new
ones), and downloads the cross-encoder model. Both are ignored by Git and can be
regenerated. To build the store up front, run `uv run python index_pdfs.py`.

## Run evaluations

Run the full evaluation suite from the project root:

```powershell
uv run python -m evals.run_suite
```

Run the test suite with:

```powershell
uv run pytest
```

All commands that access the model or embeddings require `OPENAI_API_KEY`.
