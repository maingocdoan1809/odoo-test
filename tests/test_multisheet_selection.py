import hashlib

import openpyxl
import pytest

from oqa import intake, report, view, workspace
from oqa.state import Session


def test_selected_regions_preserve_sheet_identity_merged_values_and_writeback(ws, tmp_path, monkeypatch):
    path = tmp_path / "original.xlsx"
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name in ("New", "Update"):
        sheet = book.create_sheet(name)
        sheet.append(["No.", "Test Case Name", "Steps", "Expected Result", "Result"])
        sheet.append([19, "first", "Open", "Rejected", "OLD"])
        sheet.append([23, "second", "Save", None, "OLD"])
        sheet.merge_cells("D2:D3")
    book.save(path)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = intake.build(path, selection="New!A3:R3,Update!A2:R2")
    assert [(c["id"], c["sheet"], c["row"], c["expected"]) for c in doc["cases"]] == [
        ("New__23", "New", 3, "Rejected"), ("Update__19", "Update", 2, "Rejected")]
    session = Session.create("demo", path, doc, channel="path")
    records = [dict(record, status="FAIL", reason="observed mismatch") for record in view.case_records(session)]
    monkeypatch.setattr(report, "write_case", lambda *a: None)
    monkeypatch.setattr(report, "write_summary", lambda *a: None)
    result = report.write_back(session, workspace.load("demo"), records, report.LABELS["en"])
    output = openpyxl.load_workbook(result["path"])
    assert output["New"]["E3"].value == output["Update"]["E2"].value == "FAIL"
    assert output["New"]["E2"].value == output["Update"]["E3"].value == "OLD"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize("previous_status", ["PASS", "FAIL"])
def test_recover_incorrect_worker_result_retains_attempt_and_allows_one_retry(session, ws, previous_status):
    from conftest import filled
    from oqa import events, live
    from oqa.store import write_json
    from oqa.state import GateError
    import pytest

    session.analysis_submitted(document=filled(session.cases(), ws))
    session.connected({})
    session.strategy_ready("approved")
    session.confirm("test", "approved")
    write_json(session.dir / "strategy.json", {"order": [c["id"] for c in session.cases()["cases"]]})
    attempt = session.begin_attempt("ORD-001")
    events.append(session.dir, "case_started", case="ORD-001", attempt=attempt)
    events.append(session.dir, "attempt_finished", case="ORD-001", attempt=attempt, status=previous_status, reason="incorrect interpretation")
    session.finish_case("ORD-001", previous_status, "incorrect interpretation")
    live.recover(session, "ORD-001", "Message reference confirms the observed translation matched the expectation")
    assert "ORD-001" not in session.state()["results"]
    assert session.begin_attempt("ORD-001") == 2
    assert any(e["kind"] == "attempt_finished" and e["status"] == previous_status for e in events.read(session.dir))
    session.finish_case("ORD-001", "BLOCKED", "second attempt stopped")
    with pytest.raises(GateError, match="one recovery"):
        live.recover(session, "ORD-001", "Trying a third time is outside the recovery budget")
