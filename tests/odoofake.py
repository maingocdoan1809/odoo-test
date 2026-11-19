"""Fixtures shared by the tests: a fake Odoo, a workspace, and a session taken to the confirmed strategy."""
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from conftest import make_workbook  # noqa: E402
from oqa import analysis, connect, intake, odoo_ui, workspace  # noqa: E402
from oqa.secrets import MemoryStore  # noqa: E402
from oqa.state import Session  # noqa: E402
from oqa.store import write_json  # noqa: E402
from support import fake_odoo  # noqa: E402

CREATE = """
def run(t):
    o = t.o
    with t.step(1) as s:
        o.menu("Sales", "Orders")
        s.hl(".o_list_view")
        o.expect_visible(".o_list_view")
    with t.step(2) as s:
        o.new()
        s.hl(".o_form_view")
        o.expect_visible(".o_form_view")
    with t.step(3) as s:
        o.fill("name", o.name("order1"))
        o.m2o("partner_id", "Azure")
        s.hl("[name=name]", "[name=partner_id]")
        o.expect_value("name", o.name("order1"))
        o.expect_value("partner_id", "Azure Interior")
    with t.step(4) as s:
        o.save()
        o.expect_text("Saved")
        s.hl(".o_notification")
"""
PAGINATE = """
def run(t):
    o = t.o

    def make(i):
        o.new()
        o.fill("name", o.name(f"bulk{i}"))
        o.save()
        o.page.wait_for_selector(".o_list_view")

    with t.step(1) as s:
        o.reach(lambda: o.pager()[2] > 5, make)
        s.hl(".o_pager")
        s.check(o.pager()[2] > 5, "the list does not hold more than one page")
    with t.step(2) as s:
        o.page.click(".o_pager_next")
        o.expect_text("6-")
        s.hl(".o_pager")
"""
STEPS = {
    "T-1": [("Open Sales > Orders", "The order list shows"), ("Click New", "A blank form opens"), ("Fill the name and the customer", "Both fields hold the values"), ("Save", "A Saved notice appears")],
    "T-2": [("Make more orders than one page holds", "The pager shows more than 5 records"), ("Go to the next page", "The second page shows")],
}


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(odoo_ui, "TIMEOUT", 1500)


@pytest.fixture
def fake():
    server, url = fake_odoo.start(orders=3)
    yield url
    server.shutdown()


@pytest.fixture
def live(fake, tmp_path):
    store = MemoryStore()
    ws = workspace.save({"name": "live", "url": fake, "profiles": [{"name": "Admin", "login": "admin"}]}, store, {"Admin": "pw-admin"})
    book = make_workbook(tmp_path / "orders.xlsx", [("T-1", "Create an order", "", "1. a", "ok"), ("T-2", "Paginate", "Needs more than a page", "1. b\n2. c", "ok; continue from T-1")])
    session = Session.create("live", book, intake.build(book))
    return session, ws, store


def approve(session, ws, store, strategy_of=None):
    """ANALYZE .. CONFIRM with real code paths (connect uses the browser)."""
    data = {"schema_version": 1, "cases": []}
    for case in session.cases()["cases"]:
        steps = STEPS[case["id"]]
        data["cases"].append({"id": case["id"], "profile": "Admin", "verdict": "ready", "clarity": {"score": 4, "notes": ""}, "sufficiency": {"score": 4, "missing": []}, "refs": [],
                              "data_needs": [], "edge_cases": ["too few rows"], "depends_on": ["T-1"] if case["id"] == "T-2" else [], "reuse": "", **(strategy_of or {}).get(case["id"], {}),
                              "steps": [{"n": n, "action": a, "expected": e} for n, (a, e) in enumerate(steps, 1)]})
    analysis.validate(data, session.cases(), ws)
    write_json(session.dir / "analysis.json", data)
    session.analysis_submitted()
    connect.connect(session, ws, store)
    doc, text = analysis.strategy(data, session.cases(), ws)
    write_json(session.dir / "strategy.json", doc)
    (session.dir / "strategy.md").write_text(text, encoding="utf-8")
    session.strategy_ready(doc["sha"])
    session.confirm_current("test")
    return data
