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
