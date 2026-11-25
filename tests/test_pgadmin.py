import pytest

from oqa import pgadmin, workspace
from oqa.secrets import MemoryStore
from oqa.state import GateError
from oqa.store import SchemaError


def raw(**extra):
    return {"name": "w", "url": "http://odoo.example:8069", "db": "odoo_demo", "profiles": [{"name": "Admin", "login": "admin"}], **extra}


@pytest.mark.parametrize("sql", ["select 1", "SELECT id FROM res_partner;", "with a as (select 1) select * from a", "-- note\nselect 'update me' as t",
                                 "explain select 1", "select \"update\" from t"])
def test_reading_sql_is_allowed(sql):
    assert pgadmin.check_sql(sql)


@pytest.mark.parametrize("sql", ["delete from res_partner", "update res_partner set name='x'", "select 1; select 2", "drop table t", "",
                                 "with d as (delete from t returning *) select * from d", "select 1; delete from t"])
def test_writing_or_multiple_sql_is_refused(sql):
    with pytest.raises(GateError):
        pgadmin.check_sql(sql)


def test_trailing_semicolon_is_removed():
    assert pgadmin.check_sql("select 1 ;") == "select 1"


def test_workspace_keeps_pgadmin_settings_and_passwords_apart():
    store = MemoryStore()
    ws = workspace.normalize(raw(pgadmin_url="pgadmin.example:5050/", pgadmin_login="qa@example.com", pgadmin_server="odoo-dev"))
    assert ws["pgadmin_url"] == "http://pgadmin.example:5050"
    with pytest.raises(SchemaError):                       # no password yet
        workspace.save(raw(pgadmin_url="http://p:5050", pgadmin_login="qa@example.com"), store, {"Admin": "x"})


def test_save_stores_pgadmin_passwords_in_the_store_not_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ODOO_TEST_HOME", str(tmp_path))
    store = MemoryStore()
    saved = workspace.save(raw(pgadmin_url="http://p:5050", pgadmin_login="qa@example.com"), store, {"Admin": "pw", "@pgadmin": "qapass", "@pgadmin-db": "dbpass"})
    assert store.get("oqa:w:pgadmin") == "qapass" and store.get("oqa:w:pgadmin-db") == "dbpass"
    assert "qapass" not in str(saved) and "dbpass" not in str(saved)
    view = workspace.public_view(workspace.load("w"), store)
    assert view["has_pgadmin_secret"] and view["has_pgadmin_db_secret"]
    workspace.save(raw(), store, {})                       # PgAdmin switched off: its passwords are deleted
    assert store.get("oqa:w:pgadmin") is None and store.get("oqa:w:pgadmin-db") is None


def test_settings_say_what_is_missing():
    store = MemoryStore()
    with pytest.raises(GateError, match="PgAdmin address"):
        pgadmin.settings({"name": "w", "db": "d"}, store)
    ws = {"name": "w", "db": "d", "pgadmin_url": "http://p", "pgadmin_login": "a@b.c"}
    with pytest.raises(GateError, match="password"):
        pgadmin.settings(ws, store)
    store.set("oqa:w:pgadmin", "x")
    assert pgadmin.settings(ws, store)["database"] == "d"


def test_connect_checks_pgadmin_before_the_run(monkeypatch):
    from contextlib import contextmanager

    from oqa import connect

    assert connect.check_pgadmin({"name": "w"}, MemoryStore()) == "not configured"
    ws = {"name": "w", "pgadmin_url": "http://p:5050"}

    @contextmanager
    def fails(*_args, **_kwargs):
        raise GateError("PgAdmin shows no database named x")
        yield

    @contextmanager
    def works(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(pgadmin, "session", works)
    assert connect.check_pgadmin(ws, MemoryStore()) == "ok"
    monkeypatch.setattr(pgadmin, "session", fails)
    with pytest.raises(GateError, match="PgAdmin does not connect: PgAdmin shows no database"):
        connect.check_pgadmin(ws, MemoryStore())


def _fake_pgadmin(monkeypatch, result):
    from contextlib import contextmanager

    from PIL import Image

    class Page:
        def query(self, sql, picture):
            Image.new("RGB", (1600, 900), (240, 240, 240)).save(picture)
            self.sql = sql
            return result

    @contextmanager
    def fake(*_args, **_kwargs):
        yield Page()

    monkeypatch.setattr(pgadmin, "session", fake)


def _recorded_session(session, ws, monkeypatch):
    from PIL import Image

    from oqa import live
    from test_evidence_replay import approved

    approved(session, ws)
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "_run", lambda *a, **kw: "")
    monkeypatch.setattr(live, "_box", lambda browser, target: ((50, 50, 200, 40), (600, 400)))
    monkeypatch.setattr(live, "_shot", lambda browser, path: (path.parent.mkdir(parents=True, exist_ok=True), Image.new("RGB", (600, 400), (240, 200, 180)).save(path)))
    live.start(session, "ORD-001")


def test_shot_attaches_the_pgadmin_picture_to_a_recorded_step(session, ws, monkeypatch, store):
    from oqa import live, view

    _recorded_session(session, ws, monkeypatch)
    result = {"rows": [["", "id", "name"], ["1", "7", "AUTO_TEST_x"]], "total": 1, "error": "", "box": [352, 501, 1248, 370]}
    _fake_pgadmin(monkeypatch, result)
    with pytest.raises(GateError, match="Record the steps in order"):          # only the next step, or one already recorded
        pgadmin.shot(session, ws, store, "ORD-001", 2, "select id, name from res_partner")
    out, picture = pgadmin.shot(session, ws, store, "ORD-001", 1, "select id, name from res_partner;")     # before the step: it becomes part of the step
    assert out is result and picture.is_file()
    live.step(session, "ORD-001", 1, "PASS", "The saved record is visible", target="#saved")
    images = view.case_records(session)[0]["attempts"][0]["steps"][0]["images"]
    assert len(images) == 2 and images[0]["target"] == "PgAdmin"
    pgadmin.shot(session, ws, store, "ORD-001", 1, "select 1")                                                # after the step: supplemental
    assert len(view.case_records(session)[0]["attempts"][0]["steps"][0]["images"]) == 3
    assert "1 row(s)" in pgadmin.summary(result, "x") and "7 | AUTO_TEST_x" in pgadmin.summary(result, "x")
    with pytest.raises(GateError, match="reading query"):
        pgadmin.shot(session, ws, store, "ORD-001", 1, "delete from res_partner")


def test_the_worker_has_a_pgadmin_tool_that_takes_only_the_sql(session, ws, monkeypatch, store):
    from oqa import pgadmin as module, worker_mcp
    from oqa.secrets import default_store  # noqa: F401  (imported by the tool)

    tool = next(t for t in worker_mcp.TOOLS if t["name"] == "pgadmin_shot")
    assert set(tool["inputSchema"]["required"]) == {"n", "sql"}
    seen = {}

    def fake_shot(run, workspace_doc, _store, case, n, sql, caption=None, headless=True):
        seen.update(case=case, n=n, sql=sql)
        return {"rows": [["", "c"], ["1", "1"]], "total": 1, "error": "", "box": None}, "picture.png"

    monkeypatch.setattr(module, "shot", fake_shot)
    text = worker_mcp._call(session, "ORD-001", "w1", "pgadmin_shot", {"n": 2, "sql": "select 1"})
    assert seen == {"case": "ORD-001", "n": 2, "sql": "select 1"} and "1 row(s)" in text


def test_a_case_run_again_gets_a_new_record_name(session, ws, monkeypatch):
    from oqa import fixtures, live
    from test_evidence_replay import approved

    approved(session, ws)
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "_run", lambda *a, **kw: "")
    live.start(session, "ORD-001")
    first = fixtures.run_key(session, "ORD-001", "lien he")
    assert "-a2-" not in first and first.endswith("_lien-he")
    monkeypatch.setattr(live, "_attempt", lambda s, c: 2)
    second = fixtures.run_key(session, "ORD-001", "lien he")
    assert second != first and second.endswith("a2-lien-he")
    from oqa import record_names

    assert record_names.owns(session, second, "AUTO_TEST_", "ORD-001")      # still a name this run owns


def test_sql_is_laid_out_one_clause_per_line_and_quoted_text_is_untouched():
    out = pgadmin.format_sql("select a, b from t where name = 'x from y where z' order by a limit 5")
    assert out.split("\n") == ["select a, b", "from t", "where name = 'x from y where z'", "order by a", "limit 5"]
    assert pgadmin.format_sql("select 1\nfrom t") == "select 1\nfrom t"


def test_a_shows_text_the_page_does_not_carry_is_refused_when_the_step_is_recorded(session, ws, monkeypatch):
    from oqa import live
    from PIL import Image
    from test_evidence_replay import approved

    approved(session, ws)
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "_box", lambda browser, target: ((50, 50, 200, 40), (600, 400)))
    monkeypatch.setattr(live, "_shot", lambda browser, path: (path.parent.mkdir(parents=True, exist_ok=True), Image.new("RGB", (600, 400), (240, 200, 180)).save(path)))

    def run(browser, command, *args, **kwargs):
        if command == "run-code" and "waitForFunction" in " ".join(map(str, args)) and "e.g. Lumber Inc" in " ".join(map(str, args)):
            raise GateError("TimeoutError: page.waitForFunction: Timeout 1500ms exceeded.")
        return ""

    monkeypatch.setattr(live, "_run", run)
    live.start(session, "ORD-001")
    with pytest.raises(GateError, match="could not find 'shows' verbatim"):
        live.step(session, "ORD-001", 1, "PASS", "The form is empty and ready", target="getByRole('textbox')", verified_code="await page.goto('http://localhost/x');", shows="e.g. Lumber Inc")


def test_a_key_asked_before_the_case_starts_is_for_the_next_attempt(session, ws, monkeypatch):
    from oqa import fixtures, live
    from test_evidence_replay import approved

    approved(session, ws)
    monkeypatch.setattr(live, "open_browser", lambda *a, **kw: "Admin")
    monkeypatch.setattr(live, "_run", lambda *a, **kw: "")
    assert fixtures.run_key(session, "ORD-001", "x").endswith("_x")                  # nothing started yet: attempt 1
    live.start(session, "ORD-001")
    first = fixtures.run_key(session, "ORD-001", "x")
    live.recover(session, "ORD-001", "the first worker stopped; start again with new data")
    asked_before_start = fixtures.run_key(session, "ORD-001", "x")
    live.start(session, "ORD-001")
    assert asked_before_start == fixtures.run_key(session, "ORD-001", "x") != first
