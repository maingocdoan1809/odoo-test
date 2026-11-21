"""Regression: the saved scripts replayed on other environments, with results kept per workspace and environment."""
import pytest

from conftest import filled
from oqa import cli, live, regression, replays, workspace
from oqa.state import GateError
from test_evidence_replay import _saved_script, approved


@pytest.fixture
def staged(session, ws, store, monkeypatch):
    approved(session, ws)
    _saved_script(session)
    form = dict(ws, environments=[{"name": "staging", "url": "http://stg:8069", "db": "stg", "accounts": [{"profile": "Admin", "login": "stg_admin"}]}])
    workspace.save(form, store, {"Admin@staging": "stg-pw"})
    opened = []
    monkeypatch.setattr(live, "open_browser", lambda s, c, **kw: opened.append(kw.get("env")) or "Admin")
    monkeypatch.setattr(live, "run_actions", lambda browser, lines: "")
    return opened


def test_a_replay_on_an_environment_is_recorded_for_that_environment_only(session, staged):
    live.replay(session, "ORD-001", headed=False, action_delay_ms=0, step_delay_ms=0, env="staging")
    assert staged == ["staging"]
    assert replays.status(session, "ORD-001") == "NOT RUN"                  # the session's own Replay column is the main environment's
    live.replay(session, "ORD-001", headed=False, action_delay_ms=0, step_delay_ms=0)
    assert replays.status(session, "ORD-001") == "PASS"
    saved = regression.load("demo")
    (key,) = saved
    assert saved[key]["staging"]["latest"]["env"] == "staging" and saved[key]["main"]["latest"]["status"] == "PASS"


def test_an_unknown_environment_is_refused_before_a_browser_opens(session, staged):
    with pytest.raises(Exception, match="does not exist"):
        live.replay(session, "ORD-001", action_delay_ms=0, step_delay_ms=0, env="prod")
    assert staged == []


def test_regression_runs_every_script_on_the_environment_and_shows_a_matrix(session, staged, monkeypatch):
    rows = regression.run("demo", "staging", action_delay_ms=0, step_delay_ms=0)
    assert [(r["case"], r["env"], r["replay"]) for r in rows] == [("ORD-001", "staging", "PASS")]
    table = regression.matrix("demo")
    assert table["environments"] == ["main", "staging"]
    (row,) = table["scripts"]
    assert row["results"]["staging"]["status"] == "PASS" and row["results"]["main"]["status"] == "NOT RUN" and row["case_id"] == "ORD-001"

    def refuse(browser, lines):
        if any("includes" in line for line in lines):
            raise GateError("timeout waiting for Saved")
    monkeypatch.setattr(live, "run_actions", refuse)
    rows = regression.run("demo", "staging", action_delay_ms=0, step_delay_ms=0)
    assert rows[0]["replay"] == "FAIL" and regression.matrix("demo")["scripts"][0]["results"]["staging"]["status"] == "FAIL"


def test_the_command_fails_when_a_script_fails_so_a_pipeline_can_stop(session, staged, monkeypatch, capsys):
    monkeypatch.setattr(live, "run_actions", lambda browser, lines: (_ for _ in ()).throw(GateError("boom")) if any("includes" in l for l in lines) else "")
    assert cli.main(["regress", "--ws", "demo", "--env", "staging", "--json"]) == 1
    assert cli.main(["regress", "--ws", "demo", "--matrix"]) == 0
    assert "FAIL" in capsys.readouterr().out


def test_path_run_scripts_are_listed_and_can_run_from_the_workspace(session, staged):
    original = live.script_file(session, "ORD-001")
    text = original.read_text(encoding="utf-8")
    with session.update() as state:
        state["channel"] = "path"
    saved = live.script_file(session, "ORD-001")
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(text, encoding="utf-8")
    original.unlink()
    (row,) = regression.matrix(session.ws)["scripts"]
    assert (row["case_id"], row["session"]) == ("ORD-001", session.id)
    assert regression.scripts(session.ws)[0]["file"] == saved
    (run,) = regression.run(session.ws, "main", action_delay_ms=0, step_delay_ms=0)
    assert (run["case"], run["replay"]) == ("ORD-001", "PASS")
    assert regression.load(session.ws)[row["key"]]["main"]["latest"]["status"] == "PASS"
    assert regression.matrix(session.ws)["scripts"][0]["results"]["main"]["status"] == "PASS"


def _manual_result(folder, **over):
    import json
    folder.mkdir(parents=True, exist_ok=True)
    data = {"schema": 1, "workspace": "demo", "script": regression.scripts("demo")[0]["key"], "case": "ORD-001", "env": "staging", "status": "FAIL",
            "at": "2026-11-20 10:00:00", "run_id": "rabc", "failed_step": 2, "reason": "timeout", "source": "manual", **over}
    (folder / "result.json").write_text(json.dumps(data), encoding="utf-8")
    return folder / "result.json"


def test_a_run_made_by_hand_is_imported_once_and_shows_on_the_matrix(session, staged, tmp_path, capsys):
    file = _manual_result(tmp_path / "evidence" / "run1")
    assert cli.main(["results", "import", str(tmp_path / "evidence"), "--ws", "demo"]) == 0
    assert cli.main(["results", "import", str(file), "--ws", "demo"]) == 0                  # the same run twice counts once
    latest = regression.matrix("demo")["scripts"][0]["results"]["staging"]
    assert (latest["status"], latest["failed_step"], latest["source"]) == ("FAIL", 2, "manual")
    assert len(regression.load("demo")[regression.scripts("demo")[0]["key"]]["staging"]["runs"]) == 1


@pytest.mark.parametrize("over, message", [({"env": "prod"}, "environment"), ({"status": "WAT"}, "PASS or FAIL"), ({"workspace": "other"}, "workspace"),
                                           ({"script": "x/y"}, "not a saved script")])
def test_a_result_that_cannot_be_placed_is_refused_and_nothing_is_imported(session, staged, tmp_path, over, message):
    _manual_result(tmp_path / "ok", run_id="r1", status="PASS")
    _manual_result(tmp_path / "bad", **over)
    with pytest.raises(GateError, match=message):
        regression.import_results("demo", tmp_path)
    assert regression.load("demo") == {}


def test_the_scripts_zip_tells_the_runner_which_script_and_the_runner_writes_result_json(session, staged):
    import io, json, zipfile
    from oqa import script_bundle
    archive = zipfile.ZipFile(io.BytesIO(script_bundle.build(session)))
    manifest = json.loads(archive.read("cases.json"))
    assert manifest["workspace"] == "demo" and manifest["cases"]["ORD-001"]["script"] == regression.scripts("demo")[0]["key"]
    assert manifest["cases"]["ORD-001"]["fixtureStem"] == "AUTO_TEST_create-order_"
    assert "ZZ_AUTOTEST_" in manifest["cases"]["ORD-001"]["fixtureSources"]
    runner = archive.read("replay.mjs").decode()
    assert "result.json" in runner and "process.exitCode = 1" in runner and "OQA_ENV" in runner


def _fake_browser(monkeypatch):
    calls = []

    def run(browser, *args, **kw):
        calls.append(args)
        if args[0] == "video-start":
            from pathlib import Path
            Path(args[1]).write_bytes(b"webm")
        return ""
    monkeypatch.setattr(live, "_run", run)
    return calls


def test_video_has_a_note_and_a_chapter_per_step_and_is_kept_by_its_mode(session, staged, monkeypatch):
    calls = _fake_browser(monkeypatch)
    live.replay(session, "ORD-001", headed=False, action_delay_ms=0, step_delay_ms=0, env="staging", video="on")
    kept = replays.latest(session, "ORD-001") or {}
    run = regression.load("demo")[regression.scripts("demo")[0]["key"]]["staging"]["latest"]
    assert run["video"].endswith(".webm") and "staging-" in run["video"]
    from pathlib import Path
    assert Path(run["video"]).is_file()
    chapters = [c[1] for c in calls if c[0] == "video-chapter"]
    assert chapters and all(text.startswith("Bước ") for text in chapters)
    assert any(c[0] == "video-stop" for c in calls) and kept == {}


def test_video_in_fail_mode_is_dropped_when_the_run_passes_and_kept_when_it_fails(session, staged, monkeypatch):
    from pathlib import Path
    _fake_browser(monkeypatch)
    live.replay(session, "ORD-001", headed=False, action_delay_ms=0, step_delay_ms=0, env="staging", video="fail")
    key = regression.scripts("demo")[0]["key"]
    assert "video" not in regression.load("demo")[key]["staging"]["latest"]
    assert not list((Path(live.paths.workspace_dir("demo")) / "videos").rglob("*.webm"))

    def refuse(browser, lines):
        if any("includes" in line for line in lines):
            raise GateError("timeout waiting for Saved")
    monkeypatch.setattr(live, "run_actions", refuse)
    with pytest.raises(GateError):
        live.replay(session, "ORD-001", headed=False, action_delay_ms=0, step_delay_ms=0, env="staging", video="fail")
    assert Path(regression.load("demo")[key]["staging"]["latest"]["video"]).is_file()


def test_no_video_unless_asked_and_a_fast_regression_has_none_by_default(session, staged, monkeypatch):
    calls = _fake_browser(monkeypatch)
    live.replay(session, "ORD-001", headed=False, action_delay_ms=0, step_delay_ms=0)
    assert not [c for c in calls if c[0].startswith("video")]
    with pytest.raises(GateError, match="video must be"):
        live.replay(session, "ORD-001", video="always")
    args = cli.build_parser().parse_args(["regress", "--env", "staging", "--fast"])
    assert cli.default_video(args) == "off"
    assert cli.default_video(cli.build_parser().parse_args(["regress", "--env", "staging"])) == "fail"
    assert cli.default_video(cli.build_parser().parse_args(["live", "replay", "X"])) == "off"
