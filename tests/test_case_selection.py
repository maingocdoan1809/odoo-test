"""The user ticks the cases of a run (all ticked by default) and can run the ones left out later, in the same session."""
import pytest

from conftest import filled
from oqa import analysis, cli, events, view
from oqa.state import GateError
from oqa.store import read_json, write_json


def at_confirm(session, ws):
    data = filled(session.cases(), ws)
    session.analysis_submitted(document=data)
    session.connected({})
    strategy, text = analysis.strategy(data, session.cases(), ws)
    write_json(session.dir / "strategy.json", strategy)
    (session.dir / "strategy.md").write_text(text, encoding="utf-8")
    session.strategy_ready(strategy["sha"])
    return data


def test_everything_is_ticked_until_the_user_unticks_something(session, ws):
    at_confirm(session, ws)
    selection = view.session_view(session)["selection"]
    assert selection["can_select"] and selection["selected"] == [c["id"] for c in selection["ready"]] and selection["deferred"] == []


def test_selecting_rebuilds_the_strategy_and_only_the_selected_cases_run(session, ws):
    at_confirm(session, ws)
    result = session.select_cases(["ORD-001"])
    assert result["selected"] == ["ORD-001"] and set(result["deferred"]) == {"ORD-002", "ORD-003"}
    strategy = read_json(session.dir / "strategy.json")
    assert strategy["order"] == ["ORD-001"] and "ORD-002" in strategy["deferred"]
    assert "ORD-002" in (session.dir / "strategy.md").read_text(encoding="utf-8")
    assert session.state()["strategy_sha"] == strategy["sha"]
    session.confirm_current("test")                                    # the digest the user approves is the rebuilt one
    assert session.state()["phase"] == "EXECUTE"
    records = {r["id"]: r["status"] for r in view.case_records(session)}
    assert records["ORD-001"] != "DEFERRED" and records["ORD-002"] == "DEFERRED"


def test_a_case_brings_the_cases_it_depends_on(session, ws):
    data = at_confirm(session, ws)
    for case in data["cases"]:
        if case["id"] == "ORD-003":
            case["depends_on"] = ["ORD-001"]
    session.analysis_submitted(document=data)
    session.connected({}) if session.state()["phase"] == "CONNECT" else None
    strategy, text = analysis.strategy(data, session.cases(), ws)
    write_json(session.dir / "strategy.json", strategy)
    session.strategy_ready(strategy["sha"])
    result = session.select_cases(["ORD-003"])
    assert result["selected"][:2] == ["ORD-001", "ORD-003"] and result["added"] == ["ORD-001"]


def test_failed_dependencies_close_the_queue_and_reopen_transitively(session, ws):
    from oqa import claims

    data = filled(session.cases(), ws, **{"ORD-002": {"depends_on": ["ORD-001"]}, "ORD-003": {"depends_on": ["ORD-002"]}})
    session.analysis_submitted(document=data)
    session.connected({})
    session.rebuild_strategy()
    session.confirm_current("test")
    session.begin_attempt("ORD-001")
    session.finish_case("ORD-001", "FAIL", "Root prerequisite failed")

    state = session.state()
    assert state["phase"] == "REPORT"
    assert state["results"]["ORD-002"]["blocked_by"] == {"ORD-001": "FAIL"}
    assert state["results"]["ORD-003"]["blocked_by"] == {"ORD-002": "NOT EXECUTED"}

    session.hint("ORD-001", "The fixture is now available")
    assert session.state()["phase"] == "EXECUTE" and session.state()["results"] == {}
    assert claims.free_cases(session) == ["ORD-001"]
    session.begin_attempt("ORD-001")
    session.finish_case("ORD-001", "PASS")
    assert claims.free_cases(session) == ["ORD-002"]


def test_the_choice_cannot_be_empty_or_unknown_or_late(session, ws):
    at_confirm(session, ws)
    with pytest.raises(GateError, match="at least one"):
        session.select_cases([])
    with pytest.raises(GateError, match="Not a case"):
        session.select_cases(["NOPE-1"])
    session.confirm_current("test")
    with pytest.raises(GateError, match="before the strategy is approved"):
        session.select_cases(["ORD-001"])


def test_left_out_cases_run_later_in_the_same_session(session, ws):
    at_confirm(session, ws)
    session.select_cases(["ORD-001"])
    session.confirm_current("test")
    with session.update() as state:                                      # ORD-001 ran and passed: the session reaches REPORT
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
        state["attempts"]["ORD-001"] = 1
        session._enter(state, "REPORT", "every selected case has a result")
    assert view.session_view(session)["selection"]["can_add"]
    with pytest.raises(GateError, match="Not among the cases left out"):
        session.add_cases(["ORD-001"])
    sid, before = session.id, session.state()["created"]
    result = session.add_cases(["ORD-002"])
    state = session.state()
    assert result["added"] == ["ORD-002"] and state["phase"] == "EXECUTE" and session.id == sid and state["created"] == before
    strategy = read_json(session.dir / "strategy.json")
    assert strategy["order"] == ["ORD-001", "ORD-002"] and strategy["deferred"] == ["ORD-003"]
    assert state["results"]["ORD-001"]["status"] == "PASS"               # what already ran is untouched
    assert state["confirmation"]["strategy_sha"] == strategy["sha"] and state["confirmation"]["added"] == ["ORD-002"]
    assert any(e["kind"] == "cases_added" and e["cases"] == ["ORD-002"] for e in events.read(session.dir))
    from oqa import claims
    assert "ORD-002" in claims.free_cases(session)                       # workers can take it at once


def test_the_session_reaches_report_again_when_the_added_cases_finish(session, ws):
    at_confirm(session, ws)
    session.select_cases(["ORD-001"])
    session.confirm_current("test")
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
        state["attempts"]["ORD-001"] = 1
        session._enter(state, "REPORT", "done")
    session.add_cases(["ORD-002"])
    with session.update() as state:
        state["attempts"]["ORD-002"] = 1
    session.finish_case("ORD-002", "PASS")
    assert session.state()["phase"] == "REPORT"


def test_cases_can_be_added_after_the_session_was_done(session, ws):
    at_confirm(session, ws)
    session.select_cases(["ORD-001"])
    session.confirm_current("test")
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
        session._enter(state, "DONE", "report written")
    assert session.add_cases(["ORD-002", "ORD-003"])["added"] == ["ORD-002", "ORD-003"]
    assert session.state()["phase"] == "EXECUTE"


def test_the_assistant_cannot_add_cases_and_is_told_when_the_user_does(session, ws):
    at_confirm(session, ws)
    assert "cases_added" in events.USER_KINDS and "cases_selected" in events.USER_KINDS
    args = type("A", (), {"ws": session.ws, "session": session.id, "cases": "ORD-002"})()
    with pytest.raises(GateError, match="Only the user"):
        cli.cmd_run_more(args, is_tty=False)
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "hooks"))
    import guard
    assert guard.verdict({"tool_name": "Bash", "tool_input": {"command": "python oqa.py run-more --cases ORD-002"}})
