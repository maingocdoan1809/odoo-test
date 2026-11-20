"""INTAKE: read a testcase workbook into cases.json, and do the part of the analysis a script can do without judgement: split steps, find the files
a case points at (and whether they exist), and find the other cases it mentions. The assistant judges the rest in ANALYZE.

Header names are matched through synonyms (en / ja / vi). The sheet and the header row are detected: the first sheet whose header row (within the
first 15 rows) has an ID column and an expected-result or action column."""
import hashlib
import re
from pathlib import Path

from . import SCHEMA_VERSION, refs
from .store import SchemaError

SYN = {
    "no": ["No.", "No", "ID", "TC ID", "TC No", "Test Case ID", "Case ID", "テストケースID", "ケースID", "テストID", "番号", "ケース番号", "Mã TC", "Mã testcase"],
    "cls": ["分類", "カテゴリ", "Category", "Module", "Feature", "Chức năng", "Phân loại"],
    "typ": ["タイプ", "種別", "Type", "Test Type", "Loại"],
    "view": ["テスト観点", "テスト項目", "テスト名", "試験項目", "Test Case Name", "Test Case", "Title", "Summary", "Objective", "目的",
             "Góc độ kiểm thử", "Quan điểm kiểm thử", "Tên testcase", "Mục đích"],
    "role": ["対象ロール/環境", "ロール", "対象ロール", "実施ロール", "Role", "Roles", "Environment", "Role / Environment", "Role / Môi trường áp dụng", "Vai trò", "Môi trường"],
    "pre": ["事前準備", "前提条件", "事前条件", "Precondition", "Preconditions", "Pre-condition", "Chuẩn bị trước", "Điều kiện tiên quyết", "Tiền điều kiện"],
    "act": ["アクション", "手順", "操作", "操作手順", "Steps", "Action", "Actions", "Test Steps", "Procedure", "Hành động", "Thao tác", "Các bước"],
    "exp": ["期待結果", "期待値", "確認内容", "Expected Result", "Expected Results", "Expected", "Kết quả mong đợi"],
    "note": ["備考", "Notes", "Note", "Remark", "Remarks", "Ghi chú"],
    "res": ["結果", "判定", "テスト結果", "試験結果", "実施結果", "Result", "Test Result", "Status", "Test Status", "Pass/Fail", "OK/NG", "Kết quả", "Kết quả test", "Trạng thái"],
    "date": ["実施日", "テスト実施日", "試験日", "Test Date", "Executed on", "Date", "Ngày test", "Ngày thực hiện"],
    "evid": ["エビデンス", "証跡", "Evidence", "Bằng chứng", "Minh chứng"],
}
RESULT_KEYS = ("res", "date", "evid")                   # where a result goes back into the workbook; the other keys describe the case
FILE_EXT = r"(?:xlsx|xlsm|xls|csv|pdf|docx?|pptx?|png|jpe?g|gif|txt|zip|json|xml|html?)"
QUOTED_FILE = re.compile(r"[\"'`“‘「『]([^\"'`”’」』\n]+?\." + FILE_EXT + r")[\"'`”’」』]", re.I)
BARE_FILE = re.compile(r"(?<![\w/\\.:-])((?:[A-Za-z]:)?[\w\-.()/\\]+\." + FILE_EXT + r")\b", re.I)
NUMBERING = re.compile(r"^\s*(?:\d+[.)\-:：]|[-*・•])\s*")


REFERENCE_PHRASES = [re.compile(p, re.I) for p in (
    r"\b(?:as|per|according to|in line with|consistent with|matches?|matching|follows?|following|conforms? to)\s+(?:the\s+|its\s+)?(?:(?:screen|basic|detailed|ui|ux|visual)\s+)?(?:design|specs?|specification|mock-?ups?|wireframes?|BD|requirements?|layout)s?\b",
    r"\bas designed\b",
    r"(?:đúng|theo|như|khớp|giống|phù hợp|chuẩn)\s+(?:với\s+)?(?:file\s+|tài liệu\s+|bản\s+)?(?:design|thiết kế|spec|đặc tả|mock-?up|wireframe|BD|basic design|layout|yêu cầu)",
    r"(?:設計書|仕様書|デザイン|基本設計|画面設計|BD|モックアップ|ワイヤー(?:フレーム)?|画面定義|要件)書?\s*(?:通り|どおり|に従|に沿|と一致|に基づ|と同じ)",
    r"仕様通り|仕様どおり")]
UNMEASURABLE_PHRASES = [re.compile(p, re.I) for p in (
    r"\b(?:correctly|properly|as expected|as intended|appropriately|normally|works?\s+(?:fine|well)|(?:no|without)\s+(?:issues?|problems?))\b",
    r"(?:hiển thị|hoạt động|xử lý|chạy|tính toán|lưu|hoàn thành|thao tác)\s+(?:đúng|bình thường|chính xác|ổn|tốt)(?!\s*\d)",
    r"đúng như (?:mong đợi|yêu cầu|kỳ vọng|dự kiến)",
    r"(?:không|chẳng)\s+(?:có\s+)?(?:vấn đề|sự cố)",
    r"正常に|正しく|問題(?:なく|ない)|期待通り|期待どおり|適切に")]
ATTACHMENT_WORD = re.compile(r"\b(?:attach(?:ed|ment)s?)\b|đính kèm|添付", re.I)


def find_vague(sections):
    """Phrases a person would ask about before running the case: a result judged against a document nobody named ("as designed", "đúng design"), or one that cannot be
    measured ("works correctly", "hiển thị bình thường"). `sections` = {where: text}. -> [{phrase, kind: reference|unmeasurable, where}]. A script only SEES the phrase; what to do
    about it (ask, read the file the user gave, write a concrete check) is the analysis, and `analysis.validate` will not let a case be ready until each one is resolved."""
    out, seen = [], set()
    for where, text in sections.items():
        text = str(text or "")
        spans = []
        for pattern in REFERENCE_PHRASES:
            for match in pattern.finditer(text):
                spans.append(match.span())
                if (match.group(0).lower(), "reference") not in seen:
                    seen.add((match.group(0).lower(), "reference"))
                    out.append({"phrase": match.group(0), "kind": "reference", "where": where})
        for pattern in UNMEASURABLE_PHRASES:
            for match in pattern.finditer(text):
                if any(match.start() < end and start < match.end() for start, end in spans) or (match.group(0).lower(), "unmeasurable") in seen:
                    continue
                seen.add((match.group(0).lower(), "unmeasurable"))
                out.append({"phrase": match.group(0), "kind": "unmeasurable", "where": where})
    return out


def _norm(text):
    return re.sub(r"\s+", "", str(text or "")).lower()


_INDEX = {key: {_norm(n) for n in names} for key, names in SYN.items()}


def _match(cell):
    whole = _norm(cell)
    if not whole:
        return None
    for key, names in _INDEX.items():
        if whole in names:
            return key
    for part in re.split(r"[/／\n|]+", str(cell or "")):          # bilingual header: "ケース番号/ID"
        piece = _norm(part)
        if piece and piece != whole:
            for key, names in _INDEX.items():
                if piece in names:
                    return key
    return None


def read_workbook(path, sheet_name=None):
    """(sheet title, header row, [raw case dicts], {key: column}). Raises SchemaError when no testcase table is found."""
    import openpyxl
    book = openpyxl.load_workbook(path, data_only=True, read_only=False)
    for sheet in book:
        if sheet_name is not None and sheet.title != sheet_name:
            continue
        for header in range(1, 16):
            cols = {}
            for col in range(1, sheet.max_column + 1):
                key = _match(sheet.cell(header, col).value)
                if key and key not in cols:
                    cols[key] = col
            if "no" in cols and ("exp" in cols or "act" in cols):
                rows = []
                for row in range(header + 1, sheet.max_row + 1):
                    ident = sheet.cell(row, cols["no"]).value
                    if ident in (None, ""):
                        continue
                    def value(col):
                        cell = sheet.cell(row, col)
                        if cell.value is None:
                            for area in sheet.merged_cells.ranges:
                                if cell.coordinate in area:
                                    return sheet.cell(area.min_row, area.min_col).value
                        return cell.value
                    item = {k: ("" if value(c) is None else str(value(c)).strip()) for k, c in cols.items()}
                    # Japanese UTS tables split the heading and detailed title across C/D.
                    if cols.get("view", 3) == 3 and "cls" not in cols and cols.get("act", 0) > 4:
                        item["view"] = str(value(4) or value(3) or "").strip()
                    item["no"] = str(ident).strip()
                    item["_row"] = row
                    rows.append(item)
                return sheet.title, header, rows, cols
    raise SchemaError("no testcase table found: the header row needs an ID column and an expected-result or action column", Path(path).name)


def find_files(text, folders):
    """Every file the text names -> [{text, status: found|missing, path}]. Looked for as given, then in each folder, then by name below the folders."""
    names = []
    for pattern in (QUOTED_FILE, BARE_FILE):
        for match in pattern.finditer(text):
            if match.group(1) not in names:
                names.append(match.group(1))
    out = []
    for name in names:
        found = None
        candidate = Path(name)
        if candidate.is_absolute() and candidate.exists():
            found = candidate
        for folder in folders:
            if found:
                break
            if (folder / name).exists():
                found = folder / name
        for folder in folders:
            if found:
                break
            hit = next(iter(folder.rglob(Path(name).name)), None) if folder.is_dir() else None
            if hit:
                found = hit
        out.append({"kind": "file", "text": name, "status": "found" if found else "missing", "path": str(found) if found else ""})
    return out


def select(cases_doc, spec):
    """Keep only the cases the user named, from the whole workbook (no new file is needed). `spec` is a comma-separated list: each item is a case id, a range of
    them in the workbook's order (`A..C`), or words of the title (every case whose id or title contains them). A word that matches nothing is an error that lists what exists."""
    items = [x.strip() for x in re.split(r"[,;\n]", spec or "") if x.strip()]
    if not items:
        return cases_doc
    cases = cases_doc["cases"]
    ids = [c["id"] for c in cases]
    chosen, unknown = [], []
    for item in items:
        if ".." in item:
            first, _, last = (p.strip() for p in item.partition(".."))
            if first in ids and last in ids and ids.index(first) <= ids.index(last):
                chosen += ids[ids.index(first):ids.index(last) + 1]
            else:
                unknown.append(item)
        elif item in ids:
            chosen.append(item)
        else:
            hits = [c["id"] for c in cases if item.lower() in (c["id"] + " " + c.get("title", "")).lower()]
            chosen += hits
            if not hits:
                unknown.append(item)
    if unknown:
        raise SchemaError(f"no case matches {', '.join(unknown)}. The workbook has: " + "; ".join(f"{c['id']} ({c.get('title', '')[:30]})" for c in cases[:40]) + (" ..." if len(cases) > 40 else ""), "cases")
    keep = [i for i in dict.fromkeys(chosen)]
    out = dict(cases_doc, cases=[c for c in cases if c["id"] in keep])
    for c in out["cases"]:
        c["case_refs"] = [r for r in c.get("case_refs", []) if r in keep]
    out["selection"] = {"of": len(cases), "ids": keep}
    return out


def build(path, reference_dirs=(), selection=None, sheet_name=None):
    """cases.json content for a testcase workbook."""
    path = Path(path)
    if not path.is_file():
        raise SchemaError(f"file not found: {path}", "testcase file")
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise SchemaError("only .xlsx / .xlsm testcase files are read", path.name)
    if selection and "!" in selection:
        combined, tables, seen, documents = [], {}, set(), {}
        for part in re.split(r"[,;\n]", selection):
            name, sep, area = part.strip().rpartition("!")
            match = re.fullmatch(r"[A-Za-z]+(\d+):[A-Za-z]+(\d+)", area)
            if not sep or not match:
                raise SchemaError("Use Sheet!A64:R64 for every selected region", "cases")
            name = name.strip("'")
            if name not in documents:
                documents[name] = build(path, reference_dirs, sheet_name=name)
            doc = documents[name]
            first, last = map(int, match.groups())
            chosen = [c for c in doc["cases"] if first <= c["row"] <= last]
            if not chosen:
                raise SchemaError(f"No testcase in {part}", "cases")
            tables[name] = {k: doc["source"][k] for k in ("header_row", "columns")}
            for case in chosen:
                case = dict(case)
                case["original_id"] = case["id"]
                case["id"] = f"{name}__{case['id']}"
                case["case_refs"] = []
                if case["id"] not in seen:
                    seen.add(case["id"])
                    combined.append(case)
        doc["cases"] = combined
        doc["source"]["tables"] = tables
        doc["source"]["sheet"] = next(iter(tables))
        doc["source"].update(tables[doc["source"]["sheet"]])
        doc["warnings"] = []
        doc["selection"] = {"of": len(combined), "ids": [c["id"] for c in combined], "regions": selection}
        return doc
    sheet, header, rows, columns = read_workbook(path, sheet_name)
    folders = [path.parent] + [Path(d) for d in reference_dirs]
    ids = [r["no"] for r in rows]
    warnings = [f"ID '{i}' appears {ids.count(i)} times; the first occurrence is used" for i in dict.fromkeys(ids) if ids.count(i) > 1]
    named = [i for i in dict.fromkeys(ids) if re.search(r"\D", i)]          # a bare number would match any digit in a sentence
    cases, seen = [], set()
    for row in rows:
        if row["no"] in seen:
            continue
        seen.add(row["no"])
        text = "\n".join(row.get(k, "") for k in ("pre", "act", "exp", "note"))
        lines = [NUMBERING.sub("", line).strip() for line in row.get("act", "").split("\n") if line.strip()]
        file_refs = find_files(text, folders)
        vague = find_vague({"expected": row.get("exp", ""), "steps": row.get("act", ""), "pre": row.get("pre", ""), "note": row.get("note", "")})
        if not row.get("exp", "").strip():
            vague.append({"phrase": "(no expected result)", "kind": "unmeasurable", "where": "expected"})
        attachment = ATTACHMENT_WORD.search(text)
        if attachment and not file_refs:
            vague.append({"phrase": attachment.group(0), "kind": "reference", "where": "text"})
        cases.append({
            "id": row["no"], "title": row.get("view", ""), "category": row.get("cls", ""), "type": row.get("typ", ""), "role": row.get("role", ""),
            "pre": row.get("pre", ""), "steps": lines, "expected": row.get("exp", ""), "note": row.get("note", ""), "sheet": sheet, "row": row["_row"],
            "refs": file_refs, "vague": vague,
            "case_refs": [other for other in named if other != row["no"] and re.search(r"(?<![\w-])" + re.escape(other) + r"(?![\w-])", text)],
        })
    try:
        sheets, pictures, comments = refs.workbook_extras(path)
        warnings += ([f"the workbook holds {pictures} embedded picture(s): they are not read as text. `oqa ref show \"{path}\"` saves them so you can look at them"] if pictures else [])
        warnings += ([f"the workbook holds {comments} cell comment(s): read them with `oqa ref show \"{path}\"`"] if comments else [])
        warnings += ([f"the workbook has other sheet(s) not read as testcases: {', '.join(s for s in sheets if s != sheet)} (`oqa ref show \"{path}\" --sheet <name>`)"] if len(sheets) > 1 else [])
    except Exception:          # a workbook that cannot be inspected twice must not stop the intake
        pass
    return {"schema_version": SCHEMA_VERSION,
            "source": {"file": str(path.resolve()), "name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "sheet": sheet, "header_row": header, "columns": columns},
            "warnings": warnings, "cases": cases}
