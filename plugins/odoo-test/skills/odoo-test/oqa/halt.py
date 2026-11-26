"""What "Stop" really does: end the work of ONE session, and nothing else of the user's machine.

The flag in state.json only refuses new work. This module also stops what already runs: the Playwright daemon of each case with its Chromium, and it gives every claim back. A process belongs to the session only when its command line holds the session
folder or an `oqa-<session id>-` browser name, so another session's browser, or one the user opened, is never touched.

Every case that started a run and has not finished it gets `attempt_finished` with status STOPPED. The run counters are not changed, so a resume queues the
case again with the attempts it already used."""
import os
import signal
import subprocess
import time
from pathlib import Path

from . import events

GRACE_S = 3


def open_attempts(session):
    """{case: attempt} for every case whose latest `case_started` has no `attempt_finished` after it."""
    log = events.read(session.dir)
    started = {}
    for index, event in enumerate(log):
        if event["kind"] == "case_started" and event.get("case"):
            started[event["case"]] = (event["attempt"], index)
    out = {}
    for case, (attempt, index) in started.items():
        closed = any(e["kind"] == "attempt_finished" and e.get("case") == case and e.get("attempt") == attempt for e in log[index:])
        if not closed:
            out[case] = attempt
    return out


def close_open_attempts(session, reason):
    """Write `attempt_finished` STOPPED for every open run. Safe to call again: a closed run is not written twice."""
    done = []
    for case, attempt in open_attempts(session).items():
        events.append(session.dir, "attempt_finished", case=case, attempt=attempt, status="STOPPED", reason=reason)
        done.append(case)
    return done


# --- processes ------------------------------------------------------------------------------------------------------------------------
def _table():
    """{pid: (ppid, command line)} of every process we are allowed to see. Linux reads /proc; elsewhere psutil, when installed; otherwise nothing is found."""
    out = {}
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
                out[int(entry.name)] = (int(stat.rsplit(")", 1)[1].split()[1]), command)
            except (OSError, ValueError, IndexError):
                continue
        return out
    try:
        import psutil
    except ImportError:
        return _windows_table() if os.name == "nt" else out
    for item in psutil.process_iter(["pid", "ppid", "cmdline"]):
        try:
            out[item.info["pid"]] = (item.info["ppid"] or 0, " ".join(item.info["cmdline"] or []))
        except (psutil.Error, TypeError):
            continue
    return out


def _windows_table():
    """Windows without psutil: the process list from CIM, through PowerShell (always there on Windows)."""
    import json
    script = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        rows = json.loads(done.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}
    rows = [rows] if isinstance(rows, dict) else rows
    return {int(row["ProcessId"]): (int(row.get("ParentProcessId") or 0), row.get("CommandLine") or "") for row in rows if row.get("ProcessId")}


def _mine(session, command):
    """True when a command line belongs to this session's workers or browsers."""
    return str(session.dir) in command or f"oqa-{session.id}-" in command


def session_processes(session, table=None):
    """The pids of this session's workers, daemons and browsers, children included. The current process and its parents are never in the list."""
    table = table if table is not None else _table()
    safe, pid = set(), os.getpid()
    while pid and pid not in safe:
        safe.add(pid)
        pid = table.get(pid, (0, ""))[0]
    found = {pid for pid, (_, command) in table.items() if pid not in safe and _mine(session, command)}
    grew = True
    while grew:                                                # a browser started by a daemon has a plain command line: take it as a child
        grew = False
        for pid, (parent, _) in table.items():
            if parent in found and pid not in found and pid not in safe:
                found.add(pid)
                grew = True
    return sorted(found)


def _alive(pid):
    if os.name == "nt":                                        # os.kill(pid, 0) on Windows sends CTRL_C_EVENT; ask the process handle instead
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259     # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:                                                       # a zombie is already gone
        return (Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()[0]) != "Z"
    except (OSError, IndexError):
        return True


def kill_processes(pids):
    """Ask each process to end, give it GRACE_S seconds, then force it. Returns the pids that were running."""
    pids = [pid for pid in pids if _alive(pid)]
    if os.name == "nt":
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return pids
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + GRACE_S
    while time.monotonic() < deadline and any(_alive(pid) for pid in pids):
        time.sleep(0.1)
    for pid in pids:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return pids


def _close_browsers(session, cases):
    """The polite way first: ask playwright-cli to close each case's own browser (a case's name is known, so nothing else is closed)."""
    from . import live
    from .state import GateError

    for case in cases:
        try:
            live._run(live.browser_case(session, case), "close", timeout=15)
        except (GateError, OSError, subprocess.TimeoutExpired):
            pass


# --- the whole thing ------------------------------------------------------------------------------------------------------------------
def stop_work(session, reason="Stopped by the user"):
    """End everything the session is running and put the open runs on record as STOPPED. Called by `Session.stop` after the flag is set."""
    from . import claims

    cases = open_attempts(session)
    _close_browsers(session, cases)
    killed = kill_processes(session_processes(session))
    closed = close_open_attempts(session, reason)               # after the kill: nobody is left to write another event for these runs
    held = claims.release_all(session)
    return {"closed": closed, "killed": len(killed), "released": held}
