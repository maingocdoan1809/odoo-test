"""The hooks, run the way Claude Code runs them: a subprocess fed the tool call as JSON."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import filled
from oqa import analysis, autostart, workspace
from oqa.store import write_json

ROOT = Path(__file__).resolve().parents[1]


def run(script, payload, home, **env):
    return subprocess.run([sys.executable, str(ROOT / "hooks" / script)], input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, "ODOO_TEST_HOME": str(home), "USERPROFILE": str(home), "HOME": str(home), **env})


def guard(tool, home, **args):
    return run("guard.py", {"tool_name": tool, "tool_input": args}, home)


def test_session_files_credentials_and_confirming_are_off_limits(session, home):
    state = str(session.dir / "state.json")
    for tool, args in (("Write", {"file_path": state}), ("Edit", {"file_path": str(session.dir / "events.jsonl")}), ("Write", {"file_path": str(session.dir / "evidence" / "A" / "s1.png")}),
                       ("Write", {"file_path": str(home / "workspaces" / "demo" / "workspace.json")}), ("Read", {"file_path": str(session.dir / "auth" / "Admin.json")}),
                       ("Bash", {"command": "python oqa.py confirm"}), ("Bash", {"command": "cd x && python -m oqa confirm --ws demo"}),
                       ("Bash", {"command": f"cat {session.dir}/auth/Admin.json"}),
                       ("Bash", {"command": f"python {home}/workspaces/demo/scripts/orders/T-1.py"})):
        result = guard(tool, home, **args)
        assert result.returncode == 2 and result.stderr.strip(), (tool, args)


def test_everything_else_is_allowed(session, home, tmp_path):
    for tool, args in (("Write", {"file_path": str(home / "workspaces" / "demo" / "scripts" / "orders" / "T-1.py")}), ("Write", {"file_path": str(tmp_path / "analysis.json")}),
                       ("Read", {"file_path": str(session.dir / "strategy.md")}), ("Bash", {"command": "python oqa.py run"}), ("Bash", {"command": "python oqa.py status --json"}),
                       ("Bash", {"command": "echo confirm the strategy with the user"}), ("Write", {"file_path": "C:/project/src/app.py"})):
        assert guard(tool, home, **args).returncode == 0, (tool, args)


def test_garbage_input_never_blocks(home):
    result = subprocess.run([sys.executable, str(ROOT / "hooks" / "guard.py")], input="not json", capture_output=True, text=True, env={**os.environ, "ODOO_TEST_HOME": str(home)})
    assert result.returncode == 0


def executing(session, tmp_path):
    config = workspace.load(session.ws)
    data = filled(session.cases(), config)
    write_json(session.dir / "analysis.json", data)
    session.analysis_submitted()
    session.connected({})
    doc, text = analysis.strategy(data, session.cases(), config)
    write_json(session.dir / "strategy.json", doc)
    session.strategy_ready(doc["sha"])
    session.confirm_current("t")


def stop(session, home):
    return run("stop.py", {"hook_event_name": "Stop"}, home, OQA_WS=session.ws, OQA_SESSION=session.id)


def test_stop_pushes_back_twice_then_lets_go(session, home, tmp_path):
    assert stop(session, home).returncode == 0                   # still analysing: stopping is fine
    executing(session, tmp_path)
    first = stop(session, home)
    time.sleep(0.4)                                               # the assistant keeps working between two stops (the same event reaching two hooks is within ms)
    second = stop(session, home)
    time.sleep(0.4)
    third = stop(session, home)
    assert (first.returncode, second.returncode, third.returncode) == (2, 2, 0)
    assert "ORD-001" in first.stderr and "1/2" in first.stderr and "2/2" in second.stderr


def test_the_same_stop_reaching_two_hooks_counts_once(session, home, tmp_path):
    executing(session, tmp_path)
    one, two = stop(session, home), stop(session, home)          # no pause: as when the skill's hook and the plugin's both fire
    assert (one.returncode, two.returncode) == (2, 2) and "1/2" in one.stderr and "1/2" in two.stderr
    time.sleep(0.4)
    assert "2/2" in stop(session, home).stderr


def test_stop_resets_when_there_is_progress_and_is_fine_when_the_user_is_asked(session, home, tmp_path):
    executing(session, tmp_path)
    assert stop(session, home).returncode == 2
    session.begin_attempt("ORD-001")
    session.finish_case("ORD-001", "BLOCKED", "x")
    after = stop(session, home)
    assert after.returncode == 2 and "1/2" in after.stderr                                      # progress made: the count starts again
    session.ask("Which customer?")
    assert stop(session, home).returncode == 2                                                 # waiting for the user but nobody listens: push back
    autostart.beat(session)
    assert stop(session, home).returncode == 0                                                 # `oqa wait` is running: the assistant may stop


def test_the_assistant_cannot_stop_or_resume_the_test_for_the_user(session, home):
    for command in ("python oqa.py stop", "python oqa.py resume --ws demo", "cd x && python -m oqa resume"):
        assert guard("Bash", home, command=command).returncode == 2, command
    assert guard("Bash", home, command="python oqa.py wait").returncode == 0 and guard("Bash", home, command="python oqa.py status").returncode == 0


def test_a_stopped_session_may_be_left_alone(session, home, tmp_path):
    executing(session, tmp_path)
    session.stop("dashboard")
    assert stop(session, home).returncode == 0


def prompt(session, home, text):
    return run("prompt.py", {"hook_event_name": "UserPromptSubmit", "prompt": text}, home, OQA_WS=session.ws, OQA_SESSION=session.id, OQA_NO_OPEN="1")


def test_prompt_hook_reports_user_actions_and_a_missing_wait(session, home, tmp_path):
    out = prompt(session, home, "tiếp đi")
    assert out.returncode == 0 and "phase ANALYZE" in out.stdout
    session.ask("Which customer?")
    assert "no `oqa wait` is running" in prompt(session, home, "ok").stdout
    autostart.beat(session)
    assert "no `oqa wait`" not in prompt(session, home, "ok").stdout


def test_prompt_hook_points_at_the_skill_when_no_session(home):
    out = subprocess.run([sys.executable, str(ROOT / "hooks" / "prompt.py")], input=json.dumps({"prompt": "chạy testcase odoo từ file a.xlsx"}), capture_output=True, text=True,
                         env={**os.environ, "ODOO_TEST_HOME": str(home), "HOME": str(home), "OQA_NO_OPEN": "1"})
    assert out.returncode == 0 and "odoo-test skill" in out.stdout


def test_installing_the_global_hooks_keeps_the_rest_and_a_backup_and_is_repeatable(tmp_path):
    from oqa import globalhooks
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "sonnet", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo other"}]}]}}), encoding="utf-8")
    assert sorted(globalhooks.missing(settings)) == ["prompt.py", "stop.py"]
    path, added, backup = globalhooks.install(settings)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert sorted(added) == ["prompt.py", "stop.py"] and data["model"] == "sonnet" and backup.exists()
    assert [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]][0] == "echo other" and len(data["hooks"]["Stop"]) == 2
    assert globalhooks.missing(settings) == [] and globalhooks.install(settings)[1] == []            # again: nothing added
    for event in ("UserPromptSubmit", "Stop"):
        for group in data["hooks"][event]:
            for hook in group["hooks"]:
                assert "  " not in hook["command"] and (hook["command"].startswith(("python3 ", "python ")) or hook["command"] == "echo other")


def test_a_settings_file_that_is_not_json_is_never_overwritten(tmp_path):
    import pytest
    from oqa import globalhooks
    settings = tmp_path / "settings.json"
    settings.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        globalhooks.install(settings)
    assert settings.read_text(encoding="utf-8") == "{ not json"
