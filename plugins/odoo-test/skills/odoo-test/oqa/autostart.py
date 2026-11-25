"""Things that must be running without anyone asking: the dashboard, and an `oqa wait` while the user owes the assistant an action.

Used by `oqa session new` (works even when no hook is installed) and by the hooks (which repair what is missing). A heartbeat file is used
instead of a process id, so the check works the same on Windows, macOS and Linux."""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote

from . import build_id, paths
from .store import read_json, write_json

HEARTBEAT_SECONDS = 4
FRESH_SECONDS = 20
ACTIVE_SECONDS = 6 * 3600       # a session untouched for this long is left alone by the global hooks (it may belong to other work)
SPAWN_COOLDOWN = 30             # a dashboard just started needs a few seconds before it answers: do not start a second one


VIEWER_FRESH = 90         # seconds: the page asks the server every 1.5s; a hidden tab is slowed by the browser to about once a minute
REOPEN_COOLDOWN = 120      # a tab just opened needs a moment to load; and a user who closed it on purpose is not hit again and again


def _wsl():
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def open_url(url):
    """Open `url` in the user's browser. On WSL the Linux xdg-open often reaches no browser at all, so the Windows side is asked (cmd.exe start). True when something was started."""
    if _wsl():
        import shutil
        for exe, args in (("cmd.exe", ["/c", "start", "", url]), ("explorer.exe", [url])):
            if shutil.which(exe):
                try:
                    subprocess.Popen([exe, *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except OSError:
                    continue
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def mark_viewer():
    """Called by the dashboard server whenever the open page asks it something: a tab is showing the dashboard right now."""
    mark = paths.home() / "dashboard_viewer.json"
    if time.time() - (read_json(mark, {}) or {}).get("at", 0) >= 2:
        write_json(mark, {"at": time.time()})


def viewer_seen(max_age=VIEWER_FRESH):
    """True when a dashboard tab asked the server within the last `max_age` seconds."""
    return time.time() - (read_json(paths.home() / "dashboard_viewer.json", {}) or {}).get("at", 0) < max_age


def ensure_viewer(ws, session_id):
    """The dashboard server may run with no tab open (the user closed it, or the browser never opened). When no tab asked the server lately, open one on this session.
    True when a tab was opened."""
    if os.environ.get("OQA_NO_OPEN"):
        return False
    url = dashboard_running()
    if not url or viewer_seen():
        return False
    mark = paths.home() / "dashboard_open.json"
    if time.time() - (read_json(mark, {}) or {}).get("at", 0) < REOPEN_COOLDOWN:
        return False
    write_json(mark, {"at": time.time()})
    return open_url(f"{url}#{quote(ws)}/{quote(session_id)}")


def _alive(url):
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def dashboard_running():
    """The address of the running dashboard (built from these files), or None."""
    running = read_json(paths.home() / "dashboard.json", None)
    if running and running.get("build") == build_id() and _alive(running["url"]):
        return running["url"]
    return None


def ensure_dashboard(ws, session_id, select_existing=False):
    """Start the dashboard in the background on this session when none runs (a running one is left alone). True when one was started."""
    if os.environ.get("OQA_NO_OPEN"):
        return False
    existing = dashboard_running()
    if existing:
        if select_existing:
            return open_url(f"{existing}#{quote(ws)}/{quote(session_id)}")
        return False
    mark = paths.home() / "dashboard_spawn.json"
    if time.time() - (read_json(mark, {}) or {}).get("at", 0) < SPAWN_COOLDOWN:
        return False
    write_json(mark, {"at": time.time()})
    root = Path(__file__).resolve().parents[1]
    flags = {"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
    try:
        subprocess.Popen([sys.executable, str(root / "oqa.py"), "dashboard", "--ws", ws, "--session", session_id], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(root), **flags)
    except OSError:
        return False
    return True


def session_active(session):
    """True when the session was touched recently. Hooks installed globally see every project: an old session left unfinished must not hold them."""
    times = [f.stat().st_mtime for f in (session.dir / "state.json", session.dir / "events.jsonl") if f.exists()]
    return bool(times) and time.time() - max(times) < ACTIVE_SECONDS


def ensure_init_form(ws=None):
    """Start the setup form in the background (it opens in the user's browser) when no workspace is configured. True when one was started."""
    if os.environ.get("OQA_NO_OPEN"):
        return False
    mark = paths.home() / "init_spawn.json"
    if time.time() - (read_json(mark, {}) or {}).get("at", 0) < 120:        # the form waits for the user for minutes: do not open a second one
        return False
    write_json(mark, {"at": time.time()})
    root = Path(__file__).resolve().parents[1]
    flags = {"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
    try:
        subprocess.Popen([sys.executable, str(root / "oqa.py"), "init", *(["--ws", ws] if ws else [])], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(root), **flags)
    except OSError:
        return False
    return True


def ensure_prepare(ws):
    """Prepare the environment of a workspace in the background (sitemap, lines for every screen) when it has not been done. True when one was started."""
    if os.environ.get("OQA_NO_OPEN") or os.environ.get("OQA_NO_PREPARE"):
        return False
    from . import sitemap
    if sitemap.file_of(ws).exists():
        return False
    mark = paths.home() / f"prepare_{ws}.json"
    if time.time() - (read_json(mark, {}) or {}).get("at", 0) < 600:
        return False
    write_json(mark, {"at": time.time()})
    root = Path(__file__).resolve().parents[1]
    flags = {"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}
    try:
        subprocess.Popen([sys.executable, str(root / "oqa.py"), "prepare", "--ws", ws], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(root), **flags)
    except OSError:
        return False
    return True


def _beat_file(session):
    return session.dir / "logs" / "wait.json"


def beat(session):
    """`oqa wait` calls this while it runs."""
    write_json(_beat_file(session), {"at": time.time()})


def clear_beat(session):
    _beat_file(session).unlink(missing_ok=True)


def wait_running(session):
    seen = read_json(_beat_file(session), None)
    return bool(seen) and time.time() - seen.get("at", 0) < FRESH_SECONDS
