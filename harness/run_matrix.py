"""Run the workbook through Sonnet analysis and the real Claude worker queue."""
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import mkdtemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oqa import analysis, build_id, connect, dashboard_server, intake, live, report, script_bundle, sitemap, workspace
import cli_agents
from oqa.browser import launch
from oqa.odoo_ui import Odoo
from oqa.secrets import MemoryStore
from oqa.state import Session
from oqa.store import read_json, write_json

HERE = Path(__file__).resolve().parent


HARNESS_NOTES = """Plan every Odoo testcase below for real browser execution. Use concise English.
Preserve each testcase's complete intent. Consolidate into four to six meaningful evidence steps.
The expected text must describe a specific observable state, value or error. Testcases run independently.
Create fixture records for mutations with the keys fixture(action=key) returns; never write a literal record name or code in a step, say "a site with this run's own key". fixture ui can prepare related records
using {"$fixture":"<key>"}; verify the application under test through the UI. Do not skip steps.
For import cases use the native wizard and provided CSV templates. Include any failure/recovery specified.
H12 uses Operator and Manager, and must re-find the case's own record after switching accounts.
H18's BD says Submitted Manager sees Approve and Reset to Draft; Submit is absent before and after reload in Submitted.
H19's test persona grants permission to create the test data. H20 will be skipped by the test persona.
"""


def model_plans(cases, data, base, notes=None):
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
    prompt = (notes if notes is not None else HARNESS_NOTES) + """
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


def seed_conflict(session, document, cases, ws, choice):
    """OQA_MATRIX_CONFLICT=bd|testcase: H18's testcase says the opposite of its BD on step 3. The harness persona (standing in for the user) chooses which one the test follows."""
    item = next((c for c in document["cases"] if c["id"] == "H18"), None)
    if not item or item["verdict"] != "ready":
        raise SystemExit("OQA_MATRIX_CONFLICT needs H18 selected and ready")
    step = next(s for s in item["steps"] if s["n"] == 3)
    step["expected"] = "Approve and Reset to Draft are visible and Submit is absent."
    step["oracle"] = {"source": "bd", "ref": "BD-H18.txt", "quote": "a Manager sees Approve and Reset to Draft. Submit is hidden."}
    item["testcase_conflicts"] = [{"step": 3, "testcase": "Submit is still visible and Approve is absent after submission",
                                   "bd": "Approve and Reset to Draft visible, Submit hidden", "ref": "BD-H18.txt"}]
    analysis.validate(document, cases, ws)
    session.analysis_submitted(document=document)
    code, answer = dashboard_server.Dashboard((session.ws, session.id)).handle(
        "POST", "/api/conflict", {}, {"ws": session.ws, "id": session.id, "case": "H18", "step": 3, "choice": choice})
    assert code == 200 and answer.get("ok"), answer
    print(f"Harness persona chose to follow the {choice} for H18 step 3", flush=True)


def pause(variable, why):
    seconds = int(os.environ.get(variable) or 0)
    if seconds:
        print(f"Waiting {seconds}s {why}", flush=True)
        time.sleep(seconds)


def show_dashboard(session, root):
    """OQA_MATRIX_LIVE=1: a real dashboard server on this run's home, so the run can be watched (and ticked) in a browser."""
    subprocess.Popen([sys.executable, str(HERE.parent / "oqa.py"), "dashboard", "--ws", session.ws, "--session", session.id, "--no-open"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pointer = root / "dashboard.json"
    for _ in range(40):
        if pointer.exists():
            print("Dashboard:", read_json(pointer)["url"] + f"#{session.ws}/{session.id}", flush=True)
            return
        time.sleep(0.5)


def run(suite=None):
    """`suite` (harness/run_complex.py) swaps the workbook, reference folder, workspace name and planner notes, and makes a non-PASS a reported result instead of an assertion."""
    suite = suite or {}
    sys.stdout.reconfigure(encoding="utf-8")         # case reasons are Vietnamese; a Windows console code page must not abort the run after the workers finished
    root = Path(mkdtemp(prefix="oqa-real-matrix-"))
    os.environ["ODOO_TEST_HOME"] = str(root)
    print("Matrix log folder:", root, flush=True)
    print("OQA build:", build_id(), flush=True)
    base = os.environ.get("OQA_SMOKE_URL", "http://127.0.0.1:8079")
    accounts = [{"name": name, "login": "oqa-smoke-" + name.lower()} for name in ("Manager", "Operator")]
    password = os.environ["OQA_SMOKE_PASSWORD"]
    store = MemoryStore()
    ws = workspace.save({"name": suite.get("workspace", "harness-matrix"), "url": base, "db": "oqa_harness",
                         "profiles": accounts, "reference_dirs": [str(suite.get("fixtures", HERE / "fixtures"))]},
                        store, {account["name"]: password for account in accounts})
    store.set(workspace.secret_ref(ws["name"], "Mapper"), password)
    with launch() as new_page:
        _, page = new_page()
        connect.login(page, ws, {"name": "Mapper", "login": os.environ["OQA_SMOKE_MAPPER"]}, store)
        data = sitemap.build(Odoo(page, base).rpc, profile="Mapper")
        sitemap.save(ws["name"], data)
    workbook = root / suite.get("workbook", "odoo-harness-20.xlsx")
    shutil.copy2(HERE / suite.get("source", workbook.name), workbook)
    cases = intake.select(intake.build(workbook, [suite.get("fixtures", HERE / "fixtures")]), os.environ.get("OQA_MATRIX_CASES"))
    session = Session.create(ws["name"], workbook, cases, channel="path")
    print("Matrix session:", session.id, "cases:", len(cases["cases"]), flush=True)
    plans = model_plans(cases["cases"], data, base, suite.get("notes"))
    document = analysis.template(cases, ws, session)
    for item, original in zip(document["cases"], cases["cases"]):
        plan = plans[item["id"]]
        drafted = plan["steps"]
        if item["id"] == "H20" and not drafted:
            drafted = [{"action": step, "expected": "Await the referenced specification."} for step in original["steps"]]
        item.update(profile=original["role"].split(",")[0].strip(),   # a case that uses two roles starts as the first
                     verdict="ready", clarity={"score": 4, "notes": "Harness specification"},
                    sufficiency={"score": 4, "missing": []}, edge_cases=plan["edges"] or ["Data from previous runs"],
                    replay_records=[{"record": "every record this case creates or uses", "how": "run_key", "detail": ""}],      # the harness cases make their own data with this run's key
                    steps=[dict(step, n=index) for index, step in enumerate(drafted, 1)])
    if suite:
        analysis.validate(document, cases, ws)
        session.analysis_submitted(document=document)
    else:
        review(session, document, ws, cases)
    conflict_choice = os.environ.get("OQA_MATRIX_CONFLICT")
    if conflict_choice:
        seed_conflict(session, document, cases, ws, conflict_choice)
    connect.connect(session, ws, store)
    if os.environ.get("OQA_MATRIX_LIVE"):
        show_dashboard(session, root)
    pick = [c.strip() for c in os.environ.get("OQA_MATRIX_SELECT", "").split(",") if c.strip()]
    if pick:                                     # OQA_MATRIX_SELECT=A,B: only these are ticked; the others wait as "left out" and are added after the first run
        session.select_cases(pick, by="dashboard")
        print("Cases ticked:", ", ".join(pick), flush=True)
    else:
        strategy, markdown = analysis.strategy(read_json(session.dir / "analysis.json"), cases, ws)      # the saved analysis (retrieval context stripped), as `oqa strategy` reads it
        write_json(session.dir / "strategy.json", strategy)
        (session.dir / "strategy.md").write_text(markdown, encoding="utf-8")
        session.strategy_ready(strategy["sha"])
    pause("OQA_MATRIX_PAUSE", "before the harness persona approves (tick or untick on the dashboard now)")
    strategy = read_json(session.dir / "strategy.json")          # what is on disk now: the user may have changed the ticks
    dashboard = dashboard_server.Dashboard((session.ws, session.id))
    result = dashboard.handle("POST", "/api/confirm", {}, {"ws": session.ws, "id": session.id, "strategy_sha": strategy["sha"]})
    assert result == (200, {"ok": True}), result
    print("Harness persona approved:", ", ".join(strategy["order"]), flush=True)
    outcome = cli_agents.run(session, "haiku", "sonnet", suite.get("timeout", 420)) if strategy["order"] else {"results": {}}
    print(json.dumps(outcome, ensure_ascii=False, indent=2), flush=True)
    left = (read_json(session.dir / "strategy.json") or {}).get("deferred", [])
    if left and os.environ.get("OQA_MATRIX_ADD"):          # the cases that were left out join the same session, as the "run more" box does
        pause("OQA_MATRIX_PAUSE2", f"before the harness persona adds {', '.join(left)} (press the run-more button now if you want)")
        left = (read_json(session.dir / "strategy.json") or {}).get("deferred", [])
        if left:
            code, answer = dashboard.handle("POST", "/api/run_more", {}, {"ws": session.ws, "id": session.id, "cases": left})
            assert code == 200 and answer.get("ok"), answer
        print("Cases added to the run:", ", ".join(left), flush=True)
        outcome = cli_agents.run(session, "haiku", "sonnet", suite.get("timeout", 420))
        print(json.dumps(outcome, ensure_ascii=False, indent=2), flush=True)
        strategy = read_json(session.dir / "strategy.json")
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
    wanted = {case: "PASS" for case in strategy["order"]}
    if conflict_choice == "testcase":
        wanted["H18"] = "FAIL"          # the user put the test on a testcase the application contradicts: a FAIL, judged against the testcase
    if suite and not suite.get("strict", True):
        print("Results:", {case: outcome["results"].get(case, {}).get("status", "NO RESULT") for case in strategy["order"]}, flush=True)
        return outcome
    assert all(outcome["results"].get(case, {}).get("status") == status for case, status in wanted.items()), f"Matrix results differ from {wanted}."
    print(f"Passed {len(strategy['order'])} runnable cases.", flush=True)


if __name__ == "__main__":
    run()
