"""The order of authority: design (BD) > testcase > the app. A step is judged against the design when there is one, else the testcase;
a testcase that contradicts the design is reported to the user, and the test follows the design."""
import pytest

from conftest import filled
from oqa import analysis, workspace
from oqa.store import SchemaError


def design_ref(tmp_path):
    path = tmp_path / "BD-orders.txt"
    path.write_text("4.2 Name is required", encoding="utf-8")
    return {"kind": "file", "text": "BD-orders.txt", "status": "ok", "path": str(path), "note": str(path)}


def bd_step(step, ref="BD-orders.txt 4.2"):
    return dict(step, expected="Saving without a name is refused: Name is required", oracle={"source": "bd", "ref": ref, "quote": "Name is required on every record"})


def test_a_testcase_only_step_needs_a_note_on_which_design_was_looked_for(session):
    config = workspace.load("demo")
    data = filled(session.cases(), config, **{"ORD-001": {"bd_searched": ""}})
    with pytest.raises(SchemaError, match="bd_searched"):
        analysis.validate(data, session.cases(), config)


def test_a_step_must_say_where_its_expected_result_comes_from(session):
    config = workspace.load("demo")
    data = filled(session.cases(), config)
    data["cases"][0]["steps"][0]["oracle"] = {"source": "", "ref": "", "quote": ""}
    with pytest.raises(SchemaError, match="oracle.source"):
        analysis.validate(data, session.cases(), config)


def test_a_design_step_cites_a_design_reference_of_the_case_and_quotes_it(session, tmp_path):
    config = workspace.load("demo")
    data = filled(session.cases(), config)
    case = data["cases"][0]
    case["refs"] = [design_ref(tmp_path)]
    case["steps"][0] = bd_step(case["steps"][0])
    analysis.validate(data, session.cases(), config)
    case["steps"][0]["oracle"]["ref"] = "Some other document"
    with pytest.raises(SchemaError, match="not one of this case's references"):
        analysis.validate(data, session.cases(), config)
    case["steps"][0]["oracle"].update(ref="BD-orders.txt 4.2", quote="short")
    with pytest.raises(SchemaError, match="oracle.quote"):
        analysis.validate(data, session.cases(), config)


def test_a_testcase_that_contradicts_the_design_is_reported_and_the_design_wins(session, tmp_path):
    config = workspace.load("demo")
    data = filled(session.cases(), config)
    case = data["cases"][0]
    case["refs"] = [design_ref(tmp_path)]
    conflict = {"step": 1, "testcase": "Saving without a name names the record New", "bd": "Name is required", "ref": "BD-orders.txt 4.2"}
    case["testcase_conflicts"] = [conflict]
    with pytest.raises(SchemaError, match="oracle.source must be bd"):      # the step still follows the testcase: refused
        analysis.validate(data, session.cases(), config)
    case["steps"][0] = bd_step(case["steps"][0])
    analysis.validate(data, session.cases(), config)
    assert analysis.warnings(data) == [{"case": "ORD-001", **conflict}]
    doc, text = analysis.strategy(data, session.cases(), config)
    assert doc["warnings"][0]["case"] == "ORD-001" and "Name is required" in text and "BD: BD-orders.txt 4.2" in text
    assert analysis.verify_strategy(doc, data)


def test_a_strategy_without_conflicts_carries_no_warning(session):
    config = workspace.load("demo")
    data = filled(session.cases(), config)
    doc, text = analysis.strategy(data, session.cases(), config)
    assert "warnings" not in doc and "testcase" in text      # basis is shown for every step


def test_the_basis_of_a_fail_is_the_design_when_there_is_one_else_the_testcase():
    assert analysis._basis({"oracle": {"source": "bd", "ref": "BD-orders.txt 4.2"}}) == "BD: BD-orders.txt 4.2"
    assert analysis._basis({"oracle": {"source": "testcase"}}) == "testcase"
    assert analysis._basis({"expected": "older analysis without an oracle"}) == ""


# --- the user chooses: follow the BD or follow the testcase ----------------------------
def confirming(session, tmp_path):
    """A session in CONFIRM whose first case has a testcase/design conflict on step 1 (the assistant followed the design)."""
    from oqa.store import write_json

    config = workspace.load(session.ws)
    data = filled(session.cases(), config)
    case = data["cases"][0]
    case["refs"] = [design_ref(tmp_path)]
    case["steps"][0] = bd_step(case["steps"][0])
    case["testcase_conflicts"] = [{"step": 1, "testcase": "Saving without a name names the record New", "bd": "Name is required", "ref": "BD-orders.txt 4.2"}]
    write_json(session.dir / "analysis.json", data)
    session.analysis_submitted()
    session.connected({})
    doc, text = analysis.strategy(data, session.cases(), config)
    write_json(session.dir / "strategy.json", doc)
    session.strategy_ready(doc["sha"])
    return config


def test_the_strategy_cannot_be_confirmed_until_the_user_chooses(session, tmp_path):
    from oqa import view
    from oqa.state import GateError

    confirming(session, tmp_path)
    assert view.session_view(session)["conflicts_pending"] == 1
    with pytest.raises(GateError, match="chưa chọn"):
        session.confirm_current("dashboard")


def test_choosing_the_bd_keeps_the_design_and_unblocks_the_confirmation(session, tmp_path):
    from oqa import conflicts
    from oqa.store import read_json

    confirming(session, tmp_path)
    conflicts.decide(session, "ORD-001", 1, "bd", "dashboard")
    step = read_json(session.dir / "analysis.json")["cases"][0]["steps"][0]
    assert step["oracle"]["source"] == "bd" and "Name is required" in step["expected"]
    session.confirm_current("dashboard")                      # the strategy was rebuilt: what the user confirms is what runs


def test_choosing_the_testcase_makes_the_step_follow_it_and_can_be_changed_back(session, tmp_path):
    from oqa import conflicts
    from oqa.store import read_json

    confirming(session, tmp_path)
    conflicts.decide(session, "ORD-001", 1, "testcase", "dashboard")
    step = read_json(session.dir / "analysis.json")["cases"][0]["steps"][0]
    assert step["expected"] == "Saving without a name names the record New" and step["oracle"]["chosen_over"] == "BD-orders.txt 4.2"
    assert analysis._basis(step) == "testcase (user chose it over BD: BD-orders.txt 4.2)"
    assert analysis.verify_strategy(read_json(session.dir / "strategy.json"), read_json(session.dir / "analysis.json"))
    conflicts.decide(session, "ORD-001", 1, "bd", "dashboard")
    assert read_json(session.dir / "analysis.json")["cases"][0]["steps"][0]["oracle"]["source"] == "bd"


def test_a_choice_holds_only_while_the_conflict_is_unchanged(session, tmp_path):
    from oqa import conflicts
    from oqa.store import read_json

    confirming(session, tmp_path)
    conflicts.decide(session, "ORD-001", 1, "testcase", "dashboard")
    state = session.state()
    document = read_json(session.dir / "analysis.json")
    document["cases"][0]["testcase_conflicts"][0]["bd"] = "Name is required and must be unique"
    assert len(analysis.pending_conflicts(document, state["conflict_choices"])) == 1          # the wording changed: the user decides again


def test_the_assistant_cannot_decide_for_the_user_by_rewriting_the_analysis(session, tmp_path):
    config = confirming(session, tmp_path)
    from oqa.store import read_json

    forged = read_json(session.dir / "analysis.json")
    forged["cases"][0]["steps"][0]["oracle"] = {"source": "testcase", "ref": "", "quote": "", "chosen_over": "BD-orders.txt 4.2"}
    with pytest.raises(SchemaError, match="the user decides"):
        analysis.validate(forged, session.cases(), config, session.state().get("conflict_choices"))


def test_a_decision_is_refused_once_the_cases_are_approved(session, tmp_path):
    from oqa import conflicts
    from oqa.state import GateError

    confirming(session, tmp_path)
    conflicts.decide(session, "ORD-001", 1, "bd", "dashboard")
    session.confirm_current("dashboard")
    with pytest.raises(GateError, match="đã được duyệt"):
        conflicts.decide(session, "ORD-001", 1, "testcase", "dashboard")
