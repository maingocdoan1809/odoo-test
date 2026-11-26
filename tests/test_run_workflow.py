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


def test_oqa_tool_runs_a_case_tool_only_for_the_agent_that_holds_the_case(ws, workbook, capsys):
    cases = intake.select(intake.build(workbook), "ORD-001")
    session = Session.create(ws["name"], workbook, cases, channel="path")
    session.analysis_submitted(document=filled(cases, ws))
    session.connected({})
    session.strategy_ready("approved")
    session.confirm("test", "approved")
    write_json(session.dir / "strategy.json", {"order": ["ORD-001"], "excluded": []})
    claims.claim(session, "assigned", "ORD-001")
    context = ["--ws", session.ws, "--session", session.id]
    parser = cli.build_parser()
    listed, _ = cli.cmd_tool(parser.parse_args(["tool", "--list", *context]))
    assert {"save_download", "upload_csv", "open_import", "capture_id", "goto_fixture"} <= {t["name"] for t in listed["tools"]}
    with pytest.raises(GateError, match="does not hold"):
        cli.cmd_tool(parser.parse_args(["tool", "ORD-001", "context", "--agent", "different", *context]))
    with pytest.raises(GateError, match="JSON object"):
        cli.cmd_tool(parser.parse_args(["tool", "ORD-001", "context", "--agent", "assigned", "--args", "[1]", *context]))
    answer, text = cli.cmd_tool(parser.parse_args(["tool", "ORD-001", "context", "--agent", "assigned", *context]))
    assert '"id": "ORD-001"' in text and '"id": "ORD-002"' not in text and answer["tool"] == "context"


def test_goto_fixture_resolves_the_url_in_the_browser(monkeypatch):
    import json

    from oqa import live, worker_mcp

    recorded = []
    def act(_session, _case, _command, _args, code, record):
        assert record
        recorded.append(code)
        return {}, "opened record"
    monkeypatch.setattr(live, "act", act)
    result = worker_mcp.call(None, "ORD-001", "worker", "goto_fixture", {"action_url": "/odoo/action-40", "key": "fixture-key"})

    assert json.loads(result)["code"] == recorded[0]
    assert recorded[0].startswith("await page.goto(await page.evaluate(")
    assert "new URL(route + id, location.href)" in recorded[0]


def test_no_model_or_cli_is_started_by_oqa():
    """The host's own sub-agents do every job that needs a model: no module of oqa starts one."""
    import re as _re

    root = Path(dispatch.__file__).parent
    offenders = [f.name for f in root.glob("*.py") if _re.search(r"--print|which\(.claude.\)", f.read_text(encoding="utf-8")) and f.name != "cli.py"]
    assert not offenders, offenders
    assert not hasattr(dispatch, "run") and not hasattr(dispatch, "_worker")


def test_the_host_brief_can_be_built_and_keeps_its_literal_braces(session, tmp_path):
    """The brief is an f-string: a literal brace pair left single makes every handed-out brief crash."""
    from test_flow import executing

    executing(session, tmp_path)
    brief = dispatch._host_brief(session, "ORD-001", "host-1-ab12")
    assert "case worker host-1-ab12 for case ORD-001" in brief and "{ name: 'New' }" in brief and "oqa tool ORD-001 NAME" in brief


def test_the_fact_checks_reopen_a_conclusion_that_rests_on_leftover_data(session, tmp_path):
    from oqa import dispatch, events
    from test_flow import executing

    executing(session, tmp_path)
    fail = {"status": "FAIL", "category": "application", "reason": "Site A was deleted without an error"}
    assert dispatch.audit_facts(session, "ORD-001", fail) is None                                       # nothing suspicious: the conclusion stands
    events.append(session.dir, "fixture_prepared", case="ORD-001", attempt=1, key="ZZ_AUTOTEST_ORD_ITEM", code="x", model="m", record_id=None)
    assert "ZZ_AUTOTEST_ORD_ITEM" in dispatch.audit_facts(session, "ORD-001", fail)                      # a fixed key: it may have found an earlier run's record
    blocked = {"status": "BLOCKED", "category": "precondition", "reason": "A stale pre-existing record shows a second row"}
    assert "left in the environment" in dispatch.audit_facts(session, "ORD-002", blocked)


def test_a_csv_round_edits_single_cells_of_the_approved_file_and_keeps_the_placeholders(tmp_path):
    from oqa import worker_mcp

    path = tmp_path / "items.csv"
    path.write_text("Name,Code,Cost\n{run}-A,{run}-1,0\n{run}-B,{run}-2,-4\n", encoding="utf-8")
    first = worker_mcp.render_csv(path, {"run": "r1"})
    assert first.splitlines() == ["Name,Code,Cost", "r1-A,r1-1,0", "r1-B,r1-2,-4"]
    second = worker_mcp.render_csv(path, {"run": "r1"}, [{"row": 1, "column": "Cost", "value": "2"}, {"row": 2, "column": "Code", "value": "{run}-9"}])
    assert second.splitlines() == ["Name,Code,Cost", "r1-A,r1-1,2", "r1-B,r1-9,-4"]
    with pytest.raises(ValueError, match="not in the CSV header"):
        worker_mcp.render_csv(path, {}, [{"row": 1, "column": "Price", "value": "1"}])
    with pytest.raises(ValueError, match="no row 5"):
        worker_mcp.render_csv(path, {}, [{"row": 5, "column": "Cost", "value": "1"}])


def test_a_downloaded_file_is_read_as_rows_and_can_be_edited_for_the_next_import(tmp_path):
    from openpyxl import Workbook

    csv_file = tmp_path / "export.csv"
    csv_file.write_text("External ID,Name,Cost\nx.1,Item 1,5\nx.2,Item 2,8\n", encoding="utf-8")
    read = worker_mcp.download_text(csv_file)
    assert read["data_rows"] == 2 and read["header"] == ["External ID", "Name", "Cost"] and "x.2,Item 2,8" in read["text"]
    edited = worker_mcp.render_csv(csv_file, {}, [{"row": 1, "column": "Cost", "value": "6"}, {"row": 2, "column": "Cost", "value": "0"}])
    assert edited.splitlines() == ["External ID,Name,Cost", "x.1,Item 1,6", "x.2,Item 2,0"]
    book = Workbook()
    book.active.append(["Name", "Total"])
    book.active.append(["R2", 12])
    xlsx = tmp_path / "export.xlsx"
    book.save(xlsx)
    assert worker_mcp.download_text(xlsx)["text"].splitlines() == ["Name,Total", "R2,12"]


def test_a_download_name_cannot_leave_the_case_folder(session):
    with pytest.raises(GateError, match="simple file name"):
        worker_mcp.download_path(session, "ORD-001", "../x.csv")
    assert worker_mcp.download_path(session, "ORD-001", "export.csv").parent.name == "ORD-001"
    assert {"save_download", "upload_csv"} <= {tool["name"] for tool in worker_mcp.TOOLS}


def test_a_pass_whose_script_does_not_replay_is_reported_with_what_failed(session, monkeypatch):
    from oqa import dispatch, live

    monkeypatch.setattr(live, "replay", lambda *a, **k: (_ for _ in ()).throw(GateError("Step 3 in T-1.spec.js: locator.click: Timeout 30000ms exceeded.")))
    assert "Step 3" in dispatch.replay_check(session, "ORD-001")
    monkeypatch.setattr(live, "replay", lambda *a, **k: {"replay": "PASS"})
    assert dispatch.replay_check(session, "ORD-001") is None
    assert "record id in a URL" in dispatch.REPLAY_HINT
