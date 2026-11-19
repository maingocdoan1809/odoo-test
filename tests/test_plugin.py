"""The plugin built from this skill: valid manifests, hooks of its own, a SKILL.md that does not run them a second time, hooks that work from inside it."""
import json
import os
import subprocess
import sys

from oqa import globalhooks, plugin

ROOT = plugin.ROOT


def test_the_built_plugin_has_manifests_hooks_and_the_skill(tmp_path):
    built = plugin.build(tmp_path / "market")
    manifest = json.loads((built / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((built.parent / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "odoo-test" and market["plugins"][0]["source"] == "./odoo-test" and market["description"]
    hooks = json.loads((built / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert set(hooks) == {"PreToolUse", "UserPromptSubmit", "Stop"}
    assert all((built / "hooks" / s).exists() for s in ("guard.py", "stop.py", "prompt.py")) and (built / "skills" / "odoo-test" / "oqa.py").exists()
    assert not (built / "skills" / "odoo-test" / "tests").exists() and not (built / "skills" / "odoo-test" / "hooks").exists()
    text = (built / "skills" / "odoo-test" / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("\n---\n")[0]
    assert head.startswith("---\nname: odoo-test\ndescription:") and "hooks:" not in head and "PreToolUse" not in head and text.count("\n---\n") >= 1
    assert "# odoo-test" in text                                                                    # the body is intact
    assert "hooks:" in (ROOT / "SKILL.md").read_text(encoding="utf-8")                                # the source still carries its own


def test_the_plugin_hooks_run_from_inside_the_plugin(tmp_path):
    built = plugin.build(tmp_path / "market")
    env = {**os.environ, "ODOO_TEST_HOME": str(tmp_path / "home"), "HOME": str(tmp_path / "home")}
    out = subprocess.run([sys.executable, str(built / "hooks" / "prompt.py")], input=json.dumps({"prompt": "chạy testcase odoo từ a.xlsx"}), capture_output=True, text=True, env=env)
    assert out.returncode == 0 and "odoo-test skill" in out.stdout
    blocked = subprocess.run([sys.executable, str(built / "hooks" / "guard.py")], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "python oqa.py confirm"}}), capture_output=True, text=True, env=env)
    assert blocked.returncode == 2


def test_settings_json_is_left_alone_when_the_code_runs_from_a_plugin(monkeypatch, tmp_path):
    assert globalhooks.in_plugin() is False
    monkeypatch.setattr(globalhooks, "ROOT", tmp_path / "plugin" / "skills" / "odoo-test")
    (tmp_path / "plugin" / ".claude-plugin").mkdir(parents=True)
    (tmp_path / "plugin" / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    assert globalhooks.in_plugin() is True and globalhooks.missing(tmp_path / "none.json") == []
