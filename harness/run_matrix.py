"""Run the workbook through Sonnet analysis and the real Claude worker queue."""
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import mkdtemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oqa import analysis, build_id, connect, dashboard_server, dispatch, intake, live, report, script_bundle, sitemap, workspace
from oqa.browser import launch
from oqa.odoo_ui import Odoo
from oqa.secrets import MemoryStore
from oqa.state import Session
from oqa.store import write_json

HERE = Path(__file__).resolve().parent


def model_plans(cases, data, base):
    screens = {
        node["model"]: f"{base}/odoo/action-{key.split(':')[-1]}"
        for key, node in data["nodes"].items()
        if node.get("kind") == "action" and str(node.get("model")).startswith("oqa.harness.")
    }
    schema = {
        "type": "object",
        "properties": {"cases": {"type": "array", "items": {
            "type": "object", "required": ["id", "steps", "edges"],
            "properties": {
                "id": {"type": "string"},
                "edges": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "array", "items": {
                    "type": "object", "required": ["action", "expected"],
                    "properties": {"action": {"type": "string"}, "expected": {"type": "string"}},
                }},
            },
        }}},
        "required": ["cases"],
    }
    prompt = """Plan every Odoo testcase below for real browser execution. Use concise English.
Preserve each testcase's complete intent. Consolidate into four to six meaningful evidence steps.
The expected text must describe a specific observable state, value or error. Testcases run independently.
Create fixture records for mutations with unique ZZ_AUTOTEST_ keys. fixture apply can prepare related records
using {"$fixture":"ZZ_AUTOTEST_..."}; verify the application under test through the UI. Do not skip steps.
For import cases use the native wizard and provided CSV templates. Include any failure/recovery specified.
H12 uses Operator and Manager, and must re-find the case's own record after switching accounts.
H18's BD says Submitted Manager sees Approve and Reset to Draft; Submit is absent before and after reload in Submitted.
H19's test persona grants permission to create the test data. H20 will be skipped by the test persona.

Harness screen routes:
""" + json.dumps(screens) + "\n\nCases:\n" + json.dumps(cases, ensure_ascii=False)
    result = subprocess.run(
        [shutil.which("claude") or "claude", "--print", "--model", "sonnet", "--output-format", "json",
         "--json-schema", json.dumps(schema), "--tools", ""],
        input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=240, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    envelope = json.loads(result.stdout)
    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        structured = json.loads(envelope.get("result") or "{}")
    plans = {case["id"]: case for case in structured["cases"]}
    assert set(plans) == {case["id"] for case in cases}
    return plans


def review(session, document, ws, cases):
    questions = {
        "H18": ["Please provide the BD for the Manager buttons on a submitted request."],
        "H19": ["May this test create a Site, Item and Request in the isolated harness database?"],
        "H20": ["Which reference defines the expected button order on this request?"],
    }
    reviews = {item["id"]: questions[item["id"]] for item in document["cases"] if item["id"] in questions}
    if not reviews:
        analysis.validate(document, cases, ws)
        session.analysis_submitted(document=document)
        return
    for item in document["cases"]:
        if item["id"] in reviews:
            item.update(verdict="needs_user", questions=reviews[item["id"]],
                        data_considered="The harness test persona must answer this case-specific question.")
    analysis.validate(document, cases, ws)
    session.analysis_submitted(reviews=reviews, document=document)
    dashboard = dashboard_server.Dashboard((session.ws, session.id))
    for case in reviews:
        fields = {"ws": session.ws, "id": session.id, "case": case}
        if case == "H18":
            fields["text"] = str(HERE / "fixtures" / "BD-H18.txt")
        elif case == "H19":
            fields["text"] = "The harness user grants permission to create the requested test data."
        else:
            fields.update(skip=True, text="The required reference is unavailable for this run.")
        code, answer = dashboard.handle("POST", "/api/review", {}, fields)
        assert code == 200 and answer.get("ok"), answer
    for item, original in zip(document["cases"], cases["cases"]):
        case = item["id"]
        if case in reviews:
            item.update(verdict="ready", questions=[])
        if case == "H18":
            item["refs"].append({"text": "BD-H18.txt", "kind": "file", "status": "ok",
                                 "path": str(HERE / "fixtures" / "BD-H18.txt")})
            item["vague_resolved"] = [
                {"phrase": flag["phrase"], "source": "ref:BD-H18.txt",
                 "concrete": "Submitted status, Approve and Reset to Draft visible, Submit hidden after reload."}
                for flag in original["vague"]
            ]
            item["steps"] = [
                {"n": 1, "action": "Create a case-owned Draft request with a Site and one valid Item line.",
                 "expected": "The request shows Draft and its one saved line."},
                {"n": 2, "action": "As Manager, press Submit.",
                 "expected": "The request shows Submitted."},
                {"n": 3, "action": "Inspect the request header.",
                 "expected": "Approve and Reset to Draft are visible and Submit is absent."},
                {"n": 4, "action": "Reload the same request and inspect the header again.",
                 "expected": "The status is Submitted, Approve and Reset to Draft are visible, and Submit is absent."},
            ]
        elif case == "H20":
            reason = session.state()["skipped_reviews"][case]
            item.update(verdict="blocked", reason=reason, data_considered=reason)
    analysis.validate(document, cases, ws)
    session.analysis_submitted(document=document)


def run():
    root = Path(mkdtemp(prefix="oqa-real-matrix-"))
    os.environ["ODOO_TEST_HOME"] = str(root)
    print("Matrix log folder:", root, flush=True)
    print("OQA build:", build_id(), flush=True)
    base = os.environ.get("OQA_SMOKE_URL", "http://127.0.0.1:8079")
    accounts = [{"name": name, "login": "oqa-smoke-" + name.lower()} for name in ("Manager", "Operator")]
    password = os.environ["OQA_SMOKE_PASSWORD"]
    store = MemoryStore()
    ws = workspace.save({"name": "harness-matrix", "url": base, "db": "oqa_harness",
                         "profiles": accounts, "reference_dirs": [str(HERE / "fixtures")]},
                        store, {account["name"]: password for account in accounts})
    store.set(workspace.secret_ref(ws["name"], "Mapper"), password)
    with launch() as new_page:
        _, page = new_page()
        connect.login(page, ws, {"name": "Mapper", "login": os.environ["OQA_SMOKE_MAPPER"]}, store)
        data = sitemap.build(Odoo(page, base).rpc, profile="Mapper")
        sitemap.save(ws["name"], data)
    workbook = root / "odoo-harness-20.xlsx"
    shutil.copy2(HERE / workbook.name, workbook)
    cases = intake.select(intake.build(workbook, [HERE / "fixtures"]), os.environ.get("OQA_MATRIX_CASES"))
    session = Session.create(ws["name"], workbook, cases, channel="path")
    print("Matrix session:", session.id, "cases:", len(cases["cases"]), flush=True)
    plans = model_plans(cases["cases"], data, base)
    document = analysis.template(cases, ws, session)
    for item, original in zip(document["cases"], cases["cases"]):
        plan = plans[item["id"]]
        drafted = plan["steps"]
        if item["id"] == "H20" and not drafted:
            drafted = [{"action": step, "expected": "Await the referenced specification."} for step in original["steps"]]
        item.update(profile=original["role"], verdict="ready", clarity={"score": 4, "notes": "Harness specification"},
                    sufficiency={"score": 4, "missing": []}, edge_cases=plan["edges"] or ["Data from previous runs"],
                    steps=[dict(step, n=index) for index, step in enumerate(drafted, 1)])
    review(session, document, ws, cases)
    connect.connect(session, ws, store)
    strategy, markdown = analysis.strategy(document, cases, ws)
    write_json(session.dir / "strategy.json", strategy)
    (session.dir / "strategy.md").write_text(markdown, encoding="utf-8")
    session.strategy_ready(strategy["sha"])
    dashboard = dashboard_server.Dashboard((session.ws, session.id))
    result = dashboard.handle("POST", "/api/confirm", {}, {"ws": session.ws, "id": session.id})
    assert result == (200, {"ok": True}), result
    print("Harness persona approved:", ", ".join(strategy["order"]), flush=True)
    outcome = dispatch.run(session, "haiku", "sonnet", 420) if strategy["order"] else {"results": {}}
    print(json.dumps(outcome, ensure_ascii=False, indent=2), flush=True)
    result = report.finish(session, ws) if session.state()["phase"] == "REPORT" else report.build(session, ws)
    output = Path(os.environ.get("OQA_SMOKE_OUT") or root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result["main"], output / "matrix-result.xlsx")
    write_json(output / "matrix-status.json", outcome)
    if any(item["status"] == "PASS" for item in outcome["results"].values()):
        (output / "matrix-scripts.zip").write_bytes(script_bundle.build(session))
    if os.environ.get("OQA_MATRIX_REPLAY") == "1":
        passed = [case for case in strategy["order"] if outcome["results"].get(case, {}).get("status") == "PASS"]

        def replay_twice(case):
            attempts = []
            for _ in range(2):
                try:
                    replay_session, status, reason = live.verify_replay(session, case, headed=False)
                    attempts.append({"session": replay_session.id, "status": status, "reason": reason})
                except Exception as error:
                    attempts.append({"status": "BLOCKED", "reason": str(error)})
            print(f"Replay {case}: " + ", ".join(attempt["status"] for attempt in attempts), flush=True)
            return case, attempts

        with ThreadPoolExecutor(max_workers=5) as pool:
            replays = dict(pool.map(replay_twice, passed))
        write_json(output / "matrix-replay.json", replays)
        assert all(attempt["status"] == "PASS" for attempts in replays.values() for attempt in attempts), "Some scripts did not pass both replays."
    assert all(outcome["results"].get(case, {}).get("status") == "PASS" for case in strategy["order"]), "Matrix contains unresolved cases."
    print(f"Passed {len(strategy['order'])} runnable cases.", flush=True)


if __name__ == "__main__":
    run()
