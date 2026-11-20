"""Expose only the assigned case's OQA actions to a CLI worker."""
import json
import csv
import io
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
    schema("step", "Record observed PASS/FAIL and a screenshot for a planned step. Include code and shows for replay.",
           (*strings("status", "actual", "target", "code", "shows"), ("n", "integer", {})),
           ("n", "status", "actual", "target", "code", "shows")),
    schema("end", "Close the case. FAIL = the application differs from the expectation: give reason and control (which element you operated and how you verified it is the intended one). "
                  "BLOCKED/PENDING need a prior diagnose and a reason.",
           strings("status", "reason", "category", "control"), ("status",)),
    schema("diagnose", "When stuck, tell the coordinator exactly what you found. category automation (selector, script, browser; list what you tried) or precondition (missing data/document/decision; say what is needed). "
                       "Do not close BLOCKED/PENDING just because a selector was not found or time passed.",
           strings("category", "detail", "tried", "need"), ("category", "detail")),
    schema("context", "Read refreshed case context, sitemap hints and coordinator notes.", strings("screen", "issue")),
    schema("fixture", "Find fixture fields (plan), ask the data agent (advise), create a prefixed record (apply), create it through the form (ui; screen may be ledger, license_user or contract), "
                      "get this run's unique key (key, label optional), or check an existing record to use read-only (ref, with value). For apply and ui, key is the field name and values_json maps fields to values; "
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
    schema("upload_csv", "Upload an approved CSV reference with run-specific placeholder values. Returns the replay action.",
           (*strings("name", "button"), ("values", "object", {})), ("name", "values")),
    schema("note", "Send the coordinator new facts or explain what is blocking the case.", strings("text"), ("text",)),
    *mcp_server.TOOLS,
]


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
        packet = case_context.build(session, case, args.get("screen", ""), args.get("issue", ""))
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
    if name == "upload_csv":
        path = _reference(session, case, args["name"])
        if path.suffix.lower() != ".csv":
            raise GateError("Choose a CSV reference for this case.")
        substitutions = {key: str(value) for key, value in args["values"].items()}
        output = io.StringIO()
        def substitute(cell):
            for key, value in substitutions.items():
                cell = cell.replace("{" + key + "}", value)
            return cell

        with path.open(encoding="utf-8-sig", newline="") as handle:
            csv.writer(output).writerows([substitute(cell) for cell in row] for row in csv.reader(handle))
        button = json.dumps(args.get("button") or "Upload Data File")
        code = (
            "await Promise.all([page.waitForEvent('filechooser'), "
            f"page.getByRole('button', {{name: {button}, exact: true}}).click()]).then(([chooser]) => "
            f"chooser.setFiles({{name: {json.dumps(path.name)}, mimeType: 'text/csv', "
            f"buffer: Buffer.from({json.dumps(output.getvalue())})}}));"
        )
        observed = live.act(session, case, "run-code", [], code, record=True)[1]
        return json.dumps({"code": code, "output": observed}, ensure_ascii=False)
    return mcp_server.call(session.ws, name, args)


def serve(session, case, agent):
    if (claims.table(session).get(case) or {}).get("agent") != agent:
        raise GateError("This worker does not own the case.")
    mcp_server.serve(tools=TOOLS, tool_call=partial(call, session, case, agent))
