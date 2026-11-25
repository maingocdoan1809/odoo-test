"""Expose only the assigned case's OQA actions to a CLI worker."""
import json
import csv
import io
import re
from functools import partial
from pathlib import Path

from . import case_context, claims, fixtures, live, mcp_server, refs, sitemap, workspace
from .state import GateError


def schema(name, description, fields=(), required=()):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": {key: {"type": kind, **extra} for key, kind, extra in fields},
        "required": list(required),
    }}


def strings(*names):
    return tuple((name, "string", {}) for name in names)


TOOLS = [
    schema("start", "Open the approved case browser and return its plan."),
    schema("act", "Inspect or act in the same case browser. For run-code supply one await page statement per line.",
           (*strings("command", "code"), ("args", "array", {"items": {"type": "string"}}),
            ("record", "boolean", {"description": "Keep this verified run-code fragment in the next step's replay."})), ("command",)),
    schema("profile", "Switch to a signed-in workspace profile required by the case.", strings("name"), ("name",)),
    schema("evidence_before", "Capture a checkpoint before the next action. Repeat for intermediate states; all pictures are retained.",
           (*strings("target", "caption"), ("n", "integer", {})), ("n", "target")),
    schema("pgadmin_shot", "Database evidence for a step the testcase says to confirm on the database. Call it BEFORE recording that step (n is the next step; the picture becomes part of the step), with ONE read-only SQL (SELECT, WITH, SHOW, EXPLAIN), then record the step with step, its actual = what the rows showed. "
                           "It signs in to the workspace's PgAdmin, runs the SQL on the right database and keeps the real PgAdmin screen as evidence of step n. Returns the row count and the rows: judge PASS or FAIL from them. "
                           "Use this run's fixture key in the SQL, never a fixed name. Do not open PgAdmin yourself.",
           (*strings("sql", "caption"), ("n", "integer", {})), ("n", "sql")),
    schema("step", "Record observed PASS/FAIL and a screenshot for a planned step. Include code and shows for replay.",
           (*strings("status", "actual", "target", "code", "shows"), ("n", "integer", {})),
           ("n", "status", "actual", "target", "code", "shows")),
    schema("end", "Close the case. FAIL = the application differs from the expectation: give reason and control (which element you operated and how you verified it is the intended one). "
                  "BLOCKED/PENDING need a prior diagnose and a reason.",
           strings("status", "reason", "category", "control"), ("status",)),
    schema("diagnose", "When stuck, tell the coordinator exactly what you found. category automation (selector, script, browser; list what you tried) or precondition (missing data/document/decision; say what is needed). "
                       "Do not close BLOCKED/PENDING just because a selector was not found or time passed.",
           strings("category", "detail", "tried", "need"), ("category", "detail")),
    schema("context", "Read refreshed case context, or just sitemap navigation or validation and troubleshooting knowledge.",
           (*strings("screen", "issue", "profile", "state"), ("only", "string", {"enum": ["sitemap", "knowledge"]}))),
    schema("fixture", "Find fixture fields (plan), ask the data agent (advise), create a prefixed record (apply), create it through the form (ui; screen may be ledger, license_user or contract), "
                      "get this run's unique key (key, with a short business label for the record), or check an existing record to use read-only (ref, with value). For apply and ui, key is the field name and values_json maps fields to values; "
                      "a many2one value is {\"$ref\": \"existing name\"} or {\"$fixture\": \"key\"}, a relative date {\"$date\": -180}.",
           (*strings("action", "screen", "key", "need", "values_json", "label", "value"),
            ("values", "object", {"additionalProperties": True})), ("action", "screen")),
    schema("goto_fixture", "Open a record created by fixture apply, by its stable key value. Returns the replay action.",
           strings("action_url", "key"), ("action_url", "key")),
    schema("search", "Apply an observed Odoo search suggestion and record the action. Pass the value and the exact suggestion label from the snapshot.",
           strings("value", "suggestion"), ("value", "suggestion")),
    schema("open_import", "Open the native Odoo list import wizard on the given action URL and record the navigation.",
           strings("action_url"), ("action_url",)),
    schema("reference", "Read a reference registered for this case by name.", strings("name"), ("name",)),
    schema("upload_csv", "Upload an approved CSV reference (or, with source=download, a file saved by save_download) with run-specific placeholder values. For a later round of the same import (fix a cell, upload again) pass overrides: "
                         "[{row: data row number from 1, column: header name, value}] applied on top of the reference. Returns the replay action.",
           (*strings("name", "button", "source"), ("values", "object", {}), ("overrides", "array", {"items": {"type": "object"}})), ("name",)),
    schema("capture_id", "Right after a record is saved or opened (its id is in the address), store that id in the run variable `name`. Later fragments refer to it as $OQA_ID(name), "
                         "for example await page.waitForURL(new RegExp('action-90/' + $OQA_ID(rec))). Never write a record id yourself: every replay creates new records and captures their ids again.",
           strings("name"), ("name",)),
    schema("save_download", "Run one action that starts a file download (for example clicking Export) and save the file under `name`. Returns the file's rows as text (CSV as is, XLSX cell by cell) "
                            "so you can compare it with the screen. To edit it and import it again, call upload_csv with source=download and the same name. trigger = one await page action.",
           strings("name", "trigger"), ("name", "trigger")),
    schema("note", "Send the coordinator new facts or explain what is blocking the case.", strings("text"), ("text",)),
    *mcp_server.TOOLS,
]


def render_csv(path, substitutions, overrides=None):
    """The rows of an approved CSV with its {placeholders} filled, then the cell edits of a later round of the same import (fix one cell and upload again):
    `overrides` = [{row: data row number from 1, column: header name, value}]. A value may use placeholders too. The result is what the saved script uploads, so a replay repeats every round."""
    def fill(cell):
        for key, value in substitutions.items():
            cell = cell.replace("{" + key + "}", value)
        return cell

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [[fill(cell) for cell in row] for row in csv.reader(handle)]
    for edit in overrides or []:
        if not isinstance(edit, dict) or not {"row", "column", "value"} <= set(edit):
            raise ValueError("Each override needs row (data row number from 1), column (header name) and value.")
        header, row = rows[0], int(edit["row"])
        if edit["column"] not in header:
            raise ValueError(f"Column '{edit['column']}' is not in the CSV header ({', '.join(header)}).")
        if not 1 <= row < len(rows):
            raise ValueError(f"The CSV has {len(rows) - 1} data row(s); there is no row {row}.")
        rows[row][header.index(edit["column"])] = fill(str(edit["value"]))
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return output.getvalue()


def download_path(session, case, name):
    """Where a file saved by save_download lives: under the session, per case. A simple file name only."""
    if not name or Path(name).name != name:
        raise GateError("Give a simple file name for the download, without folders.")
    return session.dir / "downloads" / re.sub(r"[^\w.-]+", "_", case) / name


def download_text(path, limit=12000):
    """A downloaded file as the worker reads it: its rows as CSV text. CSV as it is; XLSX cell by cell (an empty cell is empty text)."""
    if path.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=True)
        rows = [["" if cell is None else str(cell) for cell in row] for row in book.worksheets[0].iter_rows(values_only=True)]
        book.close()
    else:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = [list(row) for row in csv.reader(handle)]
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    text = output.getvalue()
    return {"file": path.name, "data_rows": max(len(rows) - 1, 0), "header": rows[0] if rows else [], "text": text[:limit], "truncated": len(text) > limit}


def call(session, case, agent, name, args):
    try:
        return _call(session, case, agent, name, args)
    except (GateError, ValueError, OSError, KeyError, refs.RefError, sitemap.SitemapError) as error:
        raise sitemap.SitemapError(str(error)) from error


def _reference(session, case, name):
    packet = case_context.build(session, case)
    options = [*packet["references"], *(packet.get("analysis") or {}).get("refs", [])]
    match = next((item for item in options if item.get("path") and name in (item.get("name"), item.get("text"))), None)
    if not match:
        raise GateError("Choose a reference listed in this case's context.")
    path = Path(match["path"])
    refs.refuse_secret(path)
    return path


def _call(session, case, agent, name, args):
    if name == "context":
        packet = case_context.build(session, case, args.get("screen", ""), args.get("issue", ""),
                                    only=args.get("only", ""), profile=args.get("profile", ""), state=args.get("state", ""))
        return json.dumps(packet, ensure_ascii=False)
    if name == "start":
        return live.start(session, case)[1]
    if name == "act":
        return live.act(session, case, args["command"], args.get("args") or [], args.get("code"),
                        record=args.get("record") is True)[1]
    if name == "profile":
        return live.select_profile(session, case, args["name"])[1]
    if name == "evidence_before":
        return live.shot(session, case, args["n"], args["target"], caption=args.get("caption"))[1]
    if name == "pgadmin_shot":
        from . import pgadmin
        from .secrets import default_store

        result, picture = pgadmin.shot(session, workspace.load(session.ws), default_store(), case, args["n"], args["sql"], args.get("caption"))
        return pgadmin.summary(result, f"attached to step {args['n']}")
    if name == "step":
        return live.step(session, case, args["n"], args["status"], args["actual"], args["target"],
                         args["code"], args["shows"])[1]
    if name == "end":
        if args["status"] not in ("PASS", "FAIL", "BLOCKED", "PENDING"):
            raise GateError("Choose PASS, FAIL, BLOCKED or PENDING.")
        return live.end(session, case, args["status"], args.get("reason", ""), category=args.get("category") or None, control=args.get("control") or None)[1]
    if name == "diagnose":
        return live.diagnose(session, case, args["category"], args["detail"], args.get("tried", ""), args.get("need", ""))[1]
    if name == "note":
        claims.add_note(session, agent, args["text"], case=case)
        return "Note shared with the coordinator."
    if name == "fixture":
        if args["action"] == "key":
            return fixtures.run_key(session, case, args.get("label", ""))
        if args["action"] == "ref":
            model = fixtures.preset_model(session, args["screen"])
            return f"{model}: {fixtures.check_reference(session, case, model, args.get('value', ''))} exists once; use it read-only."
        if args["action"] == "ui":
            values = json.loads(args["values_json"]) if args.get("values_json") else args.get("values")
            result = fixtures.ui_create(session, case, args["screen"], args["key"], values)
            return json.dumps({"model": result["model"], "key": result["key"]}, ensure_ascii=False)
        if args["action"] == "apply":
            values = json.loads(args["values_json"]) if args.get("values_json") else args.get("values")
            result = fixtures.apply(session, case, args["screen"], args["key"], values)
            return json.dumps({"id": result["id"], "key": result["key"]}, ensure_ascii=False)
        data = sitemap.load(session.ws)
        key, form = fixtures.form_for(data, args["screen"])
        plan = {"model": form["model"], "fields": form["fields"], "screen": key,
                "prefix": workspace.load(session.ws)["test_prefix"]}
        if args["action"] == "advise":
            plan["proposal"] = fixtures.advise(session, case, plan, args["need"])
        return json.dumps(plan, ensure_ascii=False)
    if name == "goto_fixture":
        key = json.dumps(args["key"])
        route = json.dumps(args["action_url"].rstrip("/") + "/")
        lookup = "key => { const id = sessionStorage.getItem('oqa:fixture:' + key); if (!id) throw new Error('Fixture not prepared: ' + key); return id; }"
        code = f"await page.goto(new URL({route} + await page.evaluate({lookup}, {key}), page.url()).href);"
        observed = live.act(session, case, "run-code", [], code, record=True)[1]
        return json.dumps({"code": code, "output": observed}, ensure_ascii=False)
    if name == "reference":
        return refs.describe(_reference(session, case, args["name"]), 6000, None, session.dir / "refs")
    if name == "search":
        code = "\n".join([
            f"await page.getByRole('searchbox').fill({json.dumps(args['value'])});",
            f"await page.getByRole('link', {{name: {json.dumps(args['suggestion'])}, exact: true}}).click();",
        ])
        return live.act(session, case, "run-code", [], code, record=True)[1]
    if name == "open_import":
        code = "\n".join([
            f"await page.goto({json.dumps(args['action_url'])});",
            "await page.locator('.o_cp_action_menus .dropdown-toggle').click();",
            "await page.locator('.o_import_menu').click();",
        ])
        return live.act(session, case, "run-code", [], code, record=True)[1]
    if name == "capture_id":
        return live.act(session, case, "run-code", [], live.capture_id_code(args["name"]), record=True)[1]
    if name == "save_download":
        target = download_path(session, case, args["name"])
        action = re.fullmatch(r"\s*await\s+(page\..+?);?\s*", args["trigger"], re.S)
        if not action:
            raise GateError("trigger is one await page action that starts the download, for example: await page.getByRole('button', {name: 'Export', exact: true}).click();")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        code = f"await Promise.all([page.waitForEvent('download'), {action.group(1)}]).then(([download]) => download.saveAs({json.dumps(str(target))}));"
        live.act(session, case, "run-code", [], code, record=True)
        if not target.is_file():
            raise GateError("The action did not start a download: check the Export dialog (format, columns) and that the control was clicked.")
        return json.dumps(download_text(target), ensure_ascii=False)
    if name == "upload_csv":
        if args.get("source") == "download":
            path = download_path(session, case, args["name"])
            if not path.is_file():
                raise GateError(f"No download named {args['name']} for this case: save it first with save_download.")
        else:
            path = _reference(session, case, args["name"])
        if path.suffix.lower() != ".csv":
            raise GateError("Choose a CSV file for this case (in the Export dialog choose the CSV format).")
        output = render_csv(path, {key: str(value) for key, value in (args.get("values") or {}).items()}, args.get("overrides"))
        button = json.dumps(args.get("button") or "Upload Data File")
        code = (
            "await Promise.all([page.waitForEvent('filechooser'), "
            f"page.getByRole('button', {{name: {button}, exact: true}}).click()]).then(([chooser]) => "
            f"chooser.setFiles({{name: {json.dumps(path.name)}, mimeType: 'text/csv', "
            f"buffer: Buffer.from({json.dumps(output)})}}));"
        )
        observed = live.act(session, case, "run-code", [], code, record=True)[1]
        return json.dumps({"code": code, "output": observed}, ensure_ascii=False)
    return mcp_server.call(session.ws, name, args)


def serve(session, case, agent):
    if (claims.table(session).get(case) or {}).get("agent") != agent:
        raise GateError("This worker does not own the case.")
    mcp_server.serve(tools=TOOLS, tool_call=partial(call, session, case, agent))
