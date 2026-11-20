"""REPORT: the Excel evidence of a test run, written for the person who has to read it.

  report/Evidence_<testcase file>_<session>.xlsx   ONE workbook for the whole run: a "Tổng hợp" sheet (every case with its result), then one sheet per case
  report/SUMMARY.md                                the result in a few lines

One file, rebuilt in place each time: no pile of per-case workbooks.

A case sheet says: what the testcase asked (from the workbook), how the assistant understood and planned it, what happened, and every step with the picture
of the screen, red-framed, taken for it. Built from view.case_records, the same record the dashboard shows."""
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import view
from .state import Session

LABELS = {
    "vi": {
        "title": "Báo cáo evidence kiểm thử", "summary": "Tổng hợp", "file": "File testcase", "system": "Hệ thống", "session": "Phiên test", "started": "Bắt đầu", "finished": "Kết thúc",
        "totals": "Kết quả chung", "no": "No.", "category": "Phân loại", "name": "Test Case Name", "pre": "Điều kiện tiên quyết", "src_steps": "Các bước (file gốc)",
        "src_exp": "Kết quả mong đợi (file gốc)", "profile": "Profile", "result": "Kết quả", "runs": "Số lần chạy", "checks": "Số kiểm tra", "remark": "Nhận xét",
        "s1": "1. Testcase yêu cầu gì (theo file gốc)", "s2": "2. AI hiểu và lên kế hoạch thế nào", "s3": "3. Kết quả", "s4": "4. Từng bước và ảnh evidence (lần chạy cuối)", "s5": "5. Các lần chạy",
        "note": "Ghi chú trong file", "row": "Dòng trong file", "deps": "Tiếp nối từ", "rating": "Đánh giá đề bài", "clarity": "Rõ ràng", "suff": "Đủ dữ kiện", "edge": "Tình huống biên đã tính",
        "data": "Dữ liệu có thể thiếu → cách bù", "asked": "Câu hỏi AI cần hỏi bạn", "hints": "Gợi ý của bạn", "repairs": "AI phân tích lại và sửa",
        "r_after": "Sau lần {n}", "r_cause": "nguyên nhân", "r_evidence": "căn cứ", "r_change": "đã sửa", "r_reread": "hiểu lại case", "verdict": "Kết luận", "step": "Bước", "action": "Thao tác",
        "expected": "Kết quả mong đợi", "actual": "Thực tế", "nchecks": "Kiểm tra", "evidence": "Ảnh evidence", "attempt": "Lần", "failed": "Bước lỗi", "reason": "Lý do",
        "notrun": "Chưa chạy", "none": "(không có)", "case_sheet": "Mở sheet",
        "ok": "Đạt: {n} bước đều đã chạy, có đối chiếu với kết quả mong đợi và có ảnh evidence.", "fail": "Không đạt{at}: {why}", "pending": "Chờ bạn quyết định: {why}",
        "blocked": "Không thực hiện được: {why}", "notrun_text": "Chưa chạy trong lần test này{why}", "retry": " Đã phải chạy {n} lần (các lần trước lỗi, xem mục 5).",
    },
    "en": {
        "title": "Test evidence report", "summary": "Summary", "file": "Testcase file", "system": "System", "session": "Test session", "started": "Started", "finished": "Finished",
        "totals": "Overall", "no": "No.", "category": "Category", "name": "Test Case Name", "pre": "Precondition", "src_steps": "Steps (source file)",
        "src_exp": "Expected result (source file)", "profile": "Profile", "result": "Result", "runs": "Runs", "checks": "Checks", "remark": "Remark",
        "s1": "1. What the testcase asks (from the source file)", "s2": "2. How the assistant understood and planned it", "s3": "3. Result", "s4": "4. Each step and its evidence picture (last run)", "s5": "5. Runs",
        "note": "Note in the file", "row": "Row in the file", "deps": "Continues from", "rating": "Rating of the case", "clarity": "Clarity", "suff": "Sufficiency", "edge": "Edge cases considered",
        "data": "Data that may be missing → fallback", "asked": "Questions for you", "hints": "Your hints", "repairs": "How the assistant re-analysed and repaired",
        "r_after": "After run {n}", "r_cause": "cause", "r_evidence": "evidence", "r_change": "changed", "r_reread": "now reads the case as", "verdict": "Conclusion", "step": "Step", "action": "Action",
        "expected": "Expected result", "actual": "Actual", "nchecks": "Checks", "evidence": "Evidence picture", "attempt": "Run", "failed": "Failed step", "reason": "Reason",
        "notrun": "Not run", "none": "(none)", "case_sheet": "Open sheet",
        "ok": "Passed: all {n} steps ran, each was checked against its expected result and has an evidence picture.", "fail": "Failed{at}: {why}", "pending": "Waiting for your decision: {why}",
        "blocked": "Could not be done: {why}", "notrun_text": "Not run in this test{why}", "retry": " It took {n} runs (the earlier ones failed, see section 5).",
    },
}
COLORS = {"PASS": "C6EFCE", "FAIL": "FFC7CE", "PENDING": "FFEB9C", "BLOCKED": "D9D9D9", "NOT EXECUTED": "D9D9D9"}
NOT_RUN_COLOR = "EDEDED"
IMAGE_WIDTH = 440
WIDTHS = {"A": 22, "B": 40, "C": 40, "D": 40, "E": 10, "F": 11, "G": 64}
TEXT_CHARS = 200            # roughly how many characters fit on one line of a merged B:G cell


def shown(status, t):
    return status if status in COLORS else t["notrun"]


def paint(cell, label):
    from openpyxl.styles import PatternFill
    cell.fill = PatternFill("solid", fgColor=COLORS.get(label, NOT_RUN_COLOR))


def height_for(text, chars):
    return 15 * sum(max(1, math.ceil(len(part) / chars)) for part in str(text or "").split("\n")) + 4


def conclusion(record, last, t):
    status = record["status"]
    why = record["reason"] or (last["reason"] if last else "")
    if status == "PASS":
        return t["ok"].format(n=len(last["steps"]) if last else 0) + (t["retry"].format(n=len(record["attempts"])) if len(record["attempts"]) > 1 else "")
    if status == "FAIL":
        at = f" ({t['step'].lower()} {last['failed_step']})" if last and last.get("failed_step") else ""
        return t["fail"].format(at=at, why=why or "-")
    if status == "PENDING":
        return t["pending"].format(why=why or "-")
    if status == "BLOCKED":
        return t["blocked"].format(why=why or "-")
    return t["notrun_text"].format(why=f": {why}" if why else ".")


def safe_sheet_name(name, used):
    base = re.sub(r"[\[\]:*?/\\]", "_", name)[:28] or "case"
    candidate, n = base, 2
    while candidate.lower() in used:
        candidate, n = f"{base[:25]}_{n}", n + 1
    used.add(candidate.lower())
    return candidate


def write_case(sheet, session: Session, record, t):
    """One case, top to bottom, on `sheet`."""
    from openpyxl.drawing.image import Image
    from openpyxl.styles import Alignment, Font, PatternFill
    for column, width in WIDTHS.items():
        sheet.column_dimensions[column].width = width
    wrap = Alignment(wrap_text=True, vertical="top")
    state = session.state()
    source, label = record["source"], shown(record["status"], t)
    last = record["attempts"][-1] if record["attempts"] else None
    row = 1

    def line(key, value, color=None):
        nonlocal row
        sheet.cell(row, 1, t[key]).font = Font(bold=True)
        sheet.cell(row, 1).alignment = wrap
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
        cell = sheet.cell(row, 2, value if value not in (None, "") else t["none"])
        cell.alignment = wrap
        if color:
            paint(cell, color)
        sheet.row_dimensions[row].height = height_for(value, TEXT_CHARS)
        row += 1

    def section(key):
        nonlocal row
        row += 1
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
        cell = sheet.cell(row, 1, t[key])
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F3A4F")
        sheet.row_dimensions[row].height = 20
        row += 1

    sheet.merge_cells("A1:G1")
    head = sheet.cell(1, 1, f"{record['id']}  ·  {record['title']}  ·  {label}")
    head.font = Font(bold=True, size=14)
    paint(head, label)
    sheet.row_dimensions[1].height = 26
    row = 2
    section("s1")
    line("no", record["id"])
    line("name", record["title"])
    line("category", " / ".join(x for x in (source["category"], source["type"]) if x))
    line("pre", source["pre"])
    line("src_steps", "\n".join(f"{i}. {s}" for i, s in enumerate(source["steps"], 1)))
    line("src_exp", source["expected"])
    line("note", source["note"])
    line("row", record["row"])
    section("s2")
    line("profile", record["profile"])
    line("deps", ", ".join(record["depends_on"]) + (f" ({record['reuse']})" if record["reuse"] else "") if record["depends_on"] else "")
    line("rating", f"{t['clarity']} {record['clarity']}/5 · {t['suff']} {record['sufficiency']}/5" if record["clarity"] else "")
    line("edge", "\n".join(f"• {e}" for e in record["edge_cases"]) or record["edge_none"])
    line("data", "\n".join(f"• {d['what']} → {d['fallback']}" for d in record["data_needs"]))
    if record["questions"]:
        line("asked", "\n".join(f"• {q}" for q in record["questions"]))
    section("s3")
    line("result", label, color=label)
    line("runs", len(record["attempts"]))
    if record["hints"]:
        line("hints", "\n".join(f"• {h['text']}" for h in record["hints"]))
    if record["repairs"]:
        line("repairs", "\n".join(f"• {t['r_after'].format(n=r['attempt'])}: {t['r_cause']}: {r['cause']}; {t['r_evidence']}: {r['evidence']}; {t['r_change']}: {r['change']}"
                                  + (f"; {t['r_reread']}: {r['reanalysis']}" if r.get("reanalysis") else "") for r in record["repairs"]))
    line("verdict", conclusion(record, last, t))
    section("s4")
    for column, key in enumerate(("step", "action", "expected", "actual", "nchecks", "result", "evidence"), 1):
        cell = sheet.cell(row, column, t[key])
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
    row += 1
    steps = last["steps"] if last else [{"n": p["n"], "caption": p["caption"], "expected": p["expected"], "status": "PENDING", "actual": "", "images": [], "checks": None} for p in record["planned"]]
    for step in steps:
        step_label = shown(step["status"], t)
        for column, value in enumerate((step["n"], step["caption"], step["expected"], step["actual"], step.get("checks") if step.get("checks") is not None else "", step_label), 1):
            sheet.cell(row, column, value).alignment = wrap
        paint(sheet.cell(row, 6), step_label)
        height = max(height_for(step["caption"], 42), height_for(step["expected"], 42), height_for(step["actual"], 42))
        for index, captured in enumerate(step["images"] or [{}]):
            if index:
                sheet.cell(row, 1, step["n"]).alignment = wrap
                sheet.cell(row, 2, captured.get("caption") or step["caption"]).alignment = wrap
            picture = session.dir / captured["annotated"] if captured.get("annotated") else None
            image_height = 0
            if picture and picture.exists():
                image = Image(str(picture))
                image.height, image.width = round(image.height * IMAGE_WIDTH / image.width), IMAGE_WIDTH
                sheet.add_image(image, f"G{row}")
                image_height = image.height * 0.75 + 6
            sheet.row_dimensions[row].height = max(height if not index else 30, image_height)
            row += 1
    section("s5")
    for column, key in enumerate(("attempt", "result", "failed", "reason"), 1):
        cell = sheet.cell(row, column, t[key])
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
    row += 1
    for attempt in record["attempts"]:
        status = shown(attempt["status"], t) if attempt["status"] in COLORS else attempt["status"]
        for column, value in enumerate((attempt["n"], status, attempt["failed_step"] or "", attempt["reason"]), 1):
            sheet.cell(row, column, value).alignment = wrap
        paint(sheet.cell(row, 2), status)
        sheet.row_dimensions[row].height = height_for(attempt["reason"], 42)
        row += 1
    sheet.freeze_panes = "A2"
    sheet.sheet_view.zoomScale = 90


def write_summary(sheet, session: Session, ws, records, t, sheet_names):
    from openpyxl.styles import Alignment, Font, PatternFill
    state = session.state()
    log = view.events.read(session.dir)
    counts = {}
    for record in records:
        label = shown(record["status"], t)
        counts[label] = counts.get(label, 0) + 1
    widths = {"A": 10, "B": 14, "C": 34, "D": 32, "E": 44, "F": 44, "G": 12, "H": 12, "I": 8, "J": 9, "K": 54}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.merge_cells("A1:K1")
    sheet["A1"] = f"{t['title']}: {Path(state['file']).name}"
    sheet["A1"].font = Font(bold=True, size=15)
    version = (state.get("connect") or {}).get("version")
    facts = [("file", state["file"]), ("system", f"{ws['url']}" + (f" · db {ws['db']}" if ws.get("db") else "") + (f" · Odoo {version}" if version else "")),
             ("session", f"{ws['name']} / {session.id}"), ("started", state["created"]), ("finished", log[-1]["ts"] if log else ""),
             ("totals", " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"  ({len(records)})")]
    for offset, (key, value) in enumerate(facts, 2):
        sheet.cell(offset, 1, t[key]).font = Font(bold=True)
        sheet.merge_cells(start_row=offset, start_column=3, end_row=offset, end_column=11)
        sheet.cell(offset, 3, value)
    top = len(facts) + 3
    headers = ("no", "category", "name", "pre", "src_steps", "src_exp", "profile", "result", "runs", "checks", "remark")
    for column, key in enumerate(headers, 1):
        cell = sheet.cell(top, column, t[key])
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F3A4F")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for offset, record in enumerate(records, 1):
        row = top + offset
        last = record["attempts"][-1] if record["attempts"] else None
        label = shown(record["status"], t)
        total_checks = sum(s.get("checks") or 0 for s in last["steps"]) if last else 0
        values = (record["id"], " / ".join(x for x in (record["source"]["category"], record["source"]["type"]) if x), record["title"], record["source"]["pre"],
                  "\n".join(f"{i}. {s}" for i, s in enumerate(record["source"]["steps"], 1)), record["source"]["expected"], record["profile"], label,
                  len(record["attempts"]) or "", total_checks or "", conclusion(record, last, t))
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value).alignment = wrap
        paint(sheet.cell(row, 8), label)
        link = sheet.cell(row, 1)
        link.hyperlink = f"#'{sheet_names[record['id']]}'!A1"
        link.font = Font(color="0563C1", underline="single")
        sheet.row_dimensions[row].height = max(height_for(values[4], 46), height_for(values[10], 56), height_for(values[5], 46))
    sheet.freeze_panes = sheet.cell(top + 1, 4)
    sheet.sheet_view.zoomScale = 90


def build(session: Session, ws):
    """Write the evidence workbook and SUMMARY.md. Returns {folder, main, files, counts}."""
    from openpyxl import Workbook
    t = LABELS.get(ws.get("language"), LABELS["en"])
    records = sorted(view.case_records(session), key=lambda r: r["row"])
    folder = session.dir / "report"
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "_", Path(session.state()["file"]).stem)
    main = folder / f"Evidence_{stem}_{session.id}.xlsx"
    evidence = main
    for old in folder.glob("*.xlsx"):
        if old != main and not old.name.startswith(("~$", "original_")):
            try:
                old.unlink(missing_ok=True)                         # one current workbook per session; also clears the per-case files older versions wrote
            except OSError:
                pass                                                # open in Excel right now: it goes at the next rebuild
    shutil.rmtree(folder / "cases", ignore_errors=True)
    book = Workbook()
    summary = book.active
    summary.title = t["summary"]
    used = {t["summary"].lower()}
    names = {r["id"]: safe_sheet_name(r["id"], used) for r in records}
    for record in records:
        write_case(book.create_sheet(names[record["id"]]), session, record, t)
    write_summary(summary, session, ws, records, t, names)
    try:
        book.save(main)
    except PermissionError:                                         # the person has the workbook open in Excel: write beside it instead of failing
        main = main.with_name(main.stem + time.strftime("_%H%M%S") + ".xlsx")
        evidence = main
        book.save(main)
    totals = view.counts(records)
    back = None
    try:
        back = write_back(session, ws, records, t)
        main = Path(back["path"])
        for old in folder.glob("Result_*"):                          # the dashboard serves what is in this folder: a copy of the testcase file as written
            old.unlink(missing_ok=True)
        shutil.copy2(main, folder / ("Result_" + main.name))
    except Exception as error:                                       # the evidence workbook above is still there
        back = {"path": "", "in_place": False, "backup": "", "note": f"could not write into the testcase file: {error}"}
    lines = [f"# {Path(session.state()['file']).name}", "", ", ".join(f"{k} {v}" for k, v in sorted(totals.items())), ""]
    lines += [f"- {r['id']} [{r['status']}] {r['title']}" + (f": {r['reason']}" if r["reason"] else "") for r in records]
    (folder / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"folder": str(folder), "main": str(main), "evidence_only": str(evidence), "download": ("Result_" + Path(back["path"]).name) if back.get("path") else Path(evidence).name, "testcase_file": back, "files": [str(main), str(evidence), str(folder / "SUMMARY.md")], "counts": totals}


HEADS = {"en": {"res": "Result (oqa)", "date": "Tested on (oqa)", "evid": "Evidence (oqa)", "why": "Remark (oqa)"},
         "vi": {"res": "Kết quả (oqa)", "date": "Ngày test (oqa)", "evid": "Evidence (oqa)", "why": "Ghi chú (oqa)"}}
LOSES_ON_SAVE = (b"<xdr:sp", b"graphicFrame", b"<xdr:cxnSp")        # shapes, charts and diagrams: openpyxl does not keep them when it saves a workbook it opened


def _would_lose_content(path):
    """True when saving the workbook through openpyxl would drop something from it (a chart, a shape, a diagram)."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.startswith("xl/charts/") or name.startswith("xl/diagrams/"):
                    return True
                if name.startswith("xl/drawings/") and name.endswith(".xml") and any(m in z.read(name) for m in LOSES_ON_SAVE):
                    return True
    except (OSError, zipfile.BadZipFile):
        return True
    return False


def write_back(session: Session, ws, records, t):
    """The testcase workbook the user gave is the output: each case's result goes into its own row (the Result column when the file has one, else new columns after the
    table), and the evidence goes into new sheets of the same file. The original is copied to the session first. Returns {path, in_place, backup, note}."""
    from copy import copy
    from openpyxl import load_workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    state = session.state()
    cases_doc = session.cases()
    source = Path(cases_doc["source"]["file"])
    if not source.exists():
        source = Path(state["file"])
    sheet_name, header = cases_doc["source"]["sheet"], cases_doc["source"]["header_row"]
    cols = dict(cases_doc["source"].get("columns") or {})
    heads = HEADS.get(ws.get("language"), HEADS["en"])
    folder = session.dir / "report"
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / ("original_" + source.name)
    if not backup.exists():                                           # the first time only: it is what the user gave, not what we wrote
        shutil.copy2(source, backup)
    book = load_workbook(backup, keep_vba=source.suffix.lower() == ".xlsm")
    result_fields = ("res", "date", "evid", "why")
    layouts = {}
    tables = cases_doc["source"].get("tables") or {sheet_name: {"header_row": header, "columns": cols}}
    for table_name, layout in tables.items():
        table, header, cols = book[table_name], layout["header_row"], dict(layout["columns"])
        last = max(table.max_column, max(cols.values(), default=0))
        new_columns = []
        for key in result_fields:
            if key not in cols:
                last += 1
                cols[key] = last
                new_columns.append(last)
                cell = table.cell(header, last, heads[key])
                donor = table.cell(header, max(1, last - 1))
                cell.font, cell.fill, cell.border, cell.alignment = copy(donor.font), copy(donor.fill), copy(donor.border), copy(donor.alignment)
                table.column_dimensions[get_column_letter(last)].width = 18 if key != "why" else 40
        layouts[table_name] = (table, header, cols, new_columns)
    used = {s.title.lower() for s in book.worksheets}
    names = {r["id"]: safe_sheet_name(r["id"], used) for r in records}
    for record in records:
        table, header, cols, new_columns = layouts[record.get("source", {}).get("sheet", sheet_name)]
        write_case(book.create_sheet(names[record["id"]]), session, record, t)
        row = record["row"]
        if record["status"] in COLORS:
            label = record["status"]
            cell = table.cell(row, cols["res"], label)
            paint(cell, label)
            table.cell(row, cols["date"], time.strftime("%Y-%m-%d"))
            link = table.cell(row, cols["evid"], t["case_sheet"] + " " + names[record["id"]])
            link.hyperlink = f"#'{names[record['id']]}'!A1"
            link.font = Font(color="0563C1", underline="single")
            table.cell(row, cols["why"], record["reason"] or "")
    summary = book.create_sheet(safe_sheet_name(t["summary"], used))
    write_summary(summary, session, ws, records, t, names)
    note = ""
    target = source
    if state.get("channel") == "path":
        target = source.with_name(f"{source.stem}.result-{session.id}{source.suffix}")
    elif state.get("channel") == "addin":
        if len(layouts) > 1:
            raise ValueError("Multi-sheet selections currently require channel path")
        from . import excel_writeback

        stage = session.dir / "logs"
        stage.mkdir(exist_ok=True)
        target = stage / f"excel-result{source.suffix}"
        book.save(target)
        applied, error = excel_writeback.apply(source, target, {
            "session": session.id, "sheet": sheet_name, "id_column": cols["no"],
            "header": header, "new_columns": new_columns,
            "rows": [{"row": r["row"], "id": r["id"]} for r in records],
            "cells": [{"row": header, "col": col} for col in new_columns] + [
                {"row": r["row"], "col": cols[key]}
                for r in records if r["status"] in COLORS for key in result_fields
            ],
            "evidence": [*names.values(), summary.title],
        }, stage)
        return {
            "path": str(source if applied else target), "in_place": applied, "backup": str(backup),
            "note": "" if applied else f"Could not update the open workbook: {error}",
        }
    elif _would_lose_content(source):
        target = source.with_name(source.stem + ".result" + source.suffix)
        note = f"{source.name} holds charts or shapes that would be lost when saved: the result is a copy beside it, the original is untouched."
    try:
        book.save(target)
    except PermissionError:                                            # open in Excel right now: write beside it instead of failing
        target = target.with_name(target.stem + time.strftime("_%H%M%S") + target.suffix)
        book.save(target)
        note = (note + " " if note else "") + f"{source.name} is open in another program: the result is {target.name}."
    return {"path": str(target), "in_place": target == source, "backup": str(backup), "note": note}


def finish(session: Session, ws):
    """The report of a finished session: build it, then close the session."""
    session.require("REPORT")
    result = build(session, ws)
    session.done(result["counts"])
    return result


def open_file(path):
    """Show the workbook to the person: their spreadsheet program opens it. Skipped when OQA_NO_OPEN is set (tests, servers)."""
    if os.environ.get("OQA_NO_OPEN"):
        return False
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
