"""Build this skill as a Claude Code plugin.

    <out>/.claude-plugin/marketplace.json          so `/plugin marketplace add <out>` finds it
    <out>/odoo-test/.claude-plugin/plugin.json
    <out>/odoo-test/hooks/{hooks.json, guard.py, stop.py, prompt.py}   active from the first message, because a plugin's hooks do not wait for the skill
    <out>/odoo-test/skills/odoo-test/...                                the skill itself (SKILL.md without its own hooks: the plugin's replace them)

Installing the plugin is the whole setup for hooks: nothing is written into settings.json."""
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import VERSION

ROOT = Path(__file__).resolve().parents[1]
NAME = "odoo-test"
SKILL_ITEMS = {"SKILL.md", "oqa.py", "oqa", "references"}
SKIP_DEV = shutil.ignore_patterns("temp", "tests", "__pycache__", ".pytest_cache", "*.pyc", "*.pyo")
DESCRIPTION = "Test an Odoo system from a testcase workbook: plan, run every case in a browser with red-framed screenshots, repair, and write the Excel evidence."


def move_aside(folder):
    """Rename a folder out of the way so the new copy can take its place; delete it with discard() once the new one is in. On Windows a folder that a running
    process uses as its working folder cannot be renamed: then nothing is touched and the error says so, instead of deleting half of it."""
    from .state import GateError
    if not folder.exists():
        return None
    aside = folder.with_name(f".{folder.name}.old-{os.getpid()}-{int(time.time() * 1000)}")
    try:
        folder.rename(aside)
    except PermissionError as error:
        raise GateError(f"{folder} is in use by a running process (an oqa dashboard or worker started from it): stop it, then try again. ({error})") from error
    return aside


def put_back(aside, folder):
    """The new copy failed: the old folder returns."""
    if aside and aside.exists():
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        aside.rename(folder)


def discard(aside):
    if aside:
        shutil.rmtree(aside, ignore_errors=True)


def copy_skill(target, *, include_hooks=True):
    items = SKILL_ITEMS | {"hooks"} if include_hooks else SKILL_ITEMS

    def ignore(directory, names):
        if Path(directory) == ROOT:
            return set(names) - items
        return SKIP_DEV(directory, names)

    shutil.copytree(ROOT, target, ignore=ignore)


def hooks_json():
    run = lambda script: 'sh -c \'for P in python3 python py; do "$P" -c "" >/dev/null 2>&1 && exec "$P" "${CLAUDE_PLUGIN_ROOT}/hooks/%s"; done; echo "odoo-test hook: no working python" >&2; exit 1\'' % script
    return {"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell|Write|Edit|MultiEdit|Read|Grep", "hooks": [{"type": "command", "command": run("guard.py")}]}],
                      "UserPromptSubmit": [{"hooks": [{"type": "command", "command": run("prompt.py")}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": run("stop.py")}]}]}}


def skill_text():
    """SKILL.md without the `hooks:` block of its frontmatter: the plugin's hooks.json carries them, and running both would push back twice."""
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    head, sep, body = text.partition("\n---\n")
    if not text.startswith("---\n") or not sep:
        return text
    return re.sub(r"\nhooks:\n(?:[ \t]+.*\n?|\n)*", "\n", head + "\n", count=1).rstrip("\n") + sep + body


def build(out, *, plugins_dir="", market=f"{NAME}-local"):
    """Build the plugin under `out/<plugins_dir>/odoo-test` and its marketplace file at `out/.claude-plugin`. `market` names the marketplace."""
    out = Path(out).expanduser().resolve()
    plugin = out / plugins_dir / NAME if plugins_dir else out / NAME
    if plugin.resolve() != plugin or plugin == ROOT or plugin in ROOT.parents:
        raise ValueError(f"Cannot replace {plugin} with a plugin build.")
    aside = move_aside(plugin)
    try:
        _write_plugin(plugin)
    except BaseException:
        put_back(aside, plugin)
        raise
    discard(aside)
    (out / ".claude-plugin").mkdir(exist_ok=True)
    (out / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": market, "owner": {"name": "odoo-test"}, "description": DESCRIPTION,
                                                                         "plugins": [{"name": NAME, "source": f"./{plugins_dir}/{NAME}" if plugins_dir else f"./{NAME}", "description": DESCRIPTION}]}, indent=2) + "\n", encoding="utf-8")
    return plugin


def _write_plugin(plugin):
    skill = plugin / "skills" / NAME
    copy_skill(skill, include_hooks=False)
    (skill / "SKILL.md").write_text(skill_text(), encoding="utf-8")
    (plugin / "hooks").mkdir(parents=True)
    for script in ("guard.py", "stop.py", "prompt.py"):
        shutil.copy2(ROOT / "hooks" / script, plugin / "hooks" / script)
    (plugin / "hooks" / "hooks.json").write_text(json.dumps(hooks_json(), indent=2) + "\n", encoding="utf-8")
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": NAME, "version": VERSION, "description": DESCRIPTION, "author": {"name": "odoo-test"}}, indent=2) + "\n", encoding="utf-8")
    (plugin / "README.md").write_text(f"# {NAME}\n\n{DESCRIPTION}\n\nInstall: `/plugin marketplace add <folder that holds this plugin>` then `/plugin install {NAME}@{NAME}-local`, or try it once with `claude --plugin-dir <this folder>`.\n"
                                      "Needs Python 3.9+. The first run sets up its own environment (`oqa doctor --fix`). Hooks start with the plugin: nothing is written into settings.json.\n", encoding="utf-8")


REPO_PLUGINS = "plugins"
REPO_MARKET = NAME


def build_repo():
    """Refresh the plugin committed in this repository, so `/plugin marketplace add <owner>/<repo>` installs it: <repo>/.claude-plugin/marketplace.json + <repo>/plugins/odoo-test."""
    return build(ROOT, plugins_dir=REPO_PLUGINS, market=REPO_MARKET)
