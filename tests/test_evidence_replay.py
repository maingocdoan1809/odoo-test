"""Evidence retention, replay pacing and review-session identity."""
import openpyxl
import pytest
from PIL import Image

from conftest import filled
from oqa import analysis, cli, events, live, paths, report, view
from oqa.state import GateError, Session
from oqa.store import SchemaError, read_json, write_json


def approved(session, ws):
    data = filled(session.cases(), ws)
    session.analysis_submitted(document=data)
    session.connected({})
    strategy, _ = analysis.strategy(data, session.cases(), ws)
    write_json(session.dir / "strategy.json", strategy)
    session.strategy_ready(strategy["sha"])
    session.confirm_current("test")


def test_all_intermediate_and_result_pictures_survive_in_order_in_excel(session, ws, monkeypatch):
    approved(session, ws)
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "_run", lambda *a, **kw: "")
    monkeypatch.setattr(live, "_box", lambda browser, target: None if target == "#missing" else ((50, 50, 200, 40), (600, 400)))
    captured = []

    def screenshot(browser, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 400), (240, len(captured) * 40, 180)).save(path)
        captured.append(path.read_bytes())

    monkeypatch.setattr(live, "_shot", screenshot)
    live.start(session, "ORD-001")
    live.shot(session, "ORD-001", 1, "#new", caption="Open the creation form")
    live.shot(session, "ORD-001", 1, "#name", caption="Entered name")
    with pytest.raises(GateError, match="pre-action picture alone"):
        live.step(session, "ORD-001", 1, "PASS", "The saved name is visible", target="#missing")
    live.step(session, "ORD-001", 1, "PASS", "The saved name is visible", target="#saved-name")
    step = next(e for e in events.read(session.dir) if e["kind"] == "step")
    assert len(step["images"]) == 3
    assert [item["target"] for item in step["images"]] == ["#new", "#name", "#saved-name"]
    assert [(session.dir / item["raw"]).read_bytes() for item in step["images"]] == captured
    assert len({item["raw"] for item in step["images"]}) == 3
    live.shot(session, "ORD-001", 1, "#status", caption="Saved status")
    images = view.case_records(session)[0]["attempts"][0]["steps"][0]["images"]
    assert len(images) == 4 and images[:3] == step["images"]
    output = report.build(session, ws)
    book = openpyxl.load_workbook(output["main"])
    sheet = next(sheet for sheet in book if len(sheet._images) == 4)
    assert len({image.anchor._from.row for image in sheet._images}) == 4
    assert [image.anchor._from.row for image in sheet._images] == sorted(image.anchor._from.row for image in sheet._images)


def test_new_analysis_requires_an_evidence_description_and_reads_legacy_cases(session, ws):
    data = filled(session.cases(), ws)
    data["cases"][0]["steps"][0]["evidence"] = ""
    with pytest.raises(SchemaError, match="needs evidence"):
        analysis.validate(data, session.cases(), ws)
    del data["cases"][0]["steps"][0]["evidence"]
    analysis.validate(data, session.cases(), ws)


def test_replay_pauses_after_each_action_and_checked_step(session, ws, monkeypatch):
    approved(session, ws)
    item = read_json(session.dir / "analysis.json")["cases"][0]
    script = "\n".join(f"// step {step['n']}\n// frame: #result\n// shows: Saved\n"
                       "await page.goto('http://localhost/example');\nawait page.locator('#save').click();"
                       for step in item["steps"])
    path = live.script_file(session, "ORD-001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    calls = []
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "run_actions", lambda browser, lines: calls.append(("run", lines)))
    monkeypatch.setattr(live.time, "sleep", lambda seconds: calls.append(("pause", seconds)))
    before = session.state()
    result = live.replay(session, "ORD-001", action_delay_ms=1000, step_delay_ms=2000)
    assert result["session"] == session.id and session.state() == before
    assert [kind for kind, value in calls[:6]] == ["run", "pause", "run", "pause", "run", "pause"]
    assert [value for kind, value in calls if kind == "pause"] == [1, 1, 2] * len(item["steps"])
    assert any("includes" in line for line in calls[4][1])
    calls.clear()
    live.replay(session, "ORD-001", action_delay_ms=0, step_delay_ms=0)
    assert all(kind == "run" for kind, value in calls)


@pytest.mark.parametrize("value", [-1, 60001, True, 1.5, "1000", None])
def test_invalid_replay_delays_are_rejected_before_opening_browser(session, value, monkeypatch):
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: pytest.fail("Must validate before opening"))
    with pytest.raises(GateError, match="milliseconds"):
        live.replay(session, "ORD-001", action_delay_ms=value)


def test_review_rounds_reuse_one_session_and_replace_analysis(ws, workbook, monkeypatch):
    monkeypatch.setattr(cli.init_server.InitServer, "run", lambda *a, **kw: {"workspace": ws["name"]})
    monkeypatch.setattr(cli.autostart, "ensure_prepare", lambda *a: None)
    monkeypatch.setattr(cli.autostart, "ensure_dashboard", lambda *a, **kw: False)
    parser = cli.build_parser()
    command = ["run", "start", "--file", str(workbook), "--cases", "ORD-001", "--ws", ws["name"], "--no-open"]
    first, _ = cli.cmd_run(parser.parse_args(command))
    session = Session.open(ws["name"], first["session"])
    data = filled(session.cases(), ws)
    data["cases"][0].update(verdict="needs_user", questions=["Which record should this case use?"])
    session.analysis_submitted(reviews={"ORD-001": data["cases"][0]["questions"]}, document=data)
    review, _ = cli.cmd_run(parser.parse_args(command))
    assert review["session"] == first["session"] and session.state()["waiting"]
    session.review_case("ORD-001", "Create a record for this case.")
    data["cases"][0].update(verdict="ready", questions=[])
    session.analysis_submitted(document=data)
    session.connected({})
    second, _ = cli.cmd_run(parser.parse_args(command))
    assert second["session"] == first["session"]
    data["cases"][0]["steps"][0]["expected"] = "Updated expectation after review"
    session.analysis_submitted(document=data)
    assert read_json(session.dir / "analysis.json")["cases"][0]["steps"][0]["expected"].startswith("Updated")
    assert paths.session_ids(ws["name"]) == [first["session"]]
    separate, _ = cli.cmd_run(parser.parse_args([*command, "--new-session"]))
    assert separate["session"] != first["session"]
    monkeypatch.setenv("OQA_SESSION", first["session"])
    resumed, _ = cli.cmd_run(parser.parse_args(command))
    assert resumed["session"] == first["session"]


def test_newly_available_references_do_not_create_another_review_session(ws, workbook, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.init_server.InitServer, "run", lambda *a, **kw: {"workspace": ws["name"]})
    monkeypatch.setattr(cli.autostart, "ensure_prepare", lambda *a: None)
    parser = cli.build_parser()
    command = ["run", "start", "--file", str(workbook), "--cases", "ORD-003", "--ws", ws["name"], "--no-open"]
    first, _ = cli.cmd_run(parser.parse_args(command))
    assert first["missing_refs"]
    (tmp_path / "refs" / "import.xlsx").write_bytes(b"newly shared reference")
    second, _ = cli.cmd_run(parser.parse_args(command))
    assert second["session"] == first["session"] and second["reused"]
    assert not second["missing_refs"]


def test_run_does_not_reuse_a_session_that_started_execution(session, ws, workbook, monkeypatch):
    approved(session, ws)
    with session.update() as state:
        state["channel"] = "path"
    monkeypatch.setattr(cli.init_server.InitServer, "run", lambda *a, **kw: {"workspace": ws["name"]})
    monkeypatch.setattr(cli.autostart, "ensure_prepare", lambda *a: None)
    args = cli.build_parser().parse_args(["run", "start", "--file", str(workbook), "--ws", ws["name"], "--no-open"])
    result, _ = cli.cmd_run(args)
    assert result["session"] != session.id and session.state()["phase"] == "EXECUTE"


def _saved_script(session, case="ORD-001"):
    item = read_json(session.dir / "analysis.json")["cases"][0]
    script = "\n".join(f"// step {step['n']}\n// frame: #result\n// shows: Saved\n"
                       "await page.goto('http://localhost/example');\nawait page.locator('#save').click();"
                       for step in item["steps"])
    path = live.script_file(session, case)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    return item


def test_replay_result_is_kept_apart_from_the_live_result(session, ws, monkeypatch):
    from oqa import replays
    approved(session, ws)
    _saved_script(session)
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
    live_before = session.state()["results"]
    assert replays.status(session, "ORD-001") == "NOT RUN"
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "run_actions", lambda browser, lines: "")
    live.replay(session, "ORD-001", action_delay_ms=0, step_delay_ms=0)
    assert replays.status(session, "ORD-001") == "PASS"
    saved = replays.latest(session, "ORD-001")
    assert saved["timing"]["calls"] >= 2 and saved["fast"] is False
    def refuse(browser, lines):
        if any("includes" in line for line in lines):
            raise GateError("timeout waiting for Saved")
    monkeypatch.setattr(live, "run_actions", refuse)
    with pytest.raises(GateError):
        live.replay(session, "ORD-001", action_delay_ms=0, step_delay_ms=0)
    assert replays.status(session, "ORD-001") == "FAIL"
    assert replays.latest(session, "ORD-001")["failed_step"] == 1
    assert len(replays.load(session)["ORD-001"]["runs"]) == 2
    assert session.state()["results"] == live_before                    # a failed replay never rewrites the live PASS
    record = view.case_records(session)[0]
    assert (record["live_status"], record["replay_status"], record["status"]) == ("PASS", "FAIL", "PASS")
    assert view.replay_counts([record]) == {"FAIL": 1}


def test_fast_mode_sets_both_viewing_pauses_to_zero(session, ws, monkeypatch):
    approved(session, ws)
    _saved_script(session)
    assert live.replay_delays(1000, 2000, fast=True) == (0, 0)
    assert live.replay_delays(1000, 2000) == (1000, 2000)
    calls = []
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "run_actions", lambda browser, lines: "")
    monkeypatch.setattr(live.time, "sleep", lambda seconds: calls.append(seconds))
    result = live.replay(session, "ORD-001", action_delay_ms=1000, step_delay_ms=2000, fast=True)
    assert calls == [] and result["action_delay_ms"] == 0 and result["step_delay_ms"] == 0
    from oqa import replays
    assert replays.latest(session, "ORD-001")["fast"] is True


def test_report_and_view_show_live_and_replay_separately(session, ws, monkeypatch):
    from oqa import replays
    approved(session, ws)
    _saved_script(session)
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
    replays.record(session, "ORD-001", "FAIL", reason="step 2 timed out", failed_step=2, timing={"total_s": 4.0, "pause_s": 0.0, "call_s": 3.5, "calls": 5, "steps": []})
    book = openpyxl.load_workbook(report.build(session, ws)["evidence_only"])
    cells = [str(cell.value) for sheet in book for row in sheet.iter_rows() for cell in row if cell.value is not None]
    assert any("FAIL" == text for text in cells) and any(text.startswith("Replay") or text == "Replay" for text in cells)
    summary = book[book.sheetnames[0]]
    headers = [cell.value for row in summary.iter_rows() for cell in row if cell.value in ("Replay", "Live result", "Replay result")]
    assert "Replay" in headers
    assert any("step 2 timed out" in text for text in cells)
