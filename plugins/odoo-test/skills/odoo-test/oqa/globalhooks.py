"""The hooks in SKILL.md only run once the skill is loaded. These two run from the first message, so they are written into Claude Code's settings.json:
UserPromptSubmit (starts the dashboard, tells what the user did there, points at the skill) and Stop (does not let the assistant leave while the user is
being waited for). `guard.py` stays in the skill: it only matters while a test is running."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WANTED = (("UserPromptSubmit", "prompt.py", None), ("Stop", "stop.py", None))


def settings_path(custom=None):
    return Path(custom).expanduser() if custom else Path.home() / ".claude" / "settings.json"


def _exe():
    return "python" if sys.platform == "win32" else "python3"      # a Linux or macOS machine often has no `python`


def _command(script):
    return f'{_exe()} "{ROOT / "hooks" / script}"'


def _installed(settings, event, script):
    return any(script in h.get("command", "") and str(ROOT) in h.get("command", "") for g in (settings.get("hooks") or {}).get(event, []) for h in g.get("hooks", []))


def _read(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def in_plugin():
    """True when this code runs from the installed plugin: its hooks are already active, so settings.json must not get a second copy."""
    return ROOT.parent.name == "skills" and (ROOT.parents[1] / ".claude-plugin" / "plugin.json").exists()


def missing(custom=None):
    """The scripts not yet in settings.json (an unreadable file counts as missing everything)."""
    if in_plugin():
        return []
    try:
        settings = _read(settings_path(custom))
    except (OSError, ValueError):
        return [script for _, script, _ in WANTED]
    if any(k.startswith("odoo-test@") and v for k, v in (settings.get("enabledPlugins") or {}).items()):
        return []                                        # the plugin is on: its hooks are the ones that run
    return [script for event, script, _ in WANTED if not _installed(settings, event, script)]


def install(custom=None):
    """Add what is missing, keep everything else, and keep a copy of the old file next to it. Returns (path, added, backup or None)."""
    path = settings_path(custom)
    settings = _read(path)                               # raises when the file is not valid JSON: it is never overwritten
    added = [script for event, script, _ in WANTED if not _installed(settings, event, script)]
    if not added or not missing(custom):
        return path, [], None
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak-oqa")
        shutil.copy2(path, backup)
    for event, script, _ in WANTED:
        if script in added:
            settings.setdefault("hooks", {}).setdefault(event, []).append({"hooks": [{"type": "command", "command": _command(script)}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, added, backup
