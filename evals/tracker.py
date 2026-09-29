"""
tracker.py -- append eval results to reports/eval_tracker.xlsx so runs are easy to track and compare.

Every eval calls log_quality() / log_flat() / log() at the end of its own run, and run_suite calls log().
Nothing here scores anything; it only records numbers the evals already produced.

Sheets (README explains them inside the workbook):
  Trend   one row per metric, one column per run; Baseline / Latest / Change / Status are FORMULAS.
          Status applies the same direction + tolerance rules as evals/metric_registry.py.
  Runs    the raw data, one row per (run, metric). Append-only source for Trend.
  RunLog  one row per run: label, when, which evals reported into it, and a Notes column for you.
  History every value ever logged with its exact timestamp; append-only, so reruns never erase earlier numbers.
  README  how to read it.

Every update also writes a full timestamped copy to reports/history/eval_tracker_<date>_<time>.xlsx, so the
main file being rewritten never loses an earlier state.

What counts as "a run": everything logged with the same run id. The run id is $EVAL_RUN if set,
otherwise today's date -- so the evals you run on the same day land in ONE column of the Trend sheet,
and re-running an eval within that run replaces its earlier numbers. Set EVAL_RUN to keep a rerun or an
experiment apart:

    $env:EVAL_RUN = "k3-experiment"      # PowerShell
    $env:EVAL_LABEL = "top_k=3, judge gpt-4.1-mini"   # optional free-text description of the run

If the workbook is open in Excel (Windows locks it), the numbers are saved to reports/pending.jsonl instead
and merged in automatically the next time something is logged -- an eval never fails because of this.
"""

import json
import math
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from evals.metric_registry import rule_for

TRACKER_PATH = "reports/eval_tracker.xlsx"
PENDING_PATH = "reports/pending.jsonl"
HISTORY_DIRNAME = "history"      # reports/history/ holds a timestamped copy of the workbook after every update

GROUP_ORDER = ["retriever", "generator", "pipeline", "application", "safety", "ops"]
FONT = "Arial"

# Trend sheet layout: static columns, then one column per run.
HEADER_ROW, LABEL_ROW, FIRST_DATA_ROW = 4, 3, 5
STATIC_HEADERS = ["Metric id", "Group", "Metric", "Stat", "Better", "Kind", "Tol", "Rel tol",
                  "Baseline", "Latest", "Change", "Status"]
FIRST_RUN_COL = len(STATIC_HEADERS) + 1          # column M
ROW_MARGIN = 500                                 # spare rows kept inside the lookup ranges on Runs


# ---------------------------------------------------------------- public API
def _slug(name):
    return name.strip().lower().replace(" ", "_")     # same rule as run_suite._slug -> same metric ids


def log_quality(namespace, summary, source=None, **kw):
    """summarize_by_metric() output {metric: {stat: val}} -> ids like 'retriever.contextual_recall.avg_score'."""
    metrics = {f"{namespace}.{_slug(metric)}.{stat}": val
               for metric, stats in summary.items() for stat, val in stats.items()}
    return log(metrics, source or namespace, **kw)


def log_flat(namespace, flat, source=None, **kw):
    """Already-flat {'sub.stat': val} (safety / ops) -> 'namespace.sub.stat'."""
    return log({f"{namespace}.{k}": v for k, v in flat.items()}, source or namespace, **kw)


def log(metrics, source, run_id=None, label=None, notes=None, path=TRACKER_PATH):
    """Record {metric_id: number} into the workbook. Never raises: on any problem it says so and moves on."""
    clean = {}
    for mid, val in metrics.items():
        if isinstance(val, bool):
            val = int(val)
        if isinstance(val, (int, float)) and not (isinstance(val, float) and math.isnan(val)):
            clean[mid] = float(val)
    if not clean:
        return None

    entry = {
        "run_id": run_id or os.getenv("EVAL_RUN") or datetime.now().strftime("%Y-%m-%d"),
        "label": label if label is not None else os.getenv("EVAL_LABEL", ""),
        "source": source,
        "notes": notes or "",
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": clean,
    }
    pending = _read_pending(path)
    try:
        _write_workbook(path, pending + [entry])
    except PermissionError:
        _write_pending(path, pending + [entry])
        print(f"[tracker] {path} is open in Excel -- saved {len(clean)} metrics to {PENDING_PATH}; "
              f"they will be added next time you log with the file closed.")
        return None
    except Exception as e:          # tracking must never break an eval that already finished
        _write_pending(path, pending + [entry])
        print(f"[tracker] could not update {path} ({type(e).__name__}: {e}); saved to {PENDING_PATH} instead.")
        return None

    _clear_pending(path)
    snapshot = _snapshot(path)
    print(f"[tracker] logged {len(clean)} metrics to {path}  (run '{entry['run_id']}', source '{source}')")
    if snapshot:
        print(f"[tracker] timestamped copy kept: {snapshot}")
    return path


def _snapshot(path):
    """Copy the just-written workbook to reports/history/<name>_<local date>_<time>.xlsx, so no earlier state is lost."""
    try:
        src = Path(path)
        dest_dir = src.parent / HISTORY_DIRNAME
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest, n = dest_dir / f"{src.stem}_{stamp}{src.suffix}", 2
        while dest.exists():                                   # two updates inside one second
            dest, n = dest_dir / f"{src.stem}_{stamp}-{n}{src.suffix}", n + 1
        shutil.copy2(src, dest)
        return str(dest).replace("\\", "/")
    except Exception as e:                                     # the main workbook is already saved; never fail an eval over a copy
        print(f"[tracker] could not save the timestamped copy ({type(e).__name__}: {e}); the main workbook was updated.")
        return None


# ---------------------------------------------------------------- pending file (workbook locked)
def _pending_file(path):
    return Path(path).parent / Path(PENDING_PATH).name


def _read_pending(path):
    f = _pending_file(path)
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_pending(path, entries):
    f = _pending_file(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _clear_pending(path):
    f = _pending_file(path)
    if f.exists():
        f.unlink()


# ---------------------------------------------------------------- reading the existing workbook
def _load_state(path):
    """Existing data -> (runs {(run_id, metric_id): [value, at, source]}, run_log {run_id: {...}},
    history [(logged_at, run_id, source, metric_id, value)] -- append-only, every value ever logged)."""
    runs, run_log, history = {}, {}, []
    if not Path(path).exists():
        return runs, run_log, history
    wb = load_workbook(path)
    if "Runs" in wb.sheetnames:
        for run_id, mid, val, at, src, *_ in wb["Runs"].iter_rows(min_row=2, values_only=True):
            if run_id is not None and mid is not None and val is not None:
                runs[(str(run_id), mid)] = [val, at, src]
    if "RunLog" in wb.sheetnames:
        for run_id, label, at, sources, _n, notes in wb["RunLog"].iter_rows(min_row=2, max_col=6, values_only=True):
            if run_id is not None:
                run_log[str(run_id)] = {"label": label or "", "at": at or "", "sources": [s for s in (sources or "").split(", ") if s],
                                         "notes": notes or ""}
    if "History" in wb.sheetnames:
        for at, run_id, src, mid, val in wb["History"].iter_rows(min_row=2, max_col=5, values_only=True):
            if mid is not None and val is not None:
                history.append((at, str(run_id), src, mid, val))
    else:       # a workbook from before History existed: seed it with the values it currently holds
        history = sorted(((at, rid, src, mid, val) for (rid, mid), (val, at, src) in runs.items()),
                         key=lambda h: (str(h[0]), h[1], h[3]))
    return runs, run_log, history


def _merge(runs, run_log, entries):
    for e in entries:
        rid = str(e["run_id"])
        for mid, val in e["metrics"].items():
            runs[(rid, mid)] = [val, e["at"], e["source"]]          # upsert: newest numbers win
        log = run_log.setdefault(rid, {"label": "", "at": "", "sources": [], "notes": ""})
        log["at"] = e["at"]
        if e["label"]:
            log["label"] = e["label"]
        if e["notes"]:
            log["notes"] = e["notes"]
        if e["source"] not in log["sources"]:
            log["sources"].append(e["source"])


# ---------------------------------------------------------------- writing the workbook
def _write_workbook(path, entries):
    runs, run_log, history = _load_state(path)
    _merge(runs, run_log, entries)
    history.extend((e["at"], str(e["run_id"]), e["source"], mid, val) for e in entries for mid, val in e["metrics"].items())

    wb = Workbook()
    wb.remove(wb.active)
    order = _run_order(run_log)
    _sheet_readme(wb)
    _sheet_trend(wb, runs, order)
    _sheet_runs(wb, runs)
    _sheet_runlog(wb, run_log, order, len(runs))
    _sheet_history(wb, history)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _run_order(run_log):
    return sorted(run_log, key=lambda r: (run_log[r]["at"], r))          # oldest -> newest


def _metric_sort_key(mid):
    group = mid.split(".")[0]
    return (GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER), mid)


def _style():
    return {
        "head_fill": PatternFill("solid", fgColor="1F3864"),
        "head_font": Font(name=FONT, bold=True, color="FFFFFF", size=10),
        "body": Font(name=FONT, size=10),
        "bold": Font(name=FONT, size=10, bold=True),
        "muted": Font(name=FONT, size=9, italic=True, color="595959"),
        "input_fill": PatternFill("solid", fgColor="FFF2CC"),
        "band": PatternFill("solid", fgColor="F2F2F2"),
        "thin": Border(bottom=Side(style="thin", color="D9D9D9")),
    }


def _number_format(mid):
    if mid.endswith("_usd"):
        return "$0.000000"
    if mid.endswith("_inr"):
        return "0.0000"
    if mid.endswith("_ms"):
        return "#,##0"
    if mid.endswith("pass_rate") or mid.endswith("_rate") or mid.endswith("_pct"):
        return "0.0"
    if mid.endswith(".n") or mid.endswith("_n") or mid.endswith("_tokens") or mid.endswith("_len") \
            or mid.endswith("_leaks") or mid.endswith("_requests") or mid.endswith("_pass"):
        return "#,##0"
    return "0.000"


def _nice_name(mid):
    parts = mid.split(".")
    group = parts[0]
    stat = parts[-1]
    middle = ".".join(parts[1:-1]) if len(parts) > 2 else ""
    name = middle.replace("_", " ").replace("[geval]", "").strip().title() if middle else stat.replace("_", " ")
    return group, name, stat


def _sheet_runs(wb, runs):
    st = _style()
    ws = wb.create_sheet("Runs")
    headers = ["Run ID", "Metric id", "Value", "Logged (UTC)", "Source"]
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill, c.alignment = st["head_font"], st["head_fill"], Alignment(vertical="center")
    for r, ((rid, mid), (val, at, src)) in enumerate(sorted(runs.items(), key=lambda kv: (kv[0][0], _metric_sort_key(kv[0][1]))), start=2):
        ws.append([rid, mid, val, at, src])
        ws.cell(r, 3).number_format = _number_format(mid)
        for c in ws[r]:
            c.font = st["body"]
    for col, w in zip("ABCDE", [24, 46, 14, 17, 14]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:E{max(len(runs) + 1, 2)}"


def _sheet_history(wb, history):
    """Append-only log of every value ever logged, with its exact timestamp -- nothing here is overwritten."""
    st = _style()
    ws = wb.create_sheet("History")
    ws.append(["Logged (UTC)", "Run ID", "Source", "Metric id", "Value"])
    for c in ws[1]:
        c.font, c.fill = st["head_font"], st["head_fill"]
    for at, rid, src, mid, val in history:
        ws.append([at, rid, src, mid, val])
        r = ws.max_row
        ws.cell(r, 5).number_format = _number_format(mid)
        for c in ws[r]:
            c.font = st["body"]
    for col, w in zip("ABCDE", [20, 24, 14, 46, 14]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:E{max(len(history) + 1, 2)}"


def _sheet_runlog(wb, run_log, order, n_run_rows):
    st = _style()
    ws = wb.create_sheet("RunLog")
    ws.append(["Run ID", "Label", "Last updated (UTC)", "Evals reported", "# metrics", "Notes (yours to edit)"])
    for c in ws[1]:
        c.font, c.fill = st["head_font"], st["head_fill"]
    for r, rid in enumerate(order, start=2):
        log = run_log[rid]
        ws.append([rid, log["label"], log["at"], ", ".join(log["sources"]),
                   f"=COUNTIF(Runs!$A$2:$A${n_run_rows + 1 + ROW_MARGIN},A{r})", log["notes"]])
        for c in ws[r]:
            c.font = st["body"]
            c.alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(r, 6).fill = st["input_fill"]
    for col, w in zip("ABCDEF", [24, 44, 18, 40, 10, 70]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"


def _sheet_trend(wb, runs, order):
    st = _style()
    ws = wb.create_sheet("Trend", 0)
    n_run_rows = len(runs)
    last_runs_row = n_run_rows + 1 + ROW_MARGIN
    metric_ids = sorted({mid for _, mid in runs}, key=_metric_sort_key)
    n_runs = len(order)
    last_run_col = FIRST_RUN_COL + max(n_runs, 1) - 1

    ws["A1"] = "Eval tracker -- one row per metric, one column per run"
    ws["A1"].font = Font(name=FONT, size=14, bold=True)
    ws["A2"] = ("Baseline = first run that has the metric, Latest = most recent. Status compares Latest with Baseline using the direction and "
                "tolerance rules in evals/metric_registry.py (gate/guardrail metrics only; 'info' rows are shown but not judged).")
    ws["A2"].font = st["muted"]

    for i, h in enumerate(STATIC_HEADERS, start=1):
        c = ws.cell(HEADER_ROW, i, h)
        c.font, c.fill, c.alignment = st["head_font"], st["head_fill"], Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.cell(LABEL_ROW, FIRST_RUN_COL - 1, "Run label").font = st["muted"]
    ws.cell(LABEL_ROW, FIRST_RUN_COL - 1).alignment = Alignment(horizontal="right")
    for j, rid in enumerate(order):
        col = FIRST_RUN_COL + j
        c = ws.cell(HEADER_ROW, col, rid)
        c.font, c.fill, c.alignment = st["head_font"], st["head_fill"], Alignment(horizontal="center", vertical="center", wrap_text=True)
        # &"" turns a blank label into empty text (INDEX of an empty cell would otherwise show 0)
        lab = ws.cell(LABEL_ROW, col, f'=IFERROR(INDEX(RunLog!$B$2:$B${n_runs + 1 + 50},MATCH({get_column_letter(col)}${HEADER_ROW},RunLog!$A$2:$A${n_runs + 1 + 50},0))&"","")')
        lab.font, lab.alignment = st["muted"], Alignment(horizontal="center", wrap_text=True, vertical="top")

    for i, mid in enumerate(metric_ids):
        r = FIRST_DATA_ROW + i
        group, name, stat = _nice_name(mid)
        rule = rule_for(mid)
        static = [mid, group, name, stat, rule["direction"], rule["kind"], rule["tol"], rule["rel_tol"]]
        for c, v in enumerate(static, start=1):
            cell = ws.cell(r, c, v)
            cell.font = st["body"]
        ws.cell(r, 1).font = Font(name=FONT, size=9, color="595959")
        ws.cell(r, 7).number_format = ws.cell(r, 8).number_format = "0.00"

        have = [j for j, rid in enumerate(order) if (rid, mid) in runs]
        fmt = _number_format(mid)
        for j, rid in enumerate(order):
            col = FIRST_RUN_COL + j
            run_hdr = f"{get_column_letter(col)}${HEADER_ROW}"
            crit = f"Runs!$B$2:$B${last_runs_row},$A{r},Runs!$A$2:$A${last_runs_row},{run_hdr}"     # metric id + run id
            cell = ws.cell(r, col, f'=IF(COUNTIFS({crit})=0,"",SUMIFS(Runs!$C$2:$C${last_runs_row},{crit}))')
            cell.number_format, cell.font = fmt, st["body"]

        first_ref = f"{get_column_letter(FIRST_RUN_COL + have[0])}{r}" if have else None
        last_ref = f"{get_column_letter(FIRST_RUN_COL + have[-1])}{r}" if have else None
        ws.cell(r, 9, f"={first_ref}" if first_ref else "")
        ws.cell(r, 10, f"={last_ref}" if last_ref else "")
        ws.cell(r, 11, f'=IF(OR(I{r}="",J{r}=""),"",J{r}-I{r})')
        ws.cell(r, 12, f'=IF(OR(K{r}="",F{r}="info"),"",IF(ABS(K{r})<=MAX(G{r},H{r}*ABS(I{r})),"flat",IF((K{r}>0)=(E{r}="higher"),"better","worse")))')
        for c in (9, 10, 11):
            ws.cell(r, c).number_format, ws.cell(r, c).font = fmt, st["bold"] if c == 10 else st["body"]
        ws.cell(r, 12).font, ws.cell(r, 12).alignment = st["bold"], Alignment(horizontal="center")
        if i % 2 == 1:
            for c in range(1, last_run_col + 1):
                ws.cell(r, c).fill = st["band"]

    last_row = FIRST_DATA_ROW + max(len(metric_ids), 1) - 1
    status_rng = f"L{FIRST_DATA_ROW}:L{last_row}"
    ws.conditional_formatting.add(status_rng, CellIsRule(operator="equal", formula=['"worse"'], fill=PatternFill("solid", bgColor="F8CBAD"), font=Font(name=FONT, bold=True, color="9C0006")))
    ws.conditional_formatting.add(status_rng, CellIsRule(operator="equal", formula=['"better"'], fill=PatternFill("solid", bgColor="C6E0B4"), font=Font(name=FONT, bold=True, color="375623")))

    widths = {"A": 40, "B": 11, "C": 26, "D": 12, "E": 8, "F": 10, "G": 6, "H": 7, "I": 11, "J": 11, "K": 10, "L": 9}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    for j in range(max(n_runs, 1)):
        ws.column_dimensions[get_column_letter(FIRST_RUN_COL + j)].width = 17
    ws.row_dimensions[LABEL_ROW].height = 42
    ws.row_dimensions[HEADER_ROW].height = 30
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, 4)
    ws.auto_filter.ref = f"A{HEADER_ROW}:{get_column_letter(last_run_col)}{last_row}"


def _sheet_readme(wb):
    st = _style()
    ws = wb.create_sheet("README", 0)
    lines = [
        ("Eval tracker", "title"),
        ("", None),
        ("What this is", "h"),
        ("Every eval in this project appends its numbers here when it finishes, so runs can be compared over time. The workbook is rebuilt on each "
         "update from the Runs sheet; nothing needs to be edited by hand except the Notes column on the RunLog sheet.", None),
        ("", None),
        ("Sheets", "h"),
        ("Trend   - one row per metric, one column per run. Baseline / Latest / Change / Status are formulas.", None),
        ("Runs    - the raw data, one row per (run, metric). This is what Trend reads.", None),
        ("RunLog  - one row per run: label, time, which evals reported, and a Notes column (yellow = yours to edit).", None),
        ("History - every value ever logged, with its exact time. Never overwritten.", None),
        ("", None),
        ("Reading Status", "h"),
        ("worse / better / flat compare the Latest value with the Baseline (the first run that has that metric). 'flat' means the move is inside the "
         "tolerance from evals/metric_registry.py (Tol = absolute, Rel tol = fraction of the baseline). Rows with Kind = info are shown but not judged. "
         "'Better' is the direction that counts as an improvement for that metric (higher for scores, lower for latency, cost and toxicity).", None),
        ("Judge scores wobble run to run (a few hundredths), so treat small moves inside the tolerance as noise.", None),
        ("", None),
        ("What a 'run' is", "h"),
        ("All numbers logged with the same run id share one column. The run id is the EVAL_RUN environment variable if set, otherwise today's date. "
         "So evals run on the same day land together, and re-running an eval in the same run replaces its earlier numbers in Trend and Runs "
         "(the earlier numbers stay on the History sheet).", None),
        ('To keep a rerun or experiment separate (PowerShell):   $env:EVAL_RUN = "k3-experiment"   and optionally   $env:EVAL_LABEL = "top_k=3"', None),
        ("", None),
        ("If the file is open in Excel", "h"),
        ("Windows locks an open workbook, so the numbers are saved to reports/pending.jsonl and merged in automatically the next time something is "
         "logged with the file closed. The eval itself never fails because of it.", None),
        ("", None),
        ("Nothing is lost", "h"),
        ("Every update also saves a full copy of this workbook in reports/history/, named eval_tracker_<local date>_<time>.xlsx (file names use "
         "your local time; timestamps inside the workbook are UTC). Old copies can be deleted freely.", None),
        ("", None),
        ("Provenance", "h"),
        ("Rows with source 'manual seed' were copied by hand from console summaries (rounded to 2 decimals); everything else is written by the evals.", None),
    ]
    for r, (text, kind) in enumerate(lines, start=1):
        c = ws.cell(r, 1, text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = {"title": Font(name=FONT, size=16, bold=True), "h": Font(name=FONT, size=11, bold=True, color="1F3864")}.get(kind, st["body"])
    ws.column_dimensions["A"].width = 130
