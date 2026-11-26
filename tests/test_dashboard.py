"""The dashboard's API: history, one session's view, pictures (and only pictures), and the two things only the user does."""
import json
import queue
import threading
import urllib.error
import urllib.request

import pytest

from conftest import filled, write
from oqa import dashboard_server, events, view, workspace
from oqa.state import Session
from oqa.store import read_json, write_json


class Up:
    def __init__(self, select=None):
        self.app = dashboard_server.Dashboard(select, timeout=30)
        self.urls = queue.Queue()
        threading.Thread(target=lambda: self.app.serve(self.urls.put, open_browser=False, forever=True), daemon=True).start()
        self.base = self.urls.get(timeout=5).split("/?")[0]

    def call(self, path, body=None, token=None):
        sep = "&" if "?" in path else "?"
        request = urllib.request.Request(f"{self.base}{path}{sep}t={token or self.app.token}", data=None if body is None else json.dumps(body).encode(),
                                         method="GET" if body is None else "POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if response.headers["Content-Type"].startswith("application/json") else raw)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")


@pytest.fixture
def up():
    return Up()


def at_confirm(session, tmp_path):
    config = workspace.load(session.ws)
    data = filled(session.cases(), config)
    write_json(session.dir / "analysis.json", write(tmp_path / "a.json", data) and data)
    session.analysis_submitted()
    session.connected({"profiles": {"Admin": {"landing_url": "x", "version": "17.0"}}, "version": "17.0"})
    doc, text = __import__("oqa.analysis", fromlist=["x"]).strategy(data, session.cases(), config)
    write_json(session.dir / "strategy.json", doc)
    (session.dir / "strategy.md").write_text(text, encoding="utf-8")
    session.strategy_ready(doc["sha"])


def test_history_and_view_are_served_with_the_token_only(session, up):
    assert up.call("/api/sessions")[1]["sessions"][0]["session"] == session.id
    status, body = up.call(f"/api/session?ws=demo&id={session.id}")
    assert status == 200 and body["phase"] == "ANALYZE" and body["total"] == 3 and [c["id"] for c in body["cases"]] == ["ORD-001", "ORD-002", "ORD-003"]
    assert up.call("/api/sessions", token="wrong")[0] == 403
    page = urllib.request.urlopen(f"{up.base}/?t={up.app.token}", timeout=5).read().decode()
    assert "__TOKEN__" not in page and up.app.token in page


def test_phase_track_marks_every_earlier_phase_done(session, tmp_path):
    at_confirm(session, tmp_path)
    states = {p["phase"]: p["state"] for p in view.session_view(session)["phases"]}
    assert states == {"ANALYZE": "done", "CONNECT": "done", "CONFIRM": "current", "EXECUTE": "todo", "REPORT": "todo", "DONE": "todo"}


def test_a_picture_is_served_but_nothing_outside_evidence(session, up):
    (session.dir / "evidence" / "A").mkdir(parents=True)
    (session.dir / "evidence" / "A" / "s1.png").write_bytes(b"\x89PNG" + b"0" * 20)
    (session.dir / "secret.png").write_bytes(b"\x89PNG" + b"1" * 20)
    ok = up.call(f"/api/image?ws=demo&id={session.id}&path=evidence/A/s1.png")
    assert ok[0] == 200 and ok[1].startswith(b"\x89PNG")
    for bad in ("secret.png", "evidence/../secret.png", "../../workspace.json", "evidence/A/s1.txt", "evidence"):
        assert up.call(f"/api/image?ws=demo&id={session.id}&path={bad}")[0] == 404, bad
    assert up.call(f"/api/image?ws=demo&id=..%2F..&path=x")[1].get("error")


def test_the_user_confirms_from_the_dashboard_and_a_tampered_strategy_is_refused(session, up, tmp_path):
    at_confirm(session, tmp_path)
    assert up.call(f"/api/session?ws=demo&id={session.id}")[1]["can_confirm"] is True
    doc = read_json(session.dir / "strategy.json")
    doc["order"] = doc["order"][::-1]
    write_json(session.dir / "strategy.json", doc)
    assert "strategy" in up.call("/api/confirm", {"ws": "demo", "id": session.id})[1]["error"]
    assert session.state()["phase"] == "CONFIRM"
    doc["order"] = doc["order"][::-1]
    write_json(session.dir / "strategy.json", doc)
    assert up.call("/api/confirm", {"ws": "demo", "id": session.id})[1] == {"ok": True}
    assert session.state()["phase"] == "EXECUTE" and session.state()["confirmation"]["by"] == "dashboard"


def test_the_user_answers_the_assistant_from_the_dashboard(session, up):
    assert up.call("/api/answer", {"ws": "demo", "id": session.id, "text": "x"})[1]["error"]          # nobody asked anything
    session.ask("Which customer?", "ORD-001")
    view_ = up.call(f"/api/session?ws=demo&id={session.id}")[1]
    assert view_["waiting"]["question"] == "Which customer?"
    assert up.call("/api/answer", {"ws": "demo", "id": session.id, "text": "  "})[1]["error"]
    assert up.call("/api/answer", {"ws": "demo", "id": session.id, "text": "Azure Interior"})[1] == {"ok": True}
    assert any(e["kind"] == "answered" and e["answer"] == "Azure Interior" for e in events.read(session.dir))


def test_case_statuses_follow_the_events(session, tmp_path):
    at_confirm(session, tmp_path)
    session.confirm_current("t")
    session.begin_attempt("ORD-001")
    events.append(session.dir, "case_started", case="ORD-001", attempt=1)
    assert {c["id"]: c["status"] for c in view.case_records(session)}["ORD-001"] == "RUNNING"
    events.append(session.dir, "attempt_finished", case="ORD-001", attempt=1, status="FAIL", reason="x")
    assert {c["id"]: c["status"] for c in view.case_records(session)}["ORD-001"] == "REPAIRING"
    session.finish_case("ORD-001", "BLOCKED", "needs data")
    records = {c["id"]: c for c in view.case_records(session)}
    assert records["ORD-001"]["status"] == "BLOCKED" and records["ORD-001"]["reason"] == "needs data" and records["ORD-002"]["status"] == "PLANNED"
    assert view.list_sessions()[0]["counts"]["BLOCKED"] == 1


def test_asking_for_the_dashboard_again_reuses_the_running_one_on_the_right_session(session, up, monkeypatch, capsys):
    from oqa import autostart, build_id, cli, paths
    write_json(paths.home() / "dashboard.json", {"url": f"{up.base}/?t={up.app.token}", "pid": 0, "build": build_id()})
    opened = []
    monkeypatch.setattr(autostart, "open_url", opened.append)
    assert cli.main(["dashboard", "--ws", "demo", "--session", session.id]) == 0
    assert "already running" in capsys.readouterr().out
    assert opened == [f"{up.base}/?t={up.app.token}#demo/{session.id}"]          # a new tab on THIS session; no second server was started


def test_a_stale_dashboard_pointer_is_not_trusted(session, monkeypatch):
    from oqa import cli, paths
    write_json(paths.home() / "dashboard.json", {"url": "http://127.0.0.1:9/?t=x", "pid": 0})
    assert cli._alive("http://127.0.0.1:9/?t=x") is False


def test_the_user_stops_resumes_and_hints_from_the_dashboard(session, up, tmp_path):
    at_confirm(session, tmp_path)
    session.confirm_current("t")
    sid = {"ws": "demo", "id": session.id}
    view_ = up.call(f"/api/session?ws=demo&id={session.id}")[1]
    assert view_["can_stop"] is True and view_["stopped"] is None
    assert up.call("/api/stop", sid)[1] == {"ok": True}
    assert up.call("/api/stop", sid)[1]["error"]                                                  # already stopped
    assert up.call(f"/api/session?ws=demo&id={session.id}")[1]["stopped"]["by"] == "dashboard"
    assert up.call("/api/resume", sid)[1] == {"ok": True}
    session.begin_attempt("ORD-001")
    case = next(c for c in up.call(f"/api/session?ws=demo&id={session.id}")[1]["cases"] if c["id"] == "ORD-001")
    assert case["can_hint"] is True and case["exhausted"] is False
    assert "Write the hint" in up.call("/api/hint", {**sid, "case": "ORD-001", "text": " "})[1]["error"]
    assert up.call("/api/hint", {**sid, "case": "ORD-001", "text": "use the Actions menu"})[1] == {"ok": True}
    case = next(c for c in up.call(f"/api/session?ws=demo&id={session.id}")[1]["cases"] if c["id"] == "ORD-001")
    assert case["hints"][0]["text"] == "use the Actions menu" and case["used"] == 0


def test_a_case_that_used_its_budget_is_flagged_as_needing_the_user(session, tmp_path):
    at_confirm(session, tmp_path)
    session.confirm_current("t")
    for _ in range(4):
        session.begin_attempt("ORD-001")
    assert next(c for c in view.case_records(session) if c["id"] == "ORD-001")["exhausted"] is True


def test_the_user_can_build_and_download_the_excel_evidence_from_the_dashboard(session, up, tmp_path):
    at_confirm(session, tmp_path)
    sid = {"ws": "demo", "id": session.id}
    assert up.call(f"/api/session?ws=demo&id={session.id}")[1]["reports"] == []
    status, built = up.call("/api/report", sid)
    assert status == 200 and built["ok"] and built["main"].startswith("Result_") and built["main"].endswith(".xlsx")
    shown = up.call(f"/api/session?ws=demo&id={session.id}")[1]["reports"]
    assert shown[0]["main"] is True and shown[0]["name"] == built["main"]
    got = urllib.request.urlopen(f"{up.base}/api/report?ws=demo&id={session.id}&name={built['main']}&t={up.app.token}", timeout=5)
    body = got.read()
    assert body[:2] == b"PK" and "spreadsheetml" in got.headers["Content-Type"] and built["main"] in got.headers["Content-Disposition"]
    for bad in ("../state.json", "SUMMARY.md/../../state.json", "nothing.xlsx"):
        assert up.call(f"/api/report?ws=demo&id={session.id}&name={bad}")[0] == 404, bad


def test_fonts_dropped_into_the_fonts_folder_are_served_to_the_page_without_a_token_but_nothing_else(up, tmp_path, monkeypatch):
    from oqa import localserver
    folder = tmp_path / "fonts"
    folder.mkdir()
    (folder / "BeVietnamPro-Regular.woff2").write_bytes(b"wOF2" + b"0" * 20)
    (folder / "notes.txt").write_text("not a font")
    (tmp_path / "secret.ttf").write_bytes(b"secret")
    monkeypatch.setattr(localserver, "FONTS", folder)

    def fetch(name, host=None):
        request = urllib.request.Request(f"{up.base}/font/{name}", headers={"Host": host} if host else {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers["Content-Type"], response.read()
        except urllib.error.HTTPError as error:
            return error.code, "", b""
    assert fetch("BeVietnamPro-Regular.woff2")[:2] == (200, "font/woff2")
    for bad in ("missing.woff2", "notes.txt", "..%2Fsecret.ttf", "%2e%2e/secret.ttf"):
        assert fetch(bad)[0] in (403, 404), bad
    assert fetch("BeVietnamPro-Regular.woff2", host="evil.example")[0] == 403           # only the page of this very server may use it


def test_a_dashboard_started_from_older_files_is_replaced_not_reused(up, capsys):
    from oqa import build_id, cli, paths
    assert len(build_id()) == 12 and build_id() == build_id()
    write_json(paths.home() / "dashboard.json", {"url": f"{up.base}/?t={up.app.token}", "pid": 0})            # a pointer from before builds were recorded
    assert cli.main(["dashboard", "--no-open", "--timeout", "2"]) == 0
    assert up.app.finished() is True                                                                       # the old one was asked to leave
    assert "Dashboard: http" in capsys.readouterr().out and not (paths.home() / "dashboard.json").exists()  # the new one announced itself, then ended and removed its pointer


def test_the_dashboard_that_matches_the_files_is_kept(up, capsys, monkeypatch):
    from oqa import build_id, cli, paths
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    write_json(paths.home() / "dashboard.json", {"url": f"{up.base}/?t={up.app.token}", "pid": 0, "build": build_id()})
    assert cli.main(["dashboard"]) == 0 and "already running" in capsys.readouterr().out and up.app.finished() is False


def test_the_user_deletes_a_finished_session_but_not_one_that_is_working(session, up, tmp_path):
    at_confirm(session, tmp_path)
    session.confirm_current("t")
    sid = {"ws": "demo", "id": session.id}
    session.begin_attempt("ORD-001")
    events.append(session.dir, "case_started", case="ORD-001", attempt=1)
    assert "đang chạy" in up.call("/api/delete_session", sid)[1]["error"]                         # an agent wrote a moment ago: files stay
    assert session.dir.exists()
    session.stop("dashboard")
    assert up.call("/api/delete_session", sid)[1] == {"ok": True}
    assert not session.dir.exists()
    assert session.id not in [s["session"] for s in up.call("/api/sessions")[1]["sessions"]]


def test_rerunning_a_case_needs_a_saved_script_and_a_screen(session, up, tmp_path, monkeypatch):
    at_confirm(session, tmp_path)
    session.confirm_current("t")
    body = {"ws": "demo", "id": session.id, "case": "ORD-001"}
    assert "chưa test xong" in up.call("/api/replay", body)[1]["error"]
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
    assert "chưa có script" in up.call("/api/replay", body)[1]["error"]
    case = next(c for c in up.call(f"/api/session?ws=demo&id={session.id}")[1]["cases"] if c["id"] == "ORD-001")
    assert case["can_replay"] is False


def test_replay_api_passes_pacing_to_runner_and_rejects_invalid_values(session, up, tmp_path, monkeypatch):
    from oqa import live

    at_confirm(session, tmp_path)
    session.confirm_current("test")
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
    script = live.script_file(session, "ORD-001")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("// saved", encoding="utf-8")
    launched = []
    monkeypatch.setattr(dashboard_server.subprocess, "Popen", lambda command, **kw: launched.append(command))
    body = {"ws": session.ws, "id": session.id, "case": "ORD-001", "action_delay_ms": 1200, "step_delay_ms": 2400, "video": "on"}
    assert up.call("/api/replay", body)[1]["ok"]
    command = launched[0]
    assert command[command.index("--action-delay-ms") + 1] == "1200"
    assert command[command.index("--step-delay-ms") + 1] == "2400"
    assert command[command.index("--video") + 1] == "on"
    for value in (-1, 60001, True, "1200", 1.5):
        assert up.call("/api/replay", {**body, "step_delay_ms": value})[1]["error"]
    assert up.call("/api/replay", {**body, "video": "invalid"})[1]["error"]
    assert len(launched) == 1


def test_scripts_screen_lists_scripts_with_a_result_per_environment_and_starts_a_regression(session, up, tmp_path, monkeypatch, store):
    from oqa import live, workspace

    at_confirm(session, tmp_path)
    session.confirm_current("test")
    script = live.script_file(session, "ORD-001")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("// saved", encoding="utf-8")
    config = workspace.load(session.ws)
    workspace.save(dict(config, environments=[{"name": "staging", "url": "http://stg:8069", "accounts": []}]), store, {})
    table = up.call("/api/regression?ws=demo")[1]
    assert table["environments"] == ["main", "staging"]
    (row,) = table["scripts"]
    assert (row["case_id"], row["session"], row["results"]["staging"]["status"]) == ("ORD-001", session.id, "NOT RUN")
    launched = []
    monkeypatch.setattr(dashboard_server.subprocess, "Popen", lambda command, **kw: launched.append(command))
    assert up.call("/api/regress", {"ws": "demo", "env": "staging", "case": "ORD-001", "video": "off"})[1]["ok"]
    command = launched[0]
    assert command[command.index("--env") + 1] == "staging" and command[command.index("--cases") + 1] == "ORD-001"
    assert command[command.index("--video") + 1] == "off"
    assert up.call("/api/regress", {"ws": "demo", "env": "staging", "video": "invalid"})[1]["error"]
    assert up.call("/api/regress", {"ws": "demo", "env": "prod"})[1]["error"] and len(launched) == 1


def test_a_video_you_watch_keeps_the_viewing_pauses_and_a_fast_run_does_not(session, up, tmp_path, monkeypatch, store):
    from oqa import live, workspace

    at_confirm(session, tmp_path)
    session.confirm_current("test")
    script = live.script_file(session, "ORD-001")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("// saved", encoding="utf-8")
    launched = []
    monkeypatch.setattr(dashboard_server.subprocess, "Popen", lambda command, **kw: launched.append(command))
    assert up.call("/api/regress", {"ws": "demo", "env": "main", "video": "on"})[1]["ok"]
    assert "--fast" not in launched[0] and launched[0][launched[0].index("--action-delay-ms") + 1] == str(live.DEFAULT_ACTION_DELAY_MS)
    assert up.call("/api/regress", {"ws": "demo", "env": "main", "video": "fail"})[1]["ok"]
    assert "--fast" in launched[1] and launched[1][launched[1].index("--video") + 1] == "fail"
    assert up.call("/api/regress", {"ws": "demo", "env": "main", "video": "x"})[1]["error"]


def test_the_page_has_record_buttons_and_no_leftover_of_the_removed_video_selector():
    page = (dashboard_server.PAGE).read_text(encoding="utf-8")
    assert page.count('data-record="1"') >= 4 and "Quay video tất cả" in page
    assert "videoControl" not in page and "data-video-mode" not in page            # a call to a removed function would blank the Scripts tab


def test_the_dashboard_buttons_only_leave_a_request_for_the_coordinators_sub_agents(session, up, tmp_path, monkeypatch):
    """Dispatch, hand over and generic script: the dashboard starts no process and no model; `oqa wait` tells the host, which gives the work to a sub-agent of its own."""
    from oqa import cli, live

    at_confirm(session, tmp_path)
    session.confirm_current("t")
    monkeypatch.setattr(dashboard_server.subprocess, "Popen", lambda *a, **k: pytest.fail("the dashboard must not start a process for this"))
    sid = {"ws": "demo", "id": session.id}
    assert up.call("/api/dispatch-request", {**sid, "case": "ORD-001"})[1]["ok"] is True
    path = live.script_file(session, "ORD-001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// step 1\n", encoding="utf-8")
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
    answer = up.call("/api/regenerate", {**sid, "case": "ORD-001"})[1]
    assert answer["ok"] is True and "sub-agent" in answer["message"]
    kinds = [e["kind"] for e in session.news()]
    assert "dispatch_requested" in kinds and "regenerate_requested" in kinds
    sentences = " ".join(cli._describe(e) for e in session.news())
    assert "oqa dispatch --plan" in sentences and "oqa live regenerate ORD-001 --brief" in sentences and "sub-agent" in sentences


def test_the_dashboard_shows_edits_moves_and_deletes_knowledge_and_saves_the_summary(session, up, tmp_path, ws):
    from oqa import claims, knowledge, knowledge_flow

    sid = {"ws": "demo", "id": session.id}
    assert up.call("/api/knowledge", {"ws": "demo", "op": "add", "scope": "workspace", "item": {"screen": "res.partner", "kind": "rule", "fact": "Save needs a Name"}})[1]["ok"]
    listed = up.call("/api/knowledge?ws=demo")[1]
    assert [(i["scope"], i["fact"]) for i in listed["items"]] == [("workspace", "Save needs a Name")] and "shared" in listed["scopes"]
    item = listed["items"][0]
    moved = up.call("/api/knowledge", {"ws": "demo", "op": "edit", "id": item["id"], "scope": "workspace", "item": {"fact": "Save needs a Name"}, "to": "shared"})[1]
    assert moved["scope"] == "shared"
    refused = up.call("/api/knowledge", {"ws": "demo", "op": "edit", "id": moved["id"], "scope": "shared", "item": {"fact": "password: x"}})[1]
    assert "password" in refused["error"]
    assert up.call("/api/knowledge", {"ws": "demo", "op": "delete", "id": moved["id"], "scope": "shared"})[1]["ok"]
    assert up.call("/api/knowledge?ws=demo")[1]["items"] == []
    assert up.call("/api/knowledge", {"ws": "demo", "op": "nonsense"})[0] == 400

    assert "sau khi mọi case" in up.call("/api/knowledge-review", {**sid, "op": "skip"})[1]["error"]          # not before the cases are done
    at_confirm(session, tmp_path)
    session.confirm_current("t")
    with session.update() as state:
        state["phase"] = "REPORT"
        state["results"] = {c["id"]: {"status": "PASS", "attempts": 1} for c in session.cases()["cases"]}
    claims.add_note(session, "worker", "A thing the next run should know", "ORD-001")
    knowledge_flow.digest(session)
    knowledge_flow.draft(session, [{"screen": "res.partner", "kind": "quirk", "fact": "Một điều nên nhớ", "scope": "shared"}])
    view_ = up.call(f"/api/session?ws=demo&id={session.id}")[1]
    assert view_["knowledge_review"]["status"] == "ready" and view_["knowledge_review"]["items"][0]["scope"] == "shared" and view_["knowledge_owed"]
    bad = up.call("/api/knowledge-review", {**sid, "op": "save", "items": [{"screen": "", "kind": "rule", "fact": "x"}]})[1]
    assert "error" in bad and knowledge_flow.review(session)["status"] == "ready"                                # a refused item saves nothing
    saved = up.call("/api/knowledge-review", {**sid, "op": "save", "items": [{**view_["knowledge_review"]["items"][0], "fact": "Một điều nên nhớ (đã sửa)", "scope": "workspace"}]})[1]
    assert saved["saved"]["added"] == 1
    assert [(i["scope"], i["fact"]) for i in knowledge.everything("demo")] == [("workspace", "Một điều nên nhớ (đã sửa)")]
    assert up.call(f"/api/session?ws=demo&id={session.id}")[1]["knowledge_owed"] == ""
