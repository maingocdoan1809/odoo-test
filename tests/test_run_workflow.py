from queue import Queue
from pathlib import Path
from threading import Thread

from openpyxl import load_workbook
import pytest

from oqa import browser, claims, cli, dashboard_server, dispatch, intake, report, worker_mcp
from oqa.case_context import build
from oqa.state import GateError, Session
from oqa.store import read_json, write_json

from conftest import filled, make_workbook


def test_scoped_case_reviews_and_context(ws, workbook):
    session = Session.create(ws["name"], workbook, intake.build(workbook), channel="path")
    session.analysis_submitted(reviews={"ORD-001": ["Which order?"], "ORD-002": ["Which list?"]})
    session.review_case("ORD-001", "Use the order created by this case.")
    assert session.state()["waiting"]["review"]
    assert "ORD-001" not in session.state()["reviews"]
    assert build(session, "ORD-001")["feedback"][0]["answer"].startswith("Use the order")
    assert build(session, "ORD-002")["feedback"] == []
    session.review_case("ORD-002", skip=True)
    assert session.state()["waiting"] is None
    assert session.state()["skipped_reviews"]["ORD-002"]
    assert session.state()["phase"] == "ANALYZE"


def test_five_case_worker_slots(ws, tmp_path):
    ids = [f"C{n}" for n in range(6)]
    workbook = make_workbook(tmp_path / "six.xlsx", [(case, "Case", "", "Open", "Visible") for case in ids])
    session = Session.create(ws["name"], workbook, intake.build(workbook))
    session.analysis_submitted()
    session.connected({})
    session.strategy_ready("approved")
    session.confirm("test", "approved")
    write_json(session.dir / "strategy.json", {"order": ids})
    write_json(session.dir / "analysis.json", {"cases": [{"id": case} for case in ids]})
    for index, case in enumerate(ids[:5]):
        assert claims.claim(session, f"worker-{index}", case)[0] == case
    assert claims.claim(session, "worker-5", ids[5])[0] is None
    claims.release(session, ids[0])
    assert claims.claim(session, "worker-5", ids[5])[0] == ids[5]


def test_path_report_preserves_original(ws, workbook):
    before = workbook.read_bytes()
    session = Session.create(ws["name"], workbook, intake.build(workbook), channel="path")
    result = report.build(session, ws)
    target = Path(result["main"])
    assert workbook.read_bytes() == before
    assert target != workbook
    with target.open("rb") as handle:
        exported = load_workbook(handle)
        assert "Cases" in exported.sheetnames


def test_run_picker_returns_selected_workspace_and_cases(ws, workbook, monkeypatch):
    def choose(server, announce, open_browser):
        assert server.name == ws["name"]
        assert not open_browser
        return {"workspace": ws["name"]}

    monkeypatch.setattr(cli.init_server.InitServer, "run", choose)
    args = cli.build_parser().parse_args([
        "run", "start", "--file", str(workbook), "--cases", "ORD-001",
        "--ws", ws["name"], "--no-open",
    ])
    result, _ = cli.cmd_run(args)
    session = Session.open(ws["name"], result["session"])
    assert session.state()["channel"] == "path"
    assert [case["id"] for case in session.cases()["cases"]] == ["ORD-001"]
    assert not result["dashboard_started"]


def test_dashboard_reviews_before_final_approval(ws, workbook, tmp_path):
    pytest.importorskip("playwright")
    cases = intake.select(intake.build(workbook), "ORD-001,ORD-002")
    session = Session.create(ws["name"], workbook, cases, channel="path")
    context = ["--ws", session.ws, "--session", session.id]
    decision = {
        "verdict": "needs_user", "questions": ["Which record should this test use?"],
        "data_considered": "The expected business rule needs a user decision.",
    }
    submission = tmp_path / "review.json"
    write_json(submission, filled(cases, ws, **{"ORD-001": decision, "ORD-002": decision}))
    parser = cli.build_parser()
    cli.cmd_analyze(parser.parse_args(["analyze", "--submit", str(submission), *context]))
    with pytest.raises(GateError):
        claims.claim(session, "early", "ORD-001")

    app = dashboard_server.Dashboard((session.ws, session.id), timeout=30)
    urls = Queue()
    Thread(target=lambda: app.serve(urls.put, open_browser=False, forever=True), daemon=True).start()
    with browser.launch() as new_page:
        _, page = new_page()
        page.goto(urls.get(timeout=5))
        page.locator("tr[data-id='ORD-001']").click()
        page.locator("#reviewText").fill("Create a fresh testcase record.")
        page.locator("#sendReview").click()
        page.locator("#sendReview").wait_for(state="hidden")
        page.locator("tr[data-id='ORD-002']").click()
        page.locator("#skipReview").click()
        page.locator("#skipReview").wait_for(state="hidden")
        assert session.state()["waiting"] is None
        cli.cmd_analyze(parser.parse_args(["analyze", "--template", "--out", str(submission), *context]))
        revised = read_json(submission)
        assert revised["_user_answers"][0]["case"] == "ORD-001"
        revised["cases"][0].update(verdict="ready", questions=[])
        write_json(submission, revised)
        cli.cmd_analyze(parser.parse_args(["analyze", "--submit", str(submission), *context]))
        session.connected({})
        strategy, _ = cli.cmd_strategy(parser.parse_args(["strategy", *context]))
        assert strategy["order"] == ["ORD-001"]
        page.locator("#confirm").click()
        page.locator("#confirm").wait_for(state="hidden")
        assert session.state()["phase"] == "EXECUTE"
        assert claims.claim(session, "approved", "ORD-001")[0] == "ORD-001"


def test_dispatch_reassigns_once_after_a_worker_exits(ws, workbook, monkeypatch):
    cases = intake.select(intake.build(workbook), "ORD-001")
    session = Session.create(ws["name"], workbook, cases, channel="path")
    session.analysis_submitted(document=filled(cases, ws))
    session.connected({})
    session.strategy_ready("approved")
    session.confirm("test", "approved")
    write_json(session.dir / "strategy.json", {"order": ["ORD-001"], "excluded": []})
    called = []
    monkeypatch.setattr(dispatch.shutil, "which", lambda name: "dummy")
    monkeypatch.setattr(dispatch, "_advise", lambda *args: "Start again with the mapped form.")

    def worker(session, case, agent, executable, model, timeout):
        called.append(agent)
        return "Worker exited before opening the browser."

    monkeypatch.setattr(dispatch, "_worker", worker)
    outcome = dispatch.run(session, "haiku", "sonnet", 5)
    assert called == ["dispatch-1-1", "dispatch-1-2"]
    assert outcome["phase"] == "REPORT"
    assert outcome["results"]["ORD-001"]["status"] == "BLOCKED"


def test_worker_mcp_only_serves_assigned_case(ws, workbook, monkeypatch):
    cases = intake.select(intake.build(workbook), "ORD-001")
    session = Session.create(ws["name"], workbook, cases, channel="path")
    session.analysis_submitted(document=filled(cases, ws))
    session.connected({})
    session.strategy_ready("approved")
    session.confirm("test", "approved")
    write_json(session.dir / "strategy.json", {"order": ["ORD-001"], "excluded": []})
    claims.claim(session, "assigned", "ORD-001")
    with pytest.raises(GateError, match="does not own"):
        worker_mcp.serve(session, "ORD-001", "different")

    def handle(tools, tool_call):
        assert any(tool["name"] == "step" for tool in tools)
        assert '"id": "ORD-001"' in tool_call("context", {})
        assert '"id": "ORD-002"' not in tool_call("context", {})

    monkeypatch.setattr(worker_mcp.mcp_server, "serve", handle)
    worker_mcp.serve(session, "ORD-001", "assigned")


def test_the_worker_prompt_can_be_built_and_keeps_its_literal_braces(session, tmp_path):
    """The prompt is an f-string: a literal brace pair left single makes every dispatched worker crash before its first step."""
    from oqa import dispatch
    from test_flow import executing

    executing(session, tmp_path)
    prompt = dispatch._prompt(session, "ORD-001", "dispatch-1-1")
    assert "Execute case ORD-001" in prompt and '{"$ref": ...}' in prompt and '{"name":"<the key>"' in prompt


def test_a_bad_conclusion_reached_on_leftover_data_is_reopened_instead_of_accepted(session, tmp_path, monkeypatch):
    from oqa import dispatch, events
    from test_flow import executing

    executing(session, tmp_path)
    monkeypatch.setattr(dispatch.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no model in this test")))
    fail = {"status": "FAIL", "category": "application", "reason": "Site A was deleted without an error"}
    assert dispatch.audit(session, "claude", "haiku", "ORD-001", fail) is None                        # nothing suspicious, the model cannot be reached: the conclusion stands
    events.append(session.dir, "fixture_prepared", case="ORD-001", attempt=1, key="ZZ_AUTOTEST_ORD_ITEM", code="x", model="m", record_id=None)
    assert "ZZ_AUTOTEST_ORD_ITEM" in dispatch.audit(session, "claude", "haiku", "ORD-001", fail)      # a fixed key: it may have found an earlier run's record
    blocked = {"status": "BLOCKED", "category": "precondition", "reason": "A stale pre-existing record shows a second row"}
    assert "left in the environment" in dispatch.audit(session, "claude", "haiku", "ORD-002", blocked)
