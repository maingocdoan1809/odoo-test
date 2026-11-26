"""Knowledge: the workspace's own and the shared one (ids, edit, move, delete), the facts a cheap agent picks for each case, and the summary of a finished run that the user edits and saves."""
import json

import pytest

from conftest import filled
from oqa import cli, dispatch, events, knowledge, knowledge_flow, report
from oqa.case_context import build
from oqa.state import GateError
from test_evidence_replay import approved


def learn(ws, fact, scope="workspace", screen="res.partner", kind="rule", **more):
    return knowledge.learn(ws["name"], screen, kind, fact, scope=scope, **more)


def test_a_fact_lives_in_the_workspace_or_is_shared_by_all_and_both_are_read(ws):
    learn(ws, "Save needs a Name")
    learn(ws, "A search is applied from the suggestion list", scope="shared", kind="quirk")
    items = knowledge.everything(ws["name"])
    assert [(i["scope"], i["fact"]) for i in items] == [("workspace", "Save needs a Name"), ("shared", "A search is applied from the suggestion list")]
    assert knowledge.file_of(ws["name"], "shared") != knowledge.file_of(ws["name"])
    assert [i["scope"] for i in knowledge.search(ws["name"], scope="shared")] == ["shared"]
    assert "shared knowledge/index.md" in knowledge.describe(items[1])
    other = knowledge.file_of("another", "shared")
    assert other == knowledge.file_of(ws["name"], "shared")                                  # the same file for every workspace


def test_a_fact_keeps_its_id_and_can_be_edited_moved_and_deleted(ws):
    learn(ws, "Save needs a Name")
    first = knowledge.load(ws["name"])[0]
    assert first["id"] == knowledge.load(ws["name"])[0]["id"] and len(first["id"]) == 8
    moved = knowledge.edit(ws["name"], "workspace", first["id"], {"fact": "Save needs a Name and a Company"}, to_scope="shared")
    assert moved["scope"] == "shared" and not knowledge.load(ws["name"], "workspace")
    now = knowledge.load(ws["name"], "shared")
    assert [i["fact"] for i in now] == ["Save needs a Name and a Company"] and now[0]["id"] == moved["id"]
    with pytest.raises(knowledge.KnowledgeError, match="password"):
        knowledge.edit(ws["name"], "shared", moved["id"], {"fact": "password: abc"})
    assert knowledge.load(ws["name"], "shared")[0]["fact"] == "Save needs a Name and a Company"        # a refused edit leaves the fact as it was
    knowledge.remove(ws["name"], "shared", moved["id"])
    assert knowledge.everything(ws["name"]) == []
    with pytest.raises(knowledge.KnowledgeError, match="no such"):
        knowledge.remove(ws["name"], "shared", moved["id"])


def test_deleting_one_fact_of_a_group_keeps_the_others(ws):
    learn(ws, "Fact one", condition="khi A")
    learn(ws, "Fact two", condition="khi A")
    one = next(i for i in knowledge.load(ws["name"]) if i["fact"] == "Fact one")
    knowledge.remove(ws["name"], "workspace", one["id"])
    assert [i["fact"] for i in knowledge.load(ws["name"])] == ["Fact two"]


def test_nothing_is_picked_from_a_workspace_that_knows_only_how_to_log_in(session, ws):
    approved(session, ws)
    learn(ws, "After signing in the page is /odoo", screen="login", kind="login")
    assert knowledge_flow.brief(session, ["ORD-001"]) is None and not knowledge_flow.needs_picks(session, ["ORD-001"])


def test_the_agent_picks_by_meaning_and_the_picked_facts_lead_the_case_packet(session, ws):
    approved(session, ws)
    learn(ws, "Saving a record needs the Company field to be filled first", screen="sale.order", condition="lưu")
    wanted = knowledge.load(ws["name"])[0]
    learn(ws, "Other thing nobody picked", screen="sale.order", kind="quirk")
    assert knowledge_flow.needs_picks(session, ["ORD-001"])
    text = knowledge_flow.brief(session, ["ORD-001", "ORD-002"])
    assert wanted["id"] in text and "ORD-001" in text and "BY MEANING" in text and "oqa knowledge pick --file" in text
    done = knowledge_flow.pick(session, {"ORD-001": [wanted["id"], "deadbeef"], "ORD-002": []})
    assert done == {"ORD-001": [wanted["id"]], "ORD-002": []}                                    # an unknown id is dropped; a case looked at with nothing needed is recorded
    assert not knowledge_flow.needs_picks(session, ["ORD-001", "ORD-002"])
    assert build(session, "ORD-001")["knowledge"][0].startswith("[screen:sale.order]") and "Company field" in build(session, "ORD-001")["knowledge"][0]
    assert any("Company field" in line for line in build(session, "ORD-001", only="knowledge")["knowledge"])


def test_plan_asks_for_the_picks_first_and_then_hands_out_the_workers(session, ws):
    approved(session, ws)
    learn(ws, "Saving needs the Company", screen="sale.order")
    first = dispatch.plan(session)
    assert first["workers"] == [] and "Known facts" in first["knowledge_brief"] and first["knowledge_cases"]
    assert not any(e["kind"] == "case_claimed" for e in events.read(session.dir))                # nothing was claimed yet
    knowledge_flow.pick(session, {c: [] for c in first["knowledge_cases"]})
    second = dispatch.plan(session)
    assert second["workers"] and not second.get("knowledge_brief")
    assert dispatch.plan(session, skip_knowledge=True)["workers"] == []                              # what is held is not handed out twice


def finished(session, ws):
    approved(session, ws)
    with session.update() as state:
        state["phase"] = "REPORT"
        state["results"] = {c["id"]: {"status": "PASS", "attempts": 1} for c in session.cases()["cases"]}


def test_a_run_with_nothing_to_learn_closes_without_a_summary(session, ws):
    finished(session, ws)
    assert knowledge_flow.pending(session) == ""
    assert knowledge_flow.digest(session) is None and knowledge_flow.review(session)["status"] == "empty"
    assert report.finish(session, ws)["counts"] and session.state()["phase"] == "DONE"


def test_cases_added_after_report_get_their_own_knowledge_review(session, ws):
    from oqa import claims

    session.analysis_submitted(document=filled(session.cases(), ws))
    session.connected({})
    session.select_cases(["ORD-001"])
    session.confirm_current("test")
    session.begin_attempt("ORD-001")
    claims.add_note(session, "worker", "First workaround", "ORD-001")
    session.finish_case("ORD-001", "PASS")
    knowledge_flow.skip(session)
    report.finish(session, ws)
    session.add_cases(["ORD-002"])
    session.begin_attempt("ORD-002")
    claims.add_note(session, "worker", "New workaround from the added case", "ORD-002")
    session.finish_case("ORD-002", "PASS")

    assert "knowledge digest" in knowledge_flow.pending(session)
    brief = knowledge_flow.digest(session)
    assert "New workaround from the added case" in brief and "First workaround" not in brief


def test_a_run_that_taught_something_stays_open_until_the_user_saves_the_summary(session, ws):
    finished(session, ws)
    from oqa import claims
    claims.add_note(session, "worker", "A search must be applied from the suggestion list, Enter alone does not apply it", "ORD-001")
    assert "oqa knowledge digest" in knowledge_flow.pending(session)
    result = report.finish(session, ws)
    assert result["knowledge_pending"] and session.state()["phase"] == "REPORT"                    # the report is written, the session is not closed
    brief = knowledge_flow.digest(session)
    assert "suggestion list" in brief and "oqa knowledge draft --file" in brief and "Reply with one line" in brief
    assert knowledge_flow.review(session)["status"] == "drafting" and "being drafted" in knowledge_flow.pending(session)
    with pytest.raises(knowledge.KnowledgeError, match="password"):
        knowledge_flow.draft(session, [{"screen": "x", "kind": "rule", "fact": "token: abc"}])
    drafted = knowledge_flow.draft(session, [{"screen": "res.partner", "kind": "quirk", "fact": "Một tìm kiếm phải chọn từ danh sách gợi ý", "scope": "shared", "case": "ORD-001"},
                                              {"screen": "res.partner", "kind": "rule", "fact": "Contact cần Name để lưu"}])
    assert drafted == {"status": "ready", "count": 2} and "has not saved or skipped" in knowledge_flow.pending(session)
    assert report.finish(session, ws)["knowledge_pending"]
    edited = [{**i, "keep": True} for i in knowledge_flow.review(session)["items"]]
    edited[1]["fact"] = "Contact cần Name để lưu được"
    edited[1]["scope"] = "workspace"
    counts = knowledge_flow.save(session, edited)
    assert counts["added"] == 2 and knowledge_flow.pending(session) == ""
    assert {(i["scope"], i["fact"]) for i in knowledge.everything(ws["name"])} == {("shared", "Một tìm kiếm phải chọn từ danh sách gợi ý"), ("workspace", "Contact cần Name để lưu được")}
    assert "session " + session.id in knowledge.everything(ws["name"])[0]["ref"]
    assert report.finish(session, ws) and session.state()["phase"] == "DONE"
    assert [e["kind"] for e in events.read(session.dir) if e["kind"].startswith("knowledge_")] == ["knowledge_drafted", "knowledge_saved"]


def test_the_user_can_skip_the_summary_and_a_deleted_item_is_not_saved(session, ws):
    finished(session, ws)
    from oqa import claims
    claims.add_note(session, "worker", "Something worth a thought", "ORD-001")
    knowledge_flow.digest(session)
    knowledge_flow.draft(session, [{"screen": "a", "kind": "rule", "fact": "kept"}, {"screen": "a", "kind": "rule", "fact": "dropped"}])
    knowledge_flow.save(session, [{"screen": "a", "kind": "rule", "fact": "kept"}, {"screen": "a", "kind": "rule", "fact": "dropped", "keep": False}])
    assert [i["fact"] for i in knowledge.everything(ws["name"])] == ["kept"]
    knowledge_flow.skip(session)
    assert knowledge_flow.review(session)["status"] == "skipped" and knowledge_flow.pending(session) == ""


def test_the_commands_an_agent_runs_drive_the_whole_summary(session, ws, tmp_path, capsys):
    finished(session, ws)
    from oqa import claims
    claims.add_note(session, "worker", "After Save the form has no search box", "ORD-001")
    where = ["--ws", session.ws, "--session", session.id]
    assert cli.main(["knowledge", "status", *where]) == 0 and "oqa knowledge digest" in capsys.readouterr().out
    assert cli.main(["knowledge", "digest", *where]) == 0 and "oqa knowledge draft --file" in capsys.readouterr().out
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps([{"screen": "res.partner", "kind": "quirk", "fact": "Sau Save form không có ô tìm kiếm", "scope": "workspace"}]), encoding="utf-8")
    assert cli.main(["knowledge", "draft", "--file", str(draft), *where]) == 0 and "Draft saved: 1" in capsys.readouterr().out
    assert cli.main(["report", "--no-open", *where]) == 0 and "stays in REPORT" in capsys.readouterr().out
    assert cli.main(["knowledge", "learn", "--screen", "res.partner", "--kind", "rule", "--fact", "Shared rule", "--scope", "shared", *where]) == 0
    assert "shared" in capsys.readouterr().out
    assert cli.main(["knowledge", "search", "--scope-filter", "shared", "--ws", session.ws]) == 0 and "Shared rule" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert cli.main(["knowledge", "pick", "--file", str(bad), *where]) == 2
