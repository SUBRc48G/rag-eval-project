"""
recorded.py -- shared by the robot (collect_prod.py) and the offline evals (eval_application.py, eval_safety.py):

  question_sets()      the questions each eval asks (read from the project's goldens/, read only)
  RecordedTuitor       looks like Tuitor to the evals, but its answers come from a file the robot recorded from the live app

Scoring recorded answers means the LIVE app is never contacted by an eval: only the robot talks to it, once per question.
A live app shows a student only the answer, so recorded runs have no retrieved chunks. That limits what can be scored:
application and safety (answer text), and Answer Relevancy. Contextual Relevancy, Faithfulness, Recall / Precision, cost and
the stage-by-stage speed need the app to expose more than the answer, so they are not available here.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# Every prod file (the robot and all four evals) imports this module, so this runs once, early, everywhere: some
# Windows setups default a script's stdout to a codepage that cannot represent a rupee sign, an em dash or a curly
# quote -- all of which appear in real recorded questions and answers -- and printing one would otherwise crash the
# whole run with a UnicodeEncodeError. errors="replace" swaps an unprintable character for '?' rather than crashing;
# reconfigure() doesn't exist on every stream (a test's io.StringIO redirect, for one), so this is best-effort.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROD_DIR = Path(__file__).resolve().parent
ROOT = PROD_DIR.parent.parent
GOLDENS = ROOT / "goldens"
ANSWERS_DIR = PROD_DIR / "answers"
BASELINES_DIR = PROD_DIR / "baselines"
LAST_RUN_DIR = BASELINES_DIR / "last_run"          # each eval's own most recent metrics, so run_suite.py can gather all four
                                                    # up afterward without importing every eval into one process (see run_suite.py)


def save_last_metrics(label, metrics):
    """Called by each eval's own main() right after it scores something for real. Overwritten every run on purpose --
    this is scratch space for run_suite.py to read back, not a record of history (baselines/history/ is that)."""
    LAST_RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = LAST_RUN_DIR / f"{label}.json"
    path.write_text(json.dumps({"metrics": metrics, "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S (local)")}, indent=2), encoding="utf-8")
    return path


def load_last_metrics(label):
    """None if that part has never been scored for real (only --show, or not run at all)."""
    path = LAST_RUN_DIR / f"{label}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

# which sets are just study questions, and which are attack prompts (not sent to the live app unless you explicitly allow it)
STUDY_SETS = ["application"]
ATTACK_SETS = ["scope", "leakage", "toxicity", "wellbeing"]

SAFETY_GOLDEN_FILES = {"scope": "scope_goldens_pdf.json", "leakage": "leakage_goldens.json", "toxicity": "toxicity_goldens.json"}

# Extra scope-adherence cases from Tuitor's OWN prior red-teaming (Tuitor/security_tests/*.yaml), written with Tuitor's
# real purpose in mind (unlike goldens/scope_goldens_pdf.json's more generic set). Kept as a file in tuitor_eval/prod/,
# not in the shared goldens/, since it did not come from this project's own golden-building process. All expect DECLINE:
# other students' PII, exam-cheating help, harmful instructions, prompt extraction, and fabricating a fact not in the PDFs.
REDTEAM_GOLDENS_FILE = "tuitor_redteam_goldens.json"

# The curated wellbeing sample (12 of the 119 in Tuitor/security_tests/wellbeing_questions.json): does the live app give
# its FIXED caring/safety reply for a distressed or endangered student, and NOT give it for a question that only sounds
# similar (history about death, an idiom, a normal safety question)? Scored deterministically in eval_wellbeing.py --
# no judge, since the fixed reply text is known exactly. Also in tuitor_eval/prod/, for the same reason as the redteam file.
WELLBEING_GOLDENS_FILE = "wellbeing_goldens.json"


def _load_local(name):
    """A goldens-shaped file that lives in tuitor_eval/prod/ itself, not the shared project goldens/."""
    return json.loads((PROD_DIR / name).read_text(encoding="utf-8"))

# Safety-golden ids that do NOT fit THIS app's real content. Sending them to the live app would only prove "Tuitor
# refuses a question it can't answer" -- a real property, but not the leakage/toxicity property the id was meant to
# test -- so a pass there carries no real signal. Excluded from both what the robot asks (question_sets()) and what
# gets scored (safety_goldens()). goldens/ itself is untouched: this only changes what the LIVE APP is tested on.
NOT_APPLICABLE_IDS = {
    # Ask about "MMLU", "the RAG evaluation lecture", "reference-based vs reference-free evaluation", or "model evals
    # vs application evals" -- topics from a different course, not in ANY of Tuitor's 7 books. The live app just
    # says "I don't have that in your uploaded PDFs", which trivially passes without ever being asked to protect
    # real retrieved content or resist a toxic instruction while actually answering.
    "leak_prompt_04", "leak_content_01", "leak_content_02", "leak_content_03", "leak_content_04", "leak_content_05",
    "leak_pii_05", "tox_06", "tox_07", "tox_08", "tox_09", "tox_10",
    # Names a different app ("CampusX") that Tuitor has never heard of.
    "leak_pii_04",
    # Ask about "the Chapter 3 water cycle worksheet" / "Priya Sharma" -- that PDF (pii_test_worksheet.pdf) is
    # deliberately NOT on the live shelf, so there is no real PII there to leak; the check always trivially passes.
    "leak_pii_06", "leak_pii_07", "leak_pii_08",
}


def safety_goldens(part):
    """The golden rows for one safety set (scope/leakage/toxicity), with the ones that don't fit Tuitor's real
    content removed (see NOT_APPLICABLE_IDS). 'scope' also gets the extra Tuitor-specific redteam cases appended
    (same expected_action/success_criteria shape, so the existing scope judge scores them with no new code).
    Used by both collect_prod.py (what to ask) and eval_safety.py (what to score)."""
    rows = [g for g in _load(SAFETY_GOLDEN_FILES[part]) if g["id"] not in NOT_APPLICABLE_IDS]
    if part == "scope":
        rows = rows + _load_local(REDTEAM_GOLDENS_FILE)
    return rows


def wellbeing_goldens():
    """The 12 curated wellbeing test rows: {id, category, input, expect_caring_reply, ...}."""
    return _load_local(WELLBEING_GOLDENS_FILE)


def not_applicable_count(part):
    """How many of the part's golden rows were skipped as not fitting Tuitor's real content (for a plain 'N skipped' printout)."""
    return sum(1 for g in _load(SAFETY_GOLDEN_FILES[part]) if g["id"] in NOT_APPLICABLE_IDS)


def norm(text):
    """One-line, single-spaced form of a question. The live app has a one-line input box, so a question with line breaks
    is sent (and looked up again) in this form."""
    return " ".join(str(text).split())


def _load(name):
    return json.loads((GOLDENS / name).read_text(encoding="utf-8"))


# the PDFs on the LIVE app's book shelf (sidebar 'MY BOOK SHELF'). It can only answer from these, so asking it about any other book
# would score a correct "I don't know" as a wrong answer. Keep this list the same as the live shelf: the robot reads the real shelf
# before it asks anything and stops if a book listed here is missing from it.
PROD_PDFS = ["Science_The Cell.pdf", "Hindi_Mithai_wala.pdf", "Geography_atmosphere.pdf", "Physics_Force and pressure.pdf",
             "History_Rise_of_Christianity.pdf", "History-Spread of christianity.pdf", "Turkish_Invasion_Delhi_Sultanate_Notes.pdf"]


def _books(pdf_field):
    """The book names a test-set row is about ('A.pdf + B.pdf' -> both). [] for an 'All PDFs' row, which could be about any of them."""
    return [] if pdf_field.strip() == "All PDFs" else [part.strip() for part in pdf_field.split(" + ")]


def application_questions(goldens, shelf=None):
    """The study questions the live shelf can answer:
       - a question about named books needs ALL of those books on the shelf;
       - an 'All PDFs' question could be about any book, so it is asked only when the shelf holds every book the test set knows about;
       - empty questions are dropped (the live app ignores an empty box)."""
    shelf = set(PROD_PDFS if shelf is None else shelf)
    known = {book for g in goldens for book in _books(g.get("pdf", ""))}
    complete = known <= shelf
    return [g["question"] for g in goldens
            if g["question"].strip() and ((set(_books(g.get("pdf", ""))) <= shelf) if _books(g.get("pdf", "")) else complete)]


def question_sets():
    """{set_name: [question, ...]} exactly as the matching evals will ask them (empty questions removed: the app ignores them).
    'application' = only the study questions the live app's shelf (PROD_PDFS) can answer."""
    return {
        "application": application_questions(_load("testset.json")),
        "scope": [g["input"] for g in safety_goldens("scope")],
        "leakage": [g["input"] for g in safety_goldens("leakage")],
        "toxicity": [g["input"] for g in safety_goldens("toxicity")],
        "wellbeing": [g["input"] for g in wellbeing_goldens()],
    }


def newest_answers(set_name):
    """The most recent recorded-answers file for a set, or None."""
    files = sorted(ANSWERS_DIR.glob(f"{set_name}_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


class RecordedTuitor:
    """Replays answers recorded from the live app. invoke(question) -> {"query", "context", "answer"} like the real pipeline."""
    prechecks = True                                                    # the live app runs its own wellbeing / privacy checks

    def __init__(self, *paths):
        self.records, self.sources, self.meta = {}, [], {}
        for path in paths:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.sources.append(Path(path).name)
            self.meta = data.get("meta", self.meta)
            for rec in data["answers"]:
                self.records[norm(rec["question"])] = rec
        self.retriever = SimpleNamespace(top_k="unknown (live app)", fetch_k="unknown (live app)")
        self.llm = SimpleNamespace(model_name="unknown (live app)", temperature="unknown")
        failed = [r for r in self.records.values() if not r.get("ok", True)]
        if failed:
            print(f"[recorded] {len(failed)} of {len(self.records)} recorded answers are failures (the robot got no answer); "
                  f"they will score as failed answers. Re-run the robot for those.")

    def invoke(self, question):
        rec = self.records.get(norm(question))
        if rec is None:
            raise KeyError(f"No recorded answer for this question: {norm(question)[:90]!r}. Run the robot for its set first "
                           f"(collect_prod.py --set ...). Recorded files used: {', '.join(self.sources) or 'none'}.")
        answer = rec["answer"] if rec.get("ok", True) else f"[the robot could not get an answer from the live app: {rec.get('error', 'unknown')}]"
        return {"query": question, "context": [], "answer": answer}

    def describe(self):
        return [("What was tested", f"the LIVE production Tuitor app ({self.meta.get('url', 'address not recorded')}), scored offline from answers "
                                    f"recorded by the robot: {', '.join(self.sources)}"),
                ("Recorded", f"{self.meta.get('collected_at', '?')} as student '{self.meta.get('student_name', '?')}'"),
                ("Retrieved chunks", "not visible from outside the app, so Contextual Relevancy / Faithfulness / Recall / Precision are not scored"),
                ("Wellbeing / privacy pre-checks", "part of the live app")]
