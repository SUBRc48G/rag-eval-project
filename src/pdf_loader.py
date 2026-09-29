"""PDF loading, mirroring Tuitor.py's load_pdf_with_ocr_fallback (the app's own version).

Text-layer PDFs are read with PyPDFLoader. A scanned / image-only PDF has no text layer,
so PyPDFLoader returns empty pages; those fall back to OCR (pytesseract + pdf2image).
OCR is optional -- it's only needed, and only imported, when a scanned PDF turns up.

Optional env vars (Tuitor keeps these in .streamlit/secrets.toml):
    TESSERACT_CMD   full path to tesseract.exe if it isn't on PATH
    POPPLER_PATH    Poppler's bin folder if it isn't on PATH
"""

import os
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document

_DEFAULT_WIN_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def load_pdf_with_ocr_fallback(file_path, filename=None):
    """Load a PDF page by page. Returns (docs, used_ocr).

    Raises RuntimeError if the PDF is scanned and OCR isn't available or fails.
    """
    filename = filename or Path(file_path).name
    docs = PyPDFLoader(str(file_path)).load()

    total_chars = sum(len(d.page_content.strip()) for d in docs)
    # Fewer than ~20 chars per page on average means there's effectively no real
    # text layer (stray whitespace/artifacts aside).
    if total_chars / max(len(docs), 1) >= 20:
        return docs, False

    # --- Fallback to OCR ---
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        raise RuntimeError(
            f"'{filename}' looks like a scanned PDF (no text layer found), "
            f"but OCR support isn't installed. Run `uv add pytesseract pdf2image` "
            f"(plus the Tesseract-OCR and Poppler binaries) to index it."
        )

    tesseract_cmd = os.getenv("TESSERACT_CMD")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    elif Path(_DEFAULT_WIN_TESSERACT).exists():
        pytesseract.pytesseract.tesseract_cmd = _DEFAULT_WIN_TESSERACT

    print(f"  No text layer found in {filename} -- running OCR...")
    try:
        images = convert_from_path(str(file_path), poppler_path=os.getenv("POPPLER_PATH") or None)
    except Exception as e:
        raise RuntimeError(
            f"OCR needs Poppler, but it couldn't be found ({e}). "
            f"Install Poppler for Windows and either add it to PATH, or set "
            f"POPPLER_PATH=C:/path/to/poppler/Library/bin in .env."
        )

    ocr_docs = []
    for i, img in enumerate(images):
        try:
            # hin+eng, not just eng: notes come in Hindi script as well as English, and there is
            # no per-file language hint. Tesseract runs both language models together.
            text = pytesseract.image_to_string(img, lang="hin+eng")
        except pytesseract.pytesseract.TesseractNotFoundError:
            raise RuntimeError(
                f"OCR needs Tesseract, but it couldn't be found. Install it from the "
                f"UB-Mannheim Windows build and either add it to PATH, or set "
                f"TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe in .env."
            )
        except pytesseract.pytesseract.TesseractError as e:
            if "hin" in str(e).lower():
                raise RuntimeError(
                    "OCR needs the Hindi language pack, but it isn't installed. On the "
                    "UB-Mannheim Windows build, re-run the installer and tick 'Hindi' "
                    "under additional language data."
                )
            raise RuntimeError(f"OCR failed on '{filename}': {e}")
        if text.strip():
            ocr_docs.append(Document(page_content=text, metadata={"source": str(file_path), "page": i}))

    if not ocr_docs:
        raise RuntimeError(
            f"OCR ran on '{filename}' but couldn't extract any readable text. "
            f"The scan quality may be too low."
        )

    return ocr_docs, True
