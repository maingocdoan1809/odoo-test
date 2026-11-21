"""Reusable UI fixtures: run-specific keys, read-only references, relative dates, tracking and cleanup."""
import json

import pytest

from oqa import events, fixtures, live, record_names, workspace
from oqa.state import GateError
from oqa.store import write_json
from test_evidence_replay import approved

FORM = {"model": "license.user", "fields": [
    {"name": "memo", "type": "text", "readonly": False},
    {"name": "user_id", "type": "many2one", "relation": "res.users", "readonly": False},
    {"name": "license_ledger_id", "type": "many2one", "relation": "license.ledger", "readonly": False},
    {"name": "last_usage_date", "type": "date", "readonly": False},
    {"name": "state", "type": "selection", "readonly": False},
    {"name": "active", "type": "boolean", "readonly": False},
    {"name": "management_no", "type": "char", "readonly": True},
]}


def started(session, ws, monkeypatch):
    approved(session, ws)
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "_run", lambda *a, **kw: "")
    live.start(session, "ORD-001")


def test_every_run_gets_its_own_key_and_a_replay_swaps_only_the_prefix(session, ws):
    key = fixtures.run_key(session, "ORD-001", "ledger")
    assert key == f"AUTO_TEST_create-order_ORD-001_{session.id}_ledger"
    assert fixtures.run_key(session, "License Ledger Detail (New)__24") == f"AUTO_TEST_license-ledger-detail-new-24_License_Ledger_Detail_New_24_{session.id}_ban-ghi"
    sources = record_names.replay_sources(session, "ORD-001", ws["test_prefix"])
    fresh = record_names.rewriter(sources, "AUTO_TEST_create-order_rabc_")
    assert fresh(f"{key}, ZZ_AUTOTEST_order") == "AUTO_TEST_create-order_rabc_ledger, AUTO_TEST_create-order_rabc_order"


def test_fixture_uses_the_authored_vietnamese_business_name_and_custom_workspace_prefix(session, ws):
    write_json(session.dir / "analysis.json", {"cases": [{"id": "ORD-001", "title": "Duyệt phiếu điều chuyển", "fixture_name": "Phiếu điều chuyển"}]})
    write_json(workspace.workspace_dir(session.ws) / "workspace.json", {**ws, "test_prefix": "QA_"})
    key = fixtures.run_key(session, "ORD-001", "Mặt hàng đối chứng")
    assert key == f"QA_phieu-dieu-chuyen_ORD-001_{session.id}_mat-hang-doi-chung"
    assert fixtures.run_key(session, "ORD-001", "Mặt hàng cùng tên dùng đối chứng chính") != fixtures.run_key(session, "ORD-001", "Mặt hàng cùng tên dùng đối chứng phụ")
    fixtures.require_run_key(session, key, "code", "ORD-001")
    with pytest.raises(GateError, match="unique key"):
        fixtures.require_run_key(session, key, "code", "ORD-002")


def test_presets_name_the_common_records_and_a_workspace_can_override_them(session, ws, monkeypatch):
    assert [fixtures.preset_model(session, n) for n in ("ledger", "license_user", "contract", "res.partner")] == ["license.ledger", "license.user", "contract.ledger", "res.partner"]
    monkeypatch.setattr(fixtures.workspace, "load", lambda name: {**ws, "fixture_models": {"ledger": "my.ledger"}})
    assert fixtures.preset_model(session, "ledger") == "my.ledger"


def test_relative_dates_follow_the_odoo_user_timezone_and_date_format():
    code = fixtures.date_expression(-180)
    assert "-180" in code and "user_context" in code and "date_format" in code and "timeZone: tz" in code
    assert "\n" not in code and code.startswith("await page.evaluate(")


def test_ui_lines_use_technical_names_and_only_read_existing_records(session, ws, monkeypatch):
    started(session, ws, monkeypatch)
    events.append(session.dir, "data_created", case="ORD-001", attempt=1, model="license.ledger", field="name", key="ZZ_AUTOTEST_x_ledger", via="ui")
    seen = []
    lines = fixtures.ui_lines(session, "ORD-001", FORM, "/system/action-624", {
        "memo": "ZZ_AUTOTEST_x_u1", "user_id": {"$ref": "BichBTNadmin"}, "license_ledger_id": {"$fixture": "ZZ_AUTOTEST_x_ledger"},
        "last_usage_date": {"$date": -180}, "state": "Active", "active": True}, check=lambda model, text: seen.append((model, text)))
    assert seen == [("res.users", "BichBTNadmin")]                        # the existing record is only checked, never created
    text = "\n".join(lines)
    assert lines[0] == 'await page.goto("/system/action-624");' and '.o_field_widget[name=\\"memo\\"]' in text
    assert "getByRole('option', {name: \"BichBTNadmin\", exact: true})" in text and "selectOption({label: \"Active\"})" in text and ".check()" in text
    assert lines[-2:] == ["await page.locator('.o_form_button_save').click();", "await page.locator('.o_form_button_save').waitFor({state: 'hidden'});"]
    assert all(line.startswith("await page.") for line in lines) and "evaluate(" in text
    assert not any(word in text for word in ("call_kw/license", "'create'", "innerHTML", "setAttribute", ".value ="))


def test_ui_lines_refuse_read_only_fields_untracked_fixtures_and_unmarked_relations(session, ws, monkeypatch):
    started(session, ws, monkeypatch)
    with pytest.raises(GateError, match="Not writable"):
        fixtures.ui_lines(session, "ORD-001", FORM, "/x", {"management_no": "1"})
    with pytest.raises(GateError, match="no fixture"):
        fixtures.ui_lines(session, "ORD-001", FORM, "/x", {"license_ledger_id": {"$fixture": "ZZ_AUTOTEST_never"}})
    with pytest.raises(GateError, match="is a relation"):
        fixtures.ui_lines(session, "ORD-001", FORM, "/x", {"user_id": "BichBTNadmin"})
    with pytest.raises(GateError, match="relative date"):
        fixtures.ui_lines(session, "ORD-001", FORM, "/x", {"last_usage_date": {"$date": "yesterday"}})


def test_created_records_are_tracked_per_run_and_cleanup_lists_before_removing(session, ws, monkeypatch):
    started(session, ws, monkeypatch)
    events.append(session.dir, "data_created", case="ORD-001", attempt=1, model="license.user", field="memo", key="ZZ_AUTOTEST_s_ORD-001_u1", via="ui")
    events.append(session.dir, "data_created", case="ORD-001", attempt=1, model="license.user", field="memo", key="ZZ_AUTOTEST_s_ORD-001_u1", via="ui")
    assert fixtures.created(session) == [{"model": "license.user", "field": "memo", "key": "ZZ_AUTOTEST_s_ORD-001_u1"}]
    assert fixtures.created(session, run="rabc")[0]["key"] == "AUTO_TEST_create-order_rabc_s_ORD-001_u1"
    sent = []

    def run(browser, *args, **kw):
        if "run-code" in args:
            sent.append(json.loads(args[-1][args[-1].rindex(', {"model"') + 2:-1]))
            return "### Result\n[7]"
        return ""

    monkeypatch.setattr(live, "_run", run)
    dry = fixtures.cleanup(session, "ORD-001")
    assert dry == [{"model": "license.user", "field": "memo", "key": "ZZ_AUTOTEST_s_ORD-001_u1", "found": 1, "removed": False}] and sent[-1]["apply"] is False
    done = fixtures.cleanup(session, "ORD-001", apply=True)
    assert done[0]["removed"] is True and sent[-1]["apply"] is True


def test_a_fixed_fixture_key_is_refused_because_it_would_find_a_record_an_earlier_run_left(session):
    from oqa import fixtures
    from oqa.state import GateError

    with pytest.raises(GateError, match="unique key"):
        fixtures.require_run_key(session, "ZZ_AUTOTEST_H14_ITEM", "code", "H14")
    fixtures.require_run_key(session, fixtures.run_key(session, "H14", "item"), "code", "H14")
