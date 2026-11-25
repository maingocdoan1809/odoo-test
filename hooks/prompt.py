"""UserPromptSubmit hook. Unlike the hooks in SKILL.md (which only run once the skill is active), this one is installed in settings.json
(`oqa hooks` prints the snippet), so it works before the skill is loaded and on every message after.

It does three things, all quietly:
1. a session is open: starts the dashboard if it is not running, and tells the assistant what the user did there since the last message;
2. a session is waiting for the user: reminds the assistant to run `oqa wait` in the background;
3. no session yet, but the message is about testing Odoo from a workbook: tells the assistant to load the odoo-test skill."""
import json
import re
import sys
from pathlib import Path

for _root in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[1] / "skills" / "odoo-test"):
    sys.path.insert(0, str(_root))      # the skill folder, or the plugin that holds it

from oqa import autostart, paths  # noqa: E402
from oqa.state import Session  # noqa: E402

TOPIC = re.compile(r"odoo[-\s]?test|test\s*case|testcase|ca\s*kiểm\s*thử|chạy\s*test|test\s+odoo|kiểm\s*thử.*odoo|\.xlsx", re.I)


def note(prompt):
    try:
        ws, session_id = paths.resolve_session()
    except Exception:
        ws = None
    if ws:
        session = Session.open(ws, session_id)
        state = session.state()
        if state["phase"] != "DONE" and autostart.session_active(session):
            lines = [f"oqa session {session_id} (workspace {ws}) is in phase {state['phase']}. Follow `oqa status`; this skill's process still applies."]
            opened = autostart.ensure_open(session, state)
            if opened == "started":
                lines.append("The dashboard was not running: it has just been started for the user.")
            elif opened == "opened":
                lines.append("The dashboard was running but no tab was open: a tab has just been opened for the user.")
            news = session.news()
            if news:
                lines.append("The user acted on the dashboard since you were last told (run `oqa wait` to read and clear it): " + "; ".join(e["kind"] for e in news) + ".")
            if state["waiting"] or state["phase"] == "CONFIRM":
                if not autostart.wait_running(session):
                    lines.append("The user owes an action and no `oqa wait` is running: start it in the background now.")
            return "\n".join(lines)
    if TOPIC.search(prompt or ""):
        return "This looks like testing Odoo from a testcase workbook: load the odoo-test skill first (it opens the setup form and the dashboard by itself)."
    return None


if __name__ == "__main__":
    try:
        text = note(json.load(sys.stdin).get("prompt", ""))
    except Exception:
        sys.exit(0)
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))
