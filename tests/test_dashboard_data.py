"""The data the dashboard gets for a session: worker load, the last replay video of a case, the saved script served for reading and download."""
import pytest

from oqa import claims, events, live, replays, view
from oqa.state import Session
from test_dashboard import Up
from test_flow import executing


def seed(session, tmp_path):
    """ORD-001 running on worker-1 with one step recorded, ORD-002 passed with a saved script and a replay video, ORD-003 not started."""
    executing(session, tmp_path)
    evidence = session.dir / "evidence" / "ORD-001"
    evidence.mkdir(parents=True)
    (evidence / "s1.png").write_bytes(b"\x89PNG\r\n")
    session.begin_attempt("ORD-001")
    events.append(session.dir, "case_started", case="ORD-001", attempt=1)
    events.append(session.dir, "step", case="ORD-001", attempt=1, n=1, status="PASS", caption="Open Sales", expected="ok", actual="Sales is open",
                  images=[{"raw": "evidence/ORD-001/s1.png", "annotated": "evidence/ORD-001/s1.png"}])
    claims.assign(session, "ORD-001", "worker-1")
    session.begin_attempt("ORD-002")
    events.append(session.dir, "case_started", case="ORD-002", attempt=1)
    session.finish_case("ORD-002", "PASS")
    script = live.script_file(session, "ORD-002")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("// step 1\nawait page.goto('/odoo');\n", encoding="utf-8")
    video = live.video_file(session.ws, "ORD-002", "main", "r1")
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\x1aE\xdf\xa3webm")
    replays.record(session, "ORD-002", "PASS", env="main", run_id="r1", video=str(video))
    return video


@pytest.fixture
def up():
    return Up()


def test_the_session_view_counts_workers_and_the_list_reports_stopped_sessions(session, tmp_path, up):
    seed(session, tmp_path)
    shown = up.call(f"/api/session?ws=demo&id={session.id}")[1]
    assert shown["workers"] == {"active": 1, "limit": claims.MAX_WORKERS, "cases": ["ORD-001"]}
    by_id = {c["id"]: c for c in shown["cases"]}
    assert by_id["ORD-002"]["replay_video"]["status"] == "PASS" and by_id["ORD-002"]["script_at"] > 0
    assert by_id["ORD-003"]["replay_video"] is None and by_id["ORD-003"]["script_at"] == 0
    listed = up.call("/api/sessions")[1]["sessions"][0]
    assert listed["stopped"] is False and listed["workers"] == 1
    session.stop("dashboard")
    assert up.call("/api/sessions")[1]["sessions"][0]["stopped"] is True


def test_a_later_replay_without_a_video_does_not_hide_the_earlier_video(session, tmp_path):
    video = seed(session, tmp_path)
    replays.record(session, "ORD-002", "FAIL", env="main", run_id="r2", reason="timeout")
    record = next(c for c in view.case_records(session) if c["id"] == "ORD-002")
    assert record["replay_status"] == "FAIL" and record["replay_video"]["video"] == str(video)


def test_the_saved_script_is_served_for_reading_and_for_download_and_only_for_cases_of_the_session(session, tmp_path, up):
    seed(session, tmp_path)
    status, body = up.call(f"/api/script?ws=demo&id={session.id}&case=ORD-002")
    assert status == 200 and b"page.goto" in body
    request = __import__("urllib.request", fromlist=["x"]).urlopen(f"{up.base}/api/script?ws=demo&id={session.id}&case=ORD-002&download=1&t={up.app.token}", timeout=5)
    assert "ORD-002.js" in request.headers["Content-Disposition"] and request.headers["Content-Type"].startswith("text/javascript")
    assert up.call(f"/api/script?ws=demo&id={session.id}&case=ORD-003")[0] == 404        # no script yet
    assert up.call(f"/api/script?ws=demo&id={session.id}&case=..%2F..%2Fx")[0] == 404     # not a case of the session
    assert up.call(f"/api/script?ws=demo&id={session.id}&case=ORD-002", token="wrong")[0] == 403


def test_a_dashboard_request_is_one_note_that_takes_no_slot_and_the_coordinator_answers_it(session, tmp_path, up):
    from oqa import dispatch_requests

    seed(session, tmp_path)
    session.finish_case("ORD-001", "PASS")                                              # ORD-003 continues from ORD-001
    body = {"ws": "demo", "id": session.id, "case": "ORD-003"}
    first = up.call("/api/dispatch-request", body)[1]
    again = up.call("/api/dispatch-request", body)[1]                                   # a double click
    assert first["created"] is True and again["created"] is False and first["request"]["kind"] == "dispatch"
    assert dispatch_requests.slots(session) == (0, claims.MAX_WORKERS)                  # asking took no slot
    shown = {c["id"]: c for c in up.call(f"/api/session?ws=demo&id={session.id}")[1]["cases"]}
    assert shown["ORD-003"]["request"]["status"] == "pending"
    assert claims.free_cases(session) == ["ORD-003"] and dispatch_requests.why_not_free(session, "ORD-003") == ""
    assert up.call("/api/dispatch-request", {**body, "case": "ORD-002"})[1]["error"]    # it has a result
    dispatch_requests.resolve(session, "ORD-003", "accepted", worker="dispatch-3-1")
    assert [e for e in events.read(session.dir) if e["kind"] == "dispatch_resolved"][-1]["worker"] == "dispatch-3-1"


def test_requests_are_refused_when_all_five_slots_are_taken_even_after_the_user_asked(session, tmp_path, up):
    import time

    from oqa import dispatch_requests
    from oqa.store import write_json

    seed(session, tmp_path)
    session.finish_case("ORD-001", "PASS")
    five = {f"X{i}": {"agent": f"w{i}", "at": time.time()} for i in range(5)}
    write_json(session.dir / "claims.json", five)
    body = {"ws": "demo", "id": session.id, "case": "ORD-003"}
    assert "slots are occupied" in up.call("/api/dispatch-request", body)[1]["error"]
    assert dispatch_requests.load(session) == {}                                         # nothing was stored
    write_json(session.dir / "claims.json", {})
    assert up.call("/api/dispatch-request", body)[1]["ok"]                               # a slot was free when it was asked...
    write_json(session.dir / "claims.json", five)                                        # ...and is full again when the coordinator looks
    assert dispatch_requests.why_not_free(session, "ORD-003").startswith("wait: all 5 worker slots")
    session.stop("dashboard")
    assert "stopped" in up.call("/api/dispatch-request", {**body, "case": "ORD-003"})[1]["error"]


def test_the_case_detail_offers_the_request_and_shows_its_answer(session, tmp_path):
    import queue
    import threading

    from oqa import browser, dashboard_server, dispatch_requests

    seed(session, tmp_path)
    app = dashboard_server.Dashboard((session.ws, session.id), timeout=60)
    urls = queue.Queue()
    threading.Thread(target=lambda: app.serve(urls.put, open_browser=False, forever=True), daemon=True).start()
    with browser.launch(True) as new_page:
        _, page = new_page()
        page.goto(urls.get(timeout=5))
        page.click("tr.case[data-id='ORD-003']")
        page.get_by_role("button", name="Yêu cầu điều phối", exact=True).dblclick()       # a double click is one request
        page.get_by_text("Đã gửi yêu cầu").wait_for()
        assert len(dispatch_requests.load(session)) == 1 and page.get_by_role("button", name="Yêu cầu điều phối").count() == 0
        dispatch_requests.resolve(session, "ORD-003", "waiting", note="it continues from ORD-001, which has not passed yet.")
        page.get_by_text("Đang chờ: it continues from ORD-001").wait_for(timeout=8000)
        dispatch_requests.note_failure(session, "ORD-003", "Worker timed out while executing the case.")
        page.get_by_text("Lỗi worker").wait_for(timeout=8000)
        assert "chưa phải kết luận" in page.inner_text("#detail")
