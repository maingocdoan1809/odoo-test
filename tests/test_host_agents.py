"""The host's own agents do the cases: oqa hands out the work and checks what comes back, and spawns no CLI."""
import pytest

from oqa import claims, dispatch, events, live
from oqa.state import GateError
from test_evidence_replay import approved


def test_plan_claims_the_free_cases_and_writes_one_brief_each(session, ws, monkeypatch):
    approved(session, ws)
    result = dispatch.plan(session)
    assert result["workers"] and len({w["case"] for w in result["workers"]}) == len(result["workers"])
    first = result["workers"][0]
    assert first["agent"].startswith("host-") and first["case"] in claims.table(session)
    brief = first["brief"]
    assert f"{dispatch.sys.executable} {dispatch.ENTRY}" in brief and f"--ws {session.ws} --session {session.id}" in brief and f"oqa live start {first['case']}" in brief
    assert "oqa pgadmin shot" in brief and "oqa fixture key" in brief and "Vietnamese" in brief
    again = dispatch.plan(session)                                         # what is held is not handed out twice
    assert not {w["case"] for w in again["workers"]} & {w["case"] for w in result["workers"]}


def test_plan_respects_the_limit(session, ws):
    approved(session, ws)
    assert len(dispatch.plan(session, limit=1)["workers"]) == 1


def test_plan_needs_the_execute_phase(session, ws):
    with pytest.raises(GateError):
        dispatch.plan(session)


def test_settle_says_when_there_is_no_result(session, ws):
    approved(session, ws)
    assert "no result yet" in dispatch.settle(session, "ORD-001")


def test_settle_reopens_a_conclusion_that_blames_leftover_data(session, ws, monkeypatch):
    approved(session, ws)
    recovered = []
    monkeypatch.setattr(live, "recover", lambda s, case, reason: recovered.append((case, reason)))
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "BLOCKED", "reason": "A record from an earlier run already exists", "category": "precondition"}
    text = dispatch.settle(session, "ORD-001")
    assert "looks environmental" in text and recovered and recovered[0][0] == "ORD-001"


def test_settle_keeps_a_real_failure_and_leaves_the_judgement_to_the_coordinator(session, ws, monkeypatch):
    approved(session, ws)
    monkeypatch.setattr(live, "recover", lambda *a: pytest.fail("a real failure must not be reopened"))
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "FAIL", "reason": "Quantity -1 was accepted", "category": "application"}
    assert "Judge it yourself" in dispatch.settle(session, "ORD-001")


def test_the_brief_tells_an_agent_the_exact_commands(session, ws):
    approved(session, ws)
    brief = dispatch.plan(session, limit=1)["workers"][0]["brief"]
    assert "oqa fixture key --case" in brief and "oqa pgadmin shot" in brief and "--verified-code" in brief
    assert brief.index("oqa pgadmin shot") < brief.index("oqa live step ", brief.index("oqa pgadmin shot"))      # the picture comes before the step it belongs to


def test_settle_reopens_a_pass_whose_script_does_not_replay_even_after_the_session_reached_report(session, ws, monkeypatch):
    approved(session, ws)
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "reason": "ok", "category": ""}
        state["runs"] = {"ORD-001": 1}
        state["attempts"] = {"ORD-001": 1}
        session._enter(state, "REPORT", "every case has a result")
    live.script_file(session, "ORD-001").parent.mkdir(parents=True, exist_ok=True)
    live.script_file(session, "ORD-001").write_text("// step 1\n", encoding="utf-8")
    monkeypatch.setattr(dispatch, "replay_check", lambda *a: "Step 3 timed out")
    text = dispatch.settle(session, "ORD-001", reopen=True)
    assert "does not replay" in text and "reopened once" in text
    state = session.state()
    assert state["phase"] == "EXECUTE" and "ORD-001" not in state["results"]


def _passed_with_a_script(session, ws):
    approved(session, ws)
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "reason": "ok", "category": ""}
        state["runs"] = {"ORD-001": 1}
        state["attempts"] = {"ORD-001": 1}
        session._enter(state, "REPORT", "every case has a result")
    live.script_file(session, "ORD-001").parent.mkdir(parents=True, exist_ok=True)
    live.script_file(session, "ORD-001").write_text("// step 1\n", encoding="utf-8")


def test_a_script_that_does_not_replay_is_repaired_without_running_the_case_again(session, ws, monkeypatch):
    _passed_with_a_script(session, ws)
    monkeypatch.setattr(dispatch, "replay_check", lambda *a: "Step 1 in ORD-001.spec.js: waitForFunction Timeout 8000ms exceeded")
    text = dispatch.settle(session, "ORD-001")                              # default: the live result is kept
    assert "oqa dispatch --repair ORD-001" in text and "stay as they are" in text
    state = session.state()
    assert state["phase"] == "REPORT" and state["results"]["ORD-001"]["status"] == "PASS"
    brief = dispatch.repair_brief(session, "ORD-001", "Step 1 timed out")
    assert "do NOT run the case again" in brief and "oqa live script ORD-001 --script" in brief and "oqa live replay ORD-001" in brief
    assert f"--ws {session.ws} --session {session.id}" in brief and "Step 1 timed out" in brief


def test_a_workbook_is_written_even_when_a_text_has_terminal_colour_codes(tmp_path):
    from openpyxl import Workbook

    from oqa import report

    record = {"reason": "waiting for locator('article')\x1b[2m call log \x07 done", "steps": [{"actual": "ok\x1b[22m"}]}
    clean = report.scrub(record)
    book = Workbook()
    book.active["A1"], book.active["A2"] = clean["reason"], clean["steps"][0]["actual"]
    book.save(tmp_path / "x.xlsx")
    assert clean["reason"] == "waiting for locator('article') call log  done" and clean["steps"][0]["actual"] == "ok"
