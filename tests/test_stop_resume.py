"""The user's Stop ends the session's work (workers, Playwright daemons, browsers, claims, open runs), and Resume queues the cases again."""
import subprocess
import sys
import time

import pytest

from oqa import claims, cli, events, halt, view
from test_dashboard import Up
from test_flow import executing

IDLE = "import time; time.sleep(120)"


def spawn(name):
    """A stand-in for a Playwright daemon: a process whose command line holds the browser name."""
    return subprocess.Popen([sys.executable, "-c", IDLE, name])


def alive(process):
    return process.poll() is None


@pytest.fixture
def processes():
    started = []
    yield started
    for process in started:
        if alive(process):
            process.kill()
        process.wait()


def running_case(session, case, attempt=1, worker="w1"):
    session.begin_attempt(case)
    events.append(session.dir, "case_started", case=case, attempt=attempt)
    claims.assign(session, case, worker)


def test_stop_ends_only_this_sessions_processes_and_gives_every_claim_back(session, tmp_path, processes):
    executing(session, tmp_path)
    running_case(session, "ORD-001")
    running_case(session, "ORD-002", worker="w2")
    mine = [spawn(f"oqa-{session.id}-ORD-001-aaaa"), spawn(f"--mcp-config={session.dir}/logs/dispatch-2-1/tools.json")]
    other_session = spawn("oqa-20200101-000000-dead-ORD-001-bbbb")      # another session's browser
    users = spawn("chromium-the-user-opened")                          # the user's own process
    processes.extend(mine + [other_session, users])

    done = session.stop("dashboard")

    for process in mine:
        process.wait(timeout=10)
    assert not any(alive(p) for p in mine) and alive(other_session) and alive(users)
    assert done["killed"] >= 2 and sorted(done["closed"]) == ["ORD-001", "ORD-002"]
    assert claims.table(session) == {} and not (session.dir / "claims.json").read_text(encoding="utf-8").strip("{} \n")
    assert halt.session_processes(session) == []


def test_every_open_run_is_recorded_as_stopped_and_the_dashboard_shows_no_running_case(session, tmp_path):
    executing(session, tmp_path)
    running_case(session, "ORD-001")
    running_case(session, "ORD-002", worker="w2")
    up = Up()
    before = {c["id"]: c["live_status"] for c in up.call(f"/api/session?ws=demo&id={session.id}")[1]["cases"]}
    assert before["ORD-001"] == "RUNNING"

    up.call("/api/stop", {"ws": "demo", "id": session.id})

    finished = [e for e in events.read(session.dir) if e["kind"] == "attempt_finished"]
    assert sorted((e["case"], e["status"]) for e in finished) == [("ORD-001", "STOPPED"), ("ORD-002", "STOPPED")]
    shown = up.call(f"/api/session?ws=demo&id={session.id}")[1]
    live_status = {c["id"]: c["live_status"] for c in shown["cases"]}
    assert live_status["ORD-001"] == live_status["ORD-002"] == "STOPPED"
    assert not {"RUNNING", "REPAIRING", "EXPLORING"} & set(live_status.values())
    assert shown["workers"]["active"] == 0


def test_a_session_stopped_by_an_older_version_never_shows_running_or_repairing(session, tmp_path):
    executing(session, tmp_path)
    running_case(session, "ORD-001")
    events.append(session.dir, "case_started", case="ORD-002", attempt=1)
    events.append(session.dir, "attempt_finished", case="ORD-002", attempt=1, status="FAIL", reason="x")
    with session.update() as state:
        state["stopped"] = {"by": "dashboard", "at": "2026-11-23 14:42:00"}          # the flag only: nothing was closed
    shown = {c["id"]: c["live_status"] for c in view.case_records(session)}
    assert shown["ORD-001"] == shown["ORD-002"] == "STOPPED"


def test_resume_queues_the_stopped_cases_again_and_keeps_their_attempts(session, tmp_path):
    executing(session, tmp_path)
    running_case(session, "ORD-001")
    session.stop("dashboard")
    assert session.state()["attempts"] == {"ORD-001": 1} and session.state()["runs"] == {"ORD-001": 1}

    session.resume("dashboard")

    assert "ORD-001" in claims.free_cases(session)
    assert session.state()["attempts"] == {"ORD-001": 1} and session.state()["runs"] == {"ORD-001": 1}      # nothing lost
    assert session.begin_attempt("ORD-001") == 2                                                           # a new run number, never the stopped one


def test_resume_closes_the_runs_an_older_stop_left_open(session, tmp_path):
    executing(session, tmp_path)
    running_case(session, "ORD-001")
    with session.update() as state:
        state["stopped"] = {"by": "dashboard", "at": "2026-11-23 14:42:00"}
    session.resume("dashboard")
    assert [e["status"] for e in events.read(session.dir) if e["kind"] == "attempt_finished"] == ["STOPPED"]
    claims.release(session, "ORD-001", missing_ok=True)
    assert "ORD-001" in claims.free_cases(session)


def test_the_repair_command_the_status_message_points_to_exists(session, tmp_path, capsys):
    executing(session, tmp_path)
    code = cli.main(["repair", "ORD-001", "--cause", "the save button was hidden", "--evidence", "the picture of step 3", "--change", "scroll first",
                     "--ws", "demo", "--session", session.id])
    shown = capsys.readouterr()
    assert code == 2 and "no failed run waiting for a repair" in shown.err + shown.out
