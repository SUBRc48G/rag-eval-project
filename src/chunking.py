"""Copied verbatim from Tuitor/chunking.py -- keep in sync so chunks match the Tuitor app.

Shared PDF chunking: cleans scan debris from page text, then splits on sentence boundaries.

Two things were making the tutor refuse questions whose answer WAS in the retrieved chunk:
- a chunk starting with a cut-off sentence tail ("say that a car is at rest or in motion, w"), and
- a rhetorical question addressed to the student in the same chunk ("How can the buildings ... remain at rest?").
Prompt rules did not fix either; removing them from the indexed text did. Sentence-aware splitting also stops
chunks starting mid-sentence (a fact used to get cut off from its subject, e.g. "...became the spiritual head").
"""
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

CHUNK_SIZE = 300
CHUNK_OVERLAP = 100

_SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+")
_STARTS_LOWERCASE = re.compile(r"[\s‘'\"“]*[a-z]")
# An interrogative sentence, up to its question mark even across abbreviations like "etc.".
_QUESTION = re.compile(
    r"(?:^|(?<=[.!?])\s+)(?:How|Why|What|Which|Where|When|Who|Whom|Whose|Can|Could|Do|Does|Did|Is|Are|Was|Were|Will|Would|Should)\b[^?]*\?"
)


def make_splitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP):
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", r"(?<=[.!?])\s+", "\n", " ", ""],
        is_separator_regex=True,
    )


def _drop_cut_head(paragraph):
    """A SHORT paragraph starting lowercase is the tail of a sentence from the previous page or column.
    Longer ones are kept whole: dropping their first sentence deleted real facts."""
    stripped = paragraph.strip()
    if len(stripped) < 80 and _STARTS_LOWERCASE.match(stripped):
        return ""
    return paragraph


def _drop_questions(paragraph):
    cleaned = _QUESTION.sub("", paragraph).strip()
    if cleaned == paragraph.strip():
        return paragraph
    sentences = _SENTENCE_GAP.split(cleaned)
    # a question cut off by a page break leaves a stray word behind ("... remain at rest? They")
    while sentences and len(sentences[-1]) < 20 and sentences[-1].rstrip()[-1:] not in ".!?":
        sentences.pop()
    return " ".join(sentences)


def clean_page_text(text):
    kept = []
    for paragraph in re.split(r"\n\s*\n", text):
        if not paragraph.strip():
            continue
        cleaned = _drop_questions(_drop_cut_head(paragraph))
        if cleaned.strip():
            kept.append(cleaned)
    return "\n\n".join(kept)


def chunk_documents(pages, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP):
    cleaned = [Document(page_content=clean_page_text(p.page_content), metadata=p.metadata) for p in pages]
    return make_splitter(chunk_size, chunk_overlap).split_documents([d for d in cleaned if d.page_content.strip()])
