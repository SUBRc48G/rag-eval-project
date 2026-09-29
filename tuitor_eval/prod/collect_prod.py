"""
collect_prod.py -- a robot that uses your LIVE Tuitor the way a student does, and records what it answers.
This is the ONLY file that talks to the production server. The evals then score the recorded file offline.

Run it in steps, smallest first (each step needs the one before it to have worked):

    --look                 open the page and take a screenshot. No login, no question, nothing is created.
    --inspect              log in, open the Chat Tutor page, print what it finds. No question is asked.
    --ask "question"       ask ONE question and print the answer.
    --set application      ask the study questions (add --limit 3 first). This is the safe set for a live app.
    --set scope|leakage|toxicity|wellbeing    the attack sets. They are refused unless you add --allow-attacks (see below).

Setup, once (PowerShell). The robot needs a browser, which is a ~150 MB download from Microsoft, kept outside your project:
    uv run --no-project --with playwright playwright install chromium
Every command is run like this (so nothing is added to the project's own environment):
    uv run --no-project --with playwright python tuitor_eval/prod/collect_prod.py --look

The class code never goes in this chat and never in a command. Put it in the settings file tuitor_eval/prod/.env.prod, on the line
    TUITOR_PASSCODE=the class code
(open it with:  notepad tuitor_eval\prod\.env.prod ). The robot only reads it and never prints it or copies it anywhere.
A real environment variable of the same name, if you set one in the terminal, wins over the file.
Also settable there:  TUITOR_PROD_URL   TUITOR_TEST_NAME (default ZZ-EvalBot: the student name the robot signs in as)

What it does to your live app (read this before --inspect):
  * signing in creates a student profile with the test name, so it may appear in your Class Dashboard; choose a name you can delete;
  * each question costs about a cent of OpenAI credit and appears in the app's logs / chat history, like any student's;
  * it clears the chat after every question so questions don't influence each other;
  * the attack sets send jailbreaks, "show me your instructions" attacks and fake phone numbers to the LIVE app. Don't, unless
    you own that risk; a staging copy is the right place for them;
  * the wellbeing set is different again: it sends self-harm / abuse-at-home / bullying style messages, to check the app's
    fixed caring reply. These are saved in the live chat history under the test student, same as any other message here.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from recorded import ANSWERS_DIR, ATTACK_SETS, PROD_DIR, PROD_PDFS, newest_answers, norm, question_sets

DEFAULT_URL = "https://tuitor-865994302493.asia-south1.run.app"
SCREENS = PROD_DIR / "screens"
ANSWER_TIMEOUT_MS = 240_000            # a slow answer is normal (the tutor is not instant); this is only the give-up point (4 minutes)
ANSWER_SETTLE_MS = 4_000               # the answer counts as finished only after its text has stopped changing for this long
_clock = time.perf_counter             # one place for "what time is it", so the waiting logic can be tested without really waiting
ROBOT_NOTE = "recorded by tuitor_eval/prod/collect_prod.py"


# ------------------------------------------------------------------ small pure helpers (unit-tested)
def clean_answer(text):
    """The text of a tutor bubble without its leading owl emoji and stray whitespace."""
    return re.sub(r"^\s*🦉\s*", "", text or "").strip()


_PLACEHOLDER = re.compile(r"^(thinking|typing|loading|working|generating|please wait|one moment|just a moment|hold on|let me think)\b", re.I)


def looks_unfinished(text):
    """True for an empty bubble, only dots, or a short 'Thinking...' style placeholder: the tutor has not really answered yet."""
    text = clean_answer(text)
    return not text.strip(" .…") or (len(text) < 40 and bool(_PLACEHOLDER.match(text)))


def parse_shelf(sidebar_text):
    """The book names in the sidebar's MY BOOK SHELF: every line that ends in .pdf, without the little page emoji in front."""
    return [re.sub(r"^[^\w]+", "", line.strip()) for line in (sidebar_text or "").splitlines() if line.strip().lower().endswith(".pdf")]


def shelf_gaps(shelf, wanted=None):
    """(books the test needs that the live shelf does not have, books on the shelf the test does not use)."""
    wanted = PROD_PDFS if wanted is None else wanted
    return [b for b in wanted if b not in shelf], [b for b in shelf if b not in wanted]


def pick_questions(sets, name, limit=None):
    qs = sets[name]
    return qs[:limit] if limit else qs


def refuse_attacks(set_name, allow_attacks):
    """A message when this set is an attack set and the user has not explicitly allowed it, else None."""
    if set_name in ATTACK_SETS and not allow_attacks:
        return (f"Refusing to send the '{set_name}' set to the LIVE app: it contains jailbreak / instruction-extraction / "
                f"personal-data prompts. Use a staging copy, or add --allow-attacks if you accept the risk.")
    return None


# ------------------------------------------------------------------ the robot (needs playwright)
ENV_FILE = PROD_DIR / ".env.prod"


def load_env_file(path):
    """KEY=VALUE lines from the small private settings file. Blank lines and # comments are ignored; quotes around a value are stripped."""
    values = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():        # utf-8-sig: Notepad may add an invisible marker
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def _settings():
    """(url, class code, test student name): a real environment variable wins, then the .env.prod file, then the default."""
    from_file = load_env_file(ENV_FILE)
    get = lambda key, default="": os.getenv(key) or from_file.get(key) or default
    return get("TUITOR_PROD_URL", DEFAULT_URL).rstrip("/"), get("TUITOR_PASSCODE"), get("TUITOR_TEST_NAME", "ZZ-EvalBot")


ISSUES = []                                            # things the browser complained about (errors, dropped connections), shown if the robot stops


def say(text):
    """One plain-words line about what the robot is doing right now, so a long run never looks frozen."""
    print(f">> {text}", flush=True)


def _launch(playwright, headed, slow_mo=0):
    browser = playwright.chromium.launch(headless=not headed, slow_mo=slow_mo)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.set_default_timeout(STEP_PATIENCE_MS)         # a slow first load is normal for this app; don't give up after the usual 30 s
    page = context.new_page()
    page.on("console", lambda m: ISSUES.append(f"browser {m.type}: {m.text[:140]}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: ISSUES.append(f"page error: {str(e)[:140]}"))
    page.on("requestfailed", lambda r: ISSUES.append(f"request failed: {r.url[:90]} ({r.failure})"))
    page.on("websocket", lambda ws: ws.on("close", lambda *_: ISSUES.append("the live connection to the app (websocket) CLOSED")))
    return browser, page


# Any one of these means the page has really drawn itself: the class-code box, the name box, the page menu, or the question box.
GATE_BOX = 'input[placeholder*="Enter class code" i]'      # the class-code box, found by ITS OWN label (the app also has a Teacher-login
                                                            # password box on the main screen, so "any password box" would be fooled by it)
NAME_BOX = 'input[placeholder*="Type your name" i]'
APP_SELECTOR = (GATE_BOX + ', ' + NAME_BOX + ', '
                '[data-testid="stSidebar"] [data-testid="stSelectbox"], input[placeholder*="press Enter to send" i]')


def _wait_ready(page):
    """Wait until the page has actually drawn something we recognise. Returns True if it did.
    (A fixed short pause was not enough on the first visit to a sleeping app: the page was still blank.)"""
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_selector(APP_SELECTOR, timeout=60_000)
        appeared = True
    except Exception:
        appeared = False                              # the caller's own checks will report what is (not) on screen
    page.wait_for_timeout(800)                        # let the last bits finish drawing
    return appeared


RUN_ID = datetime.now().strftime("%H%M%S")            # every picture from one run of the robot shares this, so they sort together
_shot_number = [0]


def screenshot(page, name):
    SCREENS.mkdir(parents=True, exist_ok=True)
    path = SCREENS / f"{name}_{datetime.now().strftime('%H%M%S')}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def snap(page, label, say=True):
    """Take a numbered picture of what the robot sees right now: screens/<run>_<n>_<label>.png. Never raises; a failed picture is only a note."""
    try:
        _shot_number[0] += 1
        SCREENS.mkdir(parents=True, exist_ok=True)
        path = SCREENS / f"{RUN_ID}_{_shot_number[0]:02d}_{label}.png"
        page.screenshot(path=str(path), full_page=True)
        if say:
            print(f"   [picture] {path.name}")
        return path
    except Exception as e:
        print(f"   [picture] could not take '{label}' ({type(e).__name__})")
        return None


def with_failure_picture(page, action):
    """Run action(); if ANYTHING stops it (a wrong code, a missing button, a timeout), take a picture of the screen first, then let it stop as before."""
    try:
        return action()
    except (SystemExit, Exception):
        print("The robot stopped. Here is what it was looking at:")
        snap(page, "STOPPED-here")
        try:                                           # notes that help find the cause; a note that fails is just skipped
            words = " ".join(page.locator("body").inner_text().split())[:260]
            print(f"   Text on the screen: {words!r}")
            alerts = [a.strip() for a in page.locator('[data-testid="stAlert"]').all_inner_texts() if a.strip()]
            print(f"   Messages the app is showing: {alerts or 'none'}")
        except Exception:
            pass
        if ISSUES:
            print("   Things the browser complained about (latest 6):")
            for issue in ISSUES[-6:]:
                print(f"     - {issue}")
        else:
            print("   The browser reported no errors and no dropped connections.")
        raise


UNLOCK_PATIENCE_MS = 300_000      # after Unlock the app may spend a couple of minutes loading its data; wait up to 5 minutes
STEP_PATIENCE_MS = 180_000        # the same idea for the later screens (name, chat page)


def app_is_busy(page):
    """True while Streamlit shows its 'Running...' indicator, i.e. the app is working on the last thing it was asked."""
    try:
        return bool(page.locator('[data-testid="stStatusWidget"]').count())
    except Exception:
        return False


def _gone(page, selector, milliseconds):
    """True once nothing on the page matches `selector`, waiting up to `milliseconds`."""
    waited = 0
    while waited < milliseconds:
        if not page.locator(selector).count():
            return True
        page.wait_for_timeout(500)
        waited += 500
    return False


def unlock_outcome(page, milliseconds, progress=False):
    """After submitting the class code, watch for up to `milliseconds`: 'ok' once the code box is gone, 'wrong' if the app says so, else None.
    progress=True prints a short note every 30 seconds so a long wait doesn't look like a hang."""
    waited = 0
    while waited < milliseconds:
        if not page.locator(GATE_BOX).count():
            return "ok"
        if page.get_by_text(re.compile("not quite right", re.I)).count():        # the app's own wrong-code message
            return "wrong"
        page.wait_for_timeout(500)
        waited += 500
        if progress and waited % 30_000 == 0:
            print(f"   ...still waiting ({waited // 1000}s). The app {'shows it is running' if app_is_busy(page) else 'shows no activity'}.")
    return None


def sign_in(page, url, passcode, student_name):
    """Open the app, pass the class-code screen and the 'Who are you?' screen. Returns what it found, for --inspect."""
    say(f"opening the live app: {url}")
    page.goto(url)
    _wait_ready(page)
    snap(page, "first-screen")
    found = {"class_code_screen": False, "name_screen": False}
    gate = page.locator(GATE_BOX)
    if gate.count():
        found["class_code_screen"] = True
        if not passcode:
            raise SystemExit("The app asks for the class code, and none is set. Open the settings file with:  notepad tuitor_eval\\prod\\.env.prod   "
                             "type the code after TUITOR_PASSCODE= , save the file, and run this again.")
        say("typing the class code (hidden) and pressing Enter")
        gate.first.fill(passcode)
        gate.first.press("Enter")                     # Enter locks in the typed value AND submits the form, like a student pressing Enter
        outcome = unlock_outcome(page, 20_000)        # a quick app answers within seconds
        if outcome is None and not app_is_busy(page):
            # No answer, and the app shows no sign of working: the submit may not have registered. Click the button as a second way.
            # (Never click while the app IS busy: Streamlit would restart the slow work it is doing.)
            print("   (no sign the app received it; clicking Unlock as a second way to submit)")
            page.get_by_role("button", name=re.compile("Unlock", re.I)).click(timeout=15_000)
            outcome = unlock_outcome(page, 20_000)
        if outcome is None:
            print(f"   The app is working on it. A big app can take a couple of minutes after Unlock; waiting up to "
                  f"{UNLOCK_PATIENCE_MS // 60_000} minutes...")
            outcome = unlock_outcome(page, UNLOCK_PATIENCE_MS, progress=True)
        if outcome == "wrong":
            raise SystemExit("The app said the class code is wrong (\"not quite right\"). Check the TUITOR_PASSCODE line in "
                             "tuitor_eval\\prod\\.env.prod: just the code, no spaces or quotes.")
        if outcome is None:
            raise SystemExit(f"The code screen was still showing after {UNLOCK_PATIENCE_MS // 60_000} minutes, and the app never said the "
                             f"code was wrong. See the picture and notes above.")
        _wait_ready(page)
        say("the class code was accepted")
        snap(page, "after-class-code")
    name_box = page.get_by_placeholder(re.compile("Type your name", re.I))
    if name_box.count():
        found["name_screen"] = True
        say(f"typing the student name '{student_name}' and pressing Enter")
        name_box.first.fill(student_name)
        name_box.first.press("Enter")                 # same as the class code: Enter submits the form; the button is only a backup
        if not _gone(page, NAME_BOX, 20_000) and not app_is_busy(page):
            print("   (no sign the app received the name; clicking LET'S GO as a second way to submit)")
            page.get_by_role("button", name=re.compile("LET.?S GO", re.I)).click(timeout=15_000)
        print("   (name sent; the app may take a while to load the next screen)")
        page.wait_for_selector(NAME_BOX, state="detached", timeout=STEP_PATIENCE_MS)  # the name screen goes away once accepted
        _wait_ready(page)
        say("the name was accepted")
        snap(page, "after-name")
    return found


def open_chat(page):
    """Choose 'Chat Tutor' in the page selector, then wait for the question box."""
    if page.get_by_placeholder(re.compile("press Enter to send", re.I)).count():
        return
    say("opening the Chat Tutor page from the menu")
    selector = page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first
    selector.click()
    snap(page, "page-menu-open")
    page.get_by_role("option", name=re.compile("Chat Tutor", re.I)).first.click()
    _wait_ready(page)
    page.get_by_placeholder(re.compile("press Enter to send", re.I)).first.wait_for(timeout=STEP_PATIENCE_MS)
    snap(page, "chat-page")


LAST_TIMING = {}                                       # the timing breakdown of the most recent question (seconds after clicking Send)


def _last_answer_text(page):
    """(how many tutor bubbles there are, the text of the newest one). Never raises: the app redraws the page while it works."""
    try:
        bubbles = page.locator("div.chat-ai")
        count = bubbles.count()
        return count, (clean_answer(bubbles.last.inner_text()) if count else "")
    except Exception:
        return 0, ""


def wait_for_answer(page, started, bubbles_before=0, text_before="", timeout_ms=ANSWER_TIMEOUT_MS, settle_ms=ANSWER_SETTLE_MS, quiet=False):
    """Wait until the tutor has REALLY finished answering, then return (answer text, seconds until the final text appeared).
    Finished means all three are true at once:
      1. the app's 'Running...' indicator is off (it is not still working),
      2. the newest tutor bubble is new, non-empty and not a 'Thinking...' placeholder,
      3. its text has stopped changing for `settle_ms` (so a half-written answer is never taken).
    If the indicator is stuck on but the text has been still for 30 seconds, the text is accepted anyway (with a note)."""
    deadline = _clock() + timeout_ms / 1000
    last_text, last_change, next_note = None, _clock(), _clock() + 15
    while _clock() < deadline:
        now = _clock()
        count, text = _last_answer_text(page)
        fresh = count > bubbles_before or (count > 0 and text != text_before)      # a bubble that was not there before we asked
        if not fresh:
            text = ""
        if text != last_text:
            last_text, last_change = text, now
        still = (now - last_change) * 1000
        busy = app_is_busy(page)
        real = bool(text) and not looks_unfinished(text)
        if real and not busy and still >= settle_ms:
            return text, last_change - started
        if real and busy and still >= 30_000:
            if not quiet:
                print("   (the app still shows 'running', but the answer has not changed for 30 seconds; taking it as final)")
            return text, last_change - started
        if not quiet and now >= next_note:
            state = "shows it is running" if busy else "shows no activity"
            print(f"   ...still waiting for the tutor's answer ({now - started:.0f}s). The app {state}.", flush=True)
            next_note = now + 15
        page.wait_for_timeout(500)
    raise TimeoutError(f"The tutor had not finished answering after {timeout_ms // 1000} seconds "
                       f"(last text seen: {(last_text or 'nothing')[:60]!r}).")


def read_shelf(page):
    """The books the live app lists under MY BOOK SHELF in its sidebar (read-only; a wrong or unreadable page just gives [])."""
    try:
        return parse_shelf(page.locator('[data-testid="stSidebar"]').first.inner_text())
    except Exception:
        return []


def ask_one(page, question, shots_per_question=True):
    """Type one question, WAIT for the tutor to finish answering, return (answer, seconds). Clears the chat afterwards.
    A picture of the answer is saved each time (screens/ is ignored by git, and is small); say=False keeps the printout quiet."""
    if shots_per_question:                                # (a whole set prints its own one-line-per-question summary instead)
        say(f"typing the question and pressing Send: {norm(question)[:70]}")
    box = page.get_by_placeholder(re.compile("press Enter to send", re.I)).first
    box.fill(norm(question))
    bubbles_before, text_before = _last_answer_text(page)      # whatever tutor bubbles are already on screen (e.g. a greeting) do not count as the answer
    started = _clock()
    page.get_by_role("button", name=re.compile("^Send", re.I)).click()
    clicked = _clock() - started
    if shots_per_question:
        say("question sent; waiting for the tutor to finish answering (it is not instant)")
    page.locator("div.chat-user").first.wait_for(timeout=ANSWER_TIMEOUT_MS)
    question_shown = _clock() - started
    answer, seconds = wait_for_answer(page, started, bubbles_before, text_before, quiet=not shots_per_question)
    # where the time went, so a number that looks too regular can be checked instead of guessed at
    LAST_TIMING.clear()
    LAST_TIMING.update(click_took=round(clicked, 2), question_shown_at=round(question_shown, 2), final_text_at=round(seconds, 2),
                       robot_stopped_waiting_at=round(_clock() - started, 2))
    snap(page, "answer", say=(shots_per_question or False))                  # the answer as it appears on screen; compare it with what was recorded
    LAST_TIMING["chat_cleared"] = clear_chat(page)        # the answer is already safely in hand; a stubborn Clear Chat must never lose it
    return answer, seconds


def clear_chat(page, attempts=3, patience_ms=20_000):
    """Click Clear Chat and wait until the conversation is really gone. Tries again if the click did not take (the app may be mid-redraw).
    Returns True when the chat is empty, False if it would not clear (the caller then starts a fresh session)."""
    for _ in range(attempts):
        if not page.locator("div.chat-user").count():
            return True
        clear = page.get_by_role("button", name=re.compile("Clear Chat", re.I))
        if clear.count():
            try:
                clear.first.click(timeout=10_000)
            except Exception:
                pass
        waited = 0
        while waited < patience_ms:
            if not page.locator("div.chat-user").count():
                return True
            page.wait_for_timeout(500)
            waited += 500
    return False


def collect(page, questions, set_name, meta):
    records = []
    for i, question in enumerate(questions, start=1):
        try:
            answer, seconds = ask_one(page, question, shots_per_question=False)      # still saves a picture, just doesn't print each one
            records.append({"question": question, "answer": answer, "seconds": round(seconds, 2), "ok": bool(answer), "timing": dict(LAST_TIMING)})
            if not LAST_TIMING.get("chat_cleared", True):     # the answer is kept; only the session is refreshed so the next question starts clean
                print("   (the chat would not clear; starting a fresh session for the next question. The answer above is kept.)")
                _reload(page, meta)
        except Exception as e:                        # one bad question must not lose the rest
            records.append({"question": question, "answer": "", "seconds": None, "ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"})
            _reload(page, meta)
        tag = "ok " if records[-1]["ok"] else "ERR"
        print(f"[{i}/{len(questions)}] {tag} {records[-1]['seconds'] or '-'}s  {norm(question)[:70]}")
    return records


def _reload(page, meta):
    """After a failed question: start a fresh session so the next one is not affected."""
    sign_in(page, meta["url"], meta["_passcode"], meta["student_name"])
    open_chat(page)


def failed_questions(records):
    """The questions that got no answer in a recorded run."""
    return [r["question"] for r in records if not r.get("ok", True)]


def merge_refilled(old_records, new_records):
    """The old run's records, in the old order, with each failed one replaced by its new answer (if the new attempt worked)."""
    fresh = {norm(r["question"]): r for r in new_records if r.get("ok")}
    return [fresh.get(norm(r["question"]), r) if not r.get("ok", True) else r for r in old_records]


def save(records, set_name, meta):
    ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
    path = ANSWERS_DIR / f"{set_name}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    public = {k: v for k, v in meta.items() if not k.startswith("_")}            # never write the class code to disk
    path.write_text(json.dumps({"meta": {**public, "set": set_name, "note": ROBOT_NOTE}, "answers": records}, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="Use the LIVE Tuitor like a student and record its answers.")
    parser.add_argument("--look", action="store_true", help="open the page and screenshot it (no login, nothing created)")
    parser.add_argument("--inspect", action="store_true", help="log in and open the Chat Tutor page; ask nothing")
    parser.add_argument("--ask", metavar="QUESTION", help="ask one question and print the answer")
    parser.add_argument("--set", choices=["application"] + ATTACK_SETS, help="ask a whole question set and record the answers")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions of the set")
    parser.add_argument("--fill-gaps", action="store_true",
                        help="with --set: re-ask ONLY the questions that failed in the newest recorded file of that set, and save a merged file")
    parser.add_argument("--allow-attacks", action="store_true", help="allow the attack sets (scope, leakage, toxicity, wellbeing) on the live app")
    parser.add_argument("--headed", action="store_true", help="show the browser window so you can watch")
    parser.add_argument("--watch", action="store_true",
                        help="the easy way to follow along: shows the browser window AND slows every click and keystroke down "
                             "(0.5 s each), and keeps the window open 8 seconds at the end so you can see the final screen")
    args = parser.parse_args()
    if not (args.look or args.inspect or args.ask or args.set):
        parser.error("pick one: --look, --inspect, --ask, or --set")
    if args.fill_gaps and not args.set:
        parser.error("--fill-gaps goes with --set, for example:  --set application --fill-gaps")
    if args.set and (msg := refuse_attacks(args.set, args.allow_attacks)):
        sys.exit(msg)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("The robot needs Playwright. Run it exactly like this:  uv run --no-project --with playwright python "
                 "tuitor_eval/prod/collect_prod.py ...   (and, once:  uv run --no-project --with playwright playwright install chromium)")

    url, passcode, student_name = _settings()
    meta = {"url": url, "student_name": student_name, "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S (local)"), "_passcode": passcode}
    with sync_playwright() as p:
        browser, page = _launch(p, args.headed or args.watch, slow_mo=500 if args.watch else 0)
        try:
            with_failure_picture(page, lambda: run_step(page, args, url, passcode, student_name, meta))
        finally:
            if args.watch:                                # leave the window up a moment so the final screen can be seen
                try:
                    print(">> (watch mode) leaving the window open for 8 seconds...", flush=True)
                    page.wait_for_timeout(8000)
                except Exception:
                    pass
            browser.close()


def run_step(page, args, url, passcode, student_name, meta):
    """The one thing the user asked for: --look, --inspect, --ask or --set."""
    if args.look:
        page.goto(url)
        drew = _wait_ready(page)
        saw_gate = bool(page.locator(GATE_BOX).count())
        print(f"Opened {url}\nPage title: {page.title()!r}\nThe page finished drawing: {drew}\nSaw a class-code box: {saw_gate}")
        print(f"Screenshot: {screenshot(page, 'look')}")
        return
    found = sign_in(page, url, passcode, student_name)
    print(f"Signed in. Screens seen: {found}")
    open_chat(page)
    shelf = read_shelf(page)
    print(f"Books on the live app's shelf ({len(shelf)}): {', '.join(shelf) or 'none found'}")
    missing, unused = shelf_gaps(shelf)
    if args.inspect or args.set == "application":
        if missing:
            print(f"   NOT on the live shelf, but the test expects them: {', '.join(missing)}")
        if unused:
            print(f"   On the live shelf, but no test question uses them (fine): {', '.join(unused)}")
        if not missing:
            print("   Every book the test expects is on the live shelf.")
    if args.set == "application" and missing:
        raise SystemExit("Stopping before asking anything: the questions were chosen for a shelf that has "
                         f"{', '.join(missing)}, and the live app does not. Asking about a missing book would mark a correct "
                         "\"I don't have that\" as wrong. Upload the missing book(s), or edit PROD_PDFS in tuitor_eval/prod/recorded.py "
                         "to match the real shelf. (If the shelf list above says 'none found', the robot could not read it; see the pictures.)")
    if args.inspect:
        print("Chat Tutor page is open. Question box found; nothing was asked.")
        print("Buttons seen:", [b.strip() for b in page.get_by_role("button").all_inner_texts() if b.strip()][:12])
        print(f"Pictures of every stage are in {SCREENS}")
        return
    if args.ask:
        answer, seconds = ask_one(page, args.ask)
        print(f"\nQ: {args.ask}\nA ({seconds:.1f}s): {answer}")
        print(f"Timing, in seconds after clicking Send: {LAST_TIMING}")
        return
    if args.fill_gaps:
        newest = newest_answers(args.set)
        if newest is None:
            raise SystemExit(f"No recorded '{args.set}' file yet, so there is nothing to fill. Record the set first (without --fill-gaps).")
        old = json.loads(newest.read_text(encoding="utf-8"))
        todo = failed_questions(old["answers"])
        if not todo:
            print(f"Nothing to fill: every answer in {newest.name} was recorded fine.")
            return
        print(f"Re-asking the {len(todo)} question(s) that failed in {newest.name} (the others are kept as they are)...")
        merged = merge_refilled(old["answers"], collect(page, todo, args.set, meta))
        still = len(failed_questions(merged))
        path = save(merged, args.set, {**old["meta"], "gaps_refilled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S (local)")})
        print(f"\nSaved the merged file -> {path}\n{len(merged) - still}/{len(merged)} answers are good" + (f"; {still} still failed." if still else "."))
        return
    questions = pick_questions(question_sets(), args.set, args.limit)
    print(f"Asking {len(questions)} '{args.set}' question(s) of the LIVE app as '{student_name}'..."
          + (" (only questions its shelf can answer)" if args.set == "application" else ""))
    records = collect(page, questions, args.set, meta)
    path = save(records, args.set, meta)
    bad = sum(1 for r in records if not r["ok"])
    print(f"\nRecorded {len(records) - bad}/{len(records)} answers -> {path}")
    if bad:
        print(f"{bad} question(s) got no answer; they are marked as failures in the file. Run them again if that matters.")


if __name__ == "__main__":
    main()
