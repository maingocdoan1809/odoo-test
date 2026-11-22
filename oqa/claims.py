"""Who is working on which case, and what the agents tell each other: one shared place in the session, so nobody misses anything and no case is left without a worker.

A claim is a lease: the agent keeps a case while it keeps working on it (any event on the case renews it). A case whose claim has gone quiet for LEASE_S is free again, so an
agent that died or stalled never holds a case hostage. A floating agent simply claims whatever is free (`oqa claim`) until nothing is left."""
import json
import time

from . import events, textfix
from .state import GateError
from .store import locked, read_json, write_json

LEASE_S = 600
MAX_WORKERS = 5
HEAVY_WORDS = ("batch", "cron", "mail", "import", "export", "archive", "user", "contract", "company", "upload")


def _file(session):
    return session.dir / "claims.json"


def _now():
    return time.time()


def _last_activity(log, case):
    """Epoch seconds of the last event of the case (0 when none)."""
    for e in reversed(log):
        if e.get("case") == case and e.get("kind") in ("case_started", "step", "explore", "repair"):
            try:
                return time.mktime(time.strptime(e["ts"], "%Y-%m-%d %H:%M:%S"))
            except (ValueError, KeyError):
                return 0
    return 0


def _difficulty(item):
    text = " ".join(f"{s.get('action', '')} {s.get('expected', '')}" for s in item.get("steps") or []).lower()
    return len(item.get("steps") or []) + 2 * len(item.get("data_needs") or []) + len(item.get("edge_cases") or []) + min(3, sum(1 for w in HEAVY_WORDS if w in text))


def _fresh(entry, log, case):
    return _now() - max(entry["at"], _last_activity(log, case)) < LEASE_S


def _unclaimed_activity(log, case):
    last = next((event["kind"] for event in reversed(log) if event.get("case") == case
                 and event["kind"] in ("case_started", "step", "attempt_finished", "explore", "handover")), None)
    return last not in (None, "attempt_finished", "handover") and _now() - _last_activity(log, case) < LEASE_S


def table(session):
    """{case: {agent, at, fresh}} for the claims that still count (the case has no result yet)."""
    state, log = session.state(), events.read(session.dir)
    out = {}
    for case, entry in (read_json(_file(session), {}) or {}).items():
        if case not in state["results"]:
            out[case] = {**entry, "fresh": _fresh(entry, log, case)}
    return out


def free_cases(session):
    """The cases nobody works on, easy and independent first; cases that need prepared data come after the ones that need none."""
    state = session.state()
    strategy = read_json(session.dir / "strategy.json") or {"order": []}
    analysis = {c["id"]: c for c in (read_json(session.dir / "analysis.json") or {"cases": []})["cases"]}
    held, log = table(session), events.read(session.dir)
    todo = [c for c in strategy["order"] if c not in state["results"] and c in analysis]
    ready = []
    for c in todo:
        if c in held and held[c]["fresh"]:
            continue
        if c not in held and _unclaimed_activity(log, c):
            continue                                              # someone is already working on it, even without having claimed it
        if any(d not in state["results"] or state["results"][d]["status"] != "PASS" for d in analysis[c].get("depends_on") or []):
            continue                                              # its parent has not passed yet
        ready.append(c)
    from . import dispatch_requests

    asked = set(dispatch_requests.pending(session))               # what the user asked for on the dashboard goes first; it takes no slot until a worker claims it
    return sorted(ready, key=lambda c: (c not in asked, bool(analysis[c].get("data_needs")), _difficulty(analysis[c])))


def claim(session, agent, want=None):
    """Give `agent` the next free case (or tell it which one it already holds). Returns (case, note) with case None when nothing is free."""
    if not agent:
        raise GateError("Say who you are: oqa claim --agent <name>.")
    with locked(_file(session)):
        session.require("EXECUTE")
        data = read_json(_file(session), {}) or {}
        state, log = session.state(), events.read(session.dir)
        active = {c: e for c, e in data.items() if c not in state["results"] and _fresh(e, log, c)}
        mine = [c for c, e in active.items() if e["agent"] == agent]
        if mine:
            return mine[0], f"{agent} already holds case {mine[0]}: finish it (oqa finish / oqa run) or release it (oqa release {mine[0]}) before claiming another."
        if len(active) >= MAX_WORKERS:
            return None, f"All {MAX_WORKERS} case worker slots are occupied."
        free = free_cases(session)
        if want:                                                  # an agent asks for the case it was given: it gets it only when nobody else works on it
            if want not in free:
                owner = (table(session).get(want) or {}).get("agent")
                return None, f"Case {want} is not free" + (f": {owner} holds it. Do not start it; take another with `oqa claim --agent {agent}`." if owner else " (it has a result, waits for the case it depends on, or someone is working on it now).")
            free = [want]
        if not free:
            waiting = [c for c, e in table(session).items() if e["fresh"]]
            return None, "No free case. " + (f"Being worked on: {', '.join(waiting)}." if waiting else "Every case has a result.")
        case = free[0]
        data[case] = {"agent": agent, "at": _now()}
        write_json(_file(session), data)
        return case, f"{agent} now owns case {case}. Read the shared notes first: oqa notes"


def release(session, case, missing_ok=False):
    with locked(_file(session)):
        data = read_json(_file(session), {}) or {}
        if case not in data:
            if missing_ok:
                return
            raise GateError(f"Case {case} is not claimed.")
        del data[case]
        write_json(_file(session), data)


def assign(session, case, agent):
    """Give a case straight to a named agent (a handover): the lease starts now."""
    if not agent:
        raise GateError("Name the worker that takes the case.")
    with locked(_file(session)):
        data = read_json(_file(session), {}) or {}
        state, log = session.state(), events.read(session.dir)
        active = {c: e for c, e in data.items() if c not in state["results"] and _fresh(e, log, c) and c != case}
        if len(active) >= MAX_WORKERS:
            raise GateError(f"All {MAX_WORKERS} case worker slots are occupied.")
        data[case] = {"agent": agent, "at": _now()}
        write_json(_file(session), data)


def add_note(session, agent, text, case=""):
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": agent or "?", "case": case, "text": textfix.repair_text(text.strip())}
    with locked(_file(session).with_name("notes.lock")):
        with open(session.dir / "notes.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def notes(session, limit=30):
    path = session.dir / "notes.jsonl"
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(textfix.repair(json.loads(raw)))
        except ValueError:
            continue
    return out[-limit:]
