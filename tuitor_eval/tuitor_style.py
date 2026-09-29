"""
tuitor_style.py -- the 'Style' judge, written for Tuitor's own voice.

Why this exists: the main project's Style judge rewards a CampusX-lecture voice (conversational prose with an analogy). Tuitor is not
that. Its own prompt (Tuitor.py, the chat template) tells the tutor to be "a helpful, friendly ICSE school tutor", to "explain clearly
for a school student", to "begin directly with the answer" (no "Great question!"), and to "stop as soon as the answer is complete"
(no closing encouragement or sign-off). Judging Tuitor by a lecture rubric marked every answer down for doing what it was told.

The rubric below is written from those instructions, NOT from looking at the answers, so it tests what the product promises.
It keeps the metric's name ('Style') and pass mark (0.7), so metric ids stay the same as the main project's:
    application.style_[geval].avg_score

Used by tuitor_eval/eval_application.py (local copy) and tuitor_eval/prod/eval_application.py (live app). It changes the judge in memory
only; the main project's evals/eval_application.py is not touched.
"""

from deepeval.metrics.g_eval import Rubric

STYLE_STEPS = [
    "Judge only the teaching style and tone of the actual output, not whether it is factually correct or complete.",
    "The speaker is a friendly school tutor answering a school student. Reward plain, clear language a school student can follow. "
    "Ordinary textbook terms are fine; a term the student may not know should be briefly explained or avoided.",
    "Reward an answer that starts directly with the answer. Penalize filler openers such as 'Great question!' or 'Sure, I'd be happy to help'.",
    "Reward an answer that stops when it is complete. Penalize closing sign-offs, extra encouragement or added remarks after the answer "
    "(for example 'Hope this helps!' or 'Keep learning!').",
    "Reward good organisation. A single clear sentence is right for a simple question. A short lead-in followed by a short list is right when "
    "the question asks for several points, features, types or differences. Do NOT penalize a list just for being a list.",
    "Penalize answers that are stiff, cold, robotic or bureaucratic, that ramble, that are confusing, that talk down to the student, "
    "or that lean on unexplained jargon.",
    "Do NOT reward or penalize based on correctness, completeness or length. Only style and tone.",
]

STYLE_RUBRIC = [
    Rubric(score_range=(9, 10), expected_outcome="Friendly and clear for a school student. Starts directly with the answer and stops when done, "
                                                 "with no filler opener or sign-off. Well organised (a list only where several points are asked for). "
                                                 "Any hard term is explained."),
    Rubric(score_range=(7, 8),  expected_outcome="Clear, direct and easy for a school student to follow. Perhaps a little plain or one term left "
                                                 "unexplained, but nothing that gets in the way. Fully acceptable."),
    Rubric(score_range=(4, 6),  expected_outcome="Understandable but noticeably stiff, confusing or jargon-heavy, or it uses a filler opener or "
                                                 "closing sign-off the tutor is told not to use."),
    Rubric(score_range=(0, 3),  expected_outcome="Cold, robotic, rambling or confusing, talks down to the student, or is mostly unexplained jargon."),
]

STYLE_SUMMARY = ("Tuitor's own rubric: a friendly school tutor; starts directly with the answer, stops when done (no filler opener or sign-off), "
                 "clear for a school student. Not the main project's CampusX-lecture rubric.")


def use_tuitor_style(main_eval):
    """Make a main-project eval module build its 'Style' judge with Tuitor's rubric. In memory only; safe to call twice."""
    if getattr(main_eval, "TUITOR_STYLE", False):
        return
    real_geval = main_eval.GEval

    def geval(**kwargs):
        if kwargs.get("name") == "Style":
            kwargs["evaluation_steps"] = STYLE_STEPS
            kwargs["rubric"] = STYLE_RUBRIC
        return real_geval(**kwargs)

    main_eval.GEval = geval
    main_eval.TUITOR_STYLE = True
