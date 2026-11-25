"""Stop hook: do not let the assistant stop while the session is executing cases and nobody is waiting for the user. It pushes back at most twice
for the same progress; the third time it lets go (a stuck assistant must end, not loop), and says so in the session log."""
import json
import os
import sys
import time
from pathlib import Path

for _root in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[1] / "skills" / "odoo-test"):
    sys.path.insert(0, str(_root))      # the skill folder, or the plugin that holds it

from oqa import autostart, paths  # noqa: E402
from oqa.state import Session  # noqa: E402
from oqa.store import read_json, write_json  # noqa: E402

MAX_PUSHBACKS = 2
DUPLICATE_WINDOW = 0.3      # seconds: only when the event cannot be identified (no transcript): the skill's Stop hook and the plugin's one answer the same event within milliseconds


def fingerprint(event):
    """Identifies one Stop event: the same event reaches the skill's hook and the plugin's with the same session and the same transcript, while the next stop comes after
    the assistant has written more. None when the event names no readable transcript (then only the time window can tell)."""
    try:
        return f"{event.get('session_id', '')}|{event.get('transcript_path')}|{os.stat(event['transcript_path']).st_size}"
    except (KeyError, TypeError, OSError):
        return None


def verdict(event=None):
    ws, session_id = paths.resolve_session(os.environ.get(paths.WS_ENV), os.environ.get(paths.SESSION_ENV))
    session = Session(ws, session_id)
    state = session.state()
    if state["phase"] == "DONE" or state.get("stopped") or not autostart.session_active(session):
        return None
    if not os.environ.get("OQA_NO_OPEN") and (not state.get("channel") or (session.dir / "analysis.json").is_file()):
        if not autostart.ensure_dashboard(ws, session_id):
            autostart.ensure_viewer(ws, session_id)             # the server runs but no tab is open: open one
    if state["waiting"] or state["phase"] == "CONFIRM":
        if autostart.wait_running(session):
            return None
        return _push(session, "wait", len(state["results"]), fingerprint(event or {}),
                     "The user owes you an action (an answer or the Confirm button) and nothing is listening for it. Run `oqa wait` in the background, then stop.")
    if state["phase"] != "EXECUTE":
        return None
    order = (read_json(session.dir / "strategy.json") or {}).get("order", [])
    left = [c for c in order if c not in state["results"]]
    if not left:
        return None
    return _push(session, "run", len(state["results"]), fingerprint(event or {}),
                 f"Session {session_id} is executing and {len(left)} case(s) have no result: {', '.join(left[:6])}. Keep going with `oqa live`, "
                 "or, if you are blocked, `oqa ask` the user or `oqa finish <case> --status BLOCKED --reason ...`.")


def _push(session, kind, progress, event_id, text):
    """Push back at most MAX_PUSHBACKS times for the same progress, then let go."""
    counter = session.dir / "logs" / f"stop_blocks_{kind}.json"
    seen = read_json(counter, {"done": -1, "blocks": 0})
    blocks = seen["blocks"] if seen["done"] == progress else 0
    same = seen.get("event") == event_id if event_id else time.time() - seen.get("at", 0) < DUPLICATE_WINDOW
    if blocks and same and seen["done"] == progress:
        return f"{text} ({blocks}/{MAX_PUSHBACKS})"          # the same stop reached us twice (the skill's hook and the plugin's): one push, not two
    if blocks >= MAX_PUSHBACKS:
        write_json(counter, {"done": progress, "blocks": blocks, "gave_up": True})
        return None
    write_json(counter, {"done": progress, "blocks": blocks + 1, "at": time.time(), "event": event_id})
    return f"{text} ({blocks + 1}/{MAX_PUSHBACKS})"


if __name__ == "__main__":
    try:
        message = verdict(json.load(sys.stdin))
    except Exception:
        sys.exit(0)
    if message:
        print(message, file=sys.stderr)
        sys.exit(2)
