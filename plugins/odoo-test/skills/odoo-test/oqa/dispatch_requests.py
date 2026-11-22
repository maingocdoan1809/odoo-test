"""What the user asks of the coordinator from the dashboard: "dispatch this case" (it waits for a worker) or "hand this case over again" (its worker went quiet).

A request is only a note in the session: one per case, kept in dispatch_requests.json. It does not take a worker slot and starts no worker. The coordinator (`oqa run`, or a host
agent that reads `oqa notes` / `oqa claim`) picks requested cases first, re-checks everything at that moment (slot, who holds the case, the case it depends on, the approved scope) and
writes the outcome back here, so the dashboard can say at the case whether a worker took it, why it has to wait, or why it was refused.

dispatch_requests.json = {case: {kind dispatch|reassign, status pending|accepted|waiting|rejected, at, note, worker, failure}}
`failure` is what the last worker said when it stopped without a conclusion: shown beside the case result, never as the result."""
import time

from . import claims, events
from .state import GateError
from .store import locked, read_json, write_json

KINDS = {"dispatch": "Yêu cầu điều phối", "reassign": "Yêu cầu giao lại"}
OPEN = ("pending", "waiting")


def _file(session):
    return session.dir / "dispatch_requests.json"


def load(session):
    return read_json(_file(session), {}) or {}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def kind_for(session, case, state=None, log=None):
    """What the user may ask for on this case right now: dispatch, reassign, or None (it has a result, someone works on it, or the session cannot take work)."""
    state = state or session.state()
    if state["phase"] != "EXECUTE" or state.get("stopped") or state.get("waiting") or case in state["results"]:
        return None
    held = claims.table(session).get(case)
    if held and held["fresh"]:
        return None
    return "reassign" if held or state["attempts"].get(case) else "dispatch"


def slots(session):
    """(taken, limit): the workers that hold a case now. The same count limits automatic dispatch and the user's requests."""
    taken = sum(1 for entry in claims.table(session).values() if entry["fresh"])
    return taken, claims.MAX_WORKERS


def submit(session, case, by="dashboard"):
    """Record the user's request. Refuses what can never work now (stopped session, a result already exists, a live worker holds the case, no free slot), and answers at once
    instead of silently queueing. Asking twice for the same case keeps the one pending request."""
    with locked(_file(session)):
        state = session.state()
        order = (read_json(session.dir / "strategy.json", {}) or {}).get("order", [])
        if case not in order:
            raise GateError(f"{case} is not in the approved scope.")
        if state["phase"] != "EXECUTE":
            raise GateError(f"The session is in {state['phase']}: cases are dispatched only while it executes.")
        if state.get("stopped"):
            raise GateError("The test is stopped. Resume it first.")
        if state.get("waiting"):
            raise GateError("The session is waiting for your answer. Answer first.")
        if case in state["results"]:
            raise GateError(f"{case} already has a result ({state['results'][case]['status']}).")
        held = claims.table(session).get(case)
        if held and held["fresh"]:
            raise GateError(f"{held['agent']} is working on {case}.")
        data = load(session)
        if (data.get(case) or {}).get("status") in OPEN:
            return data[case], False
        taken, limit = slots(session)
        if taken >= limit:
            raise GateError(f"All {limit} case worker slots are occupied ({taken}/{limit}). Ask again when one is free.")
        kind = kind_for(session, case, state)
        entry = {"kind": kind, "status": "pending", "at": _now(), "by": by, "note": "", "worker": ""}
        data[case] = {**data.get(case, {}), **entry}
        write_json(_file(session), data)
    events.append(session.dir, "dispatch_requested", case=case, request=kind, by=by)
    claims.add_note(session, "user", f"{KINDS[kind]} for case {case}: take it with the next free worker (oqa claim).", case=case)
    return data[case], True


def why_not_free(session, case):
    """Why a requested case cannot start this moment (the coordinator re-checks at the time it looks), or "" when it can."""
    from . import claims as _claims
    import json as _json

    state = session.state()
    strategy = read_json(session.dir / "strategy.json", {}) or {"order": []}
    if case not in strategy["order"]:
        return "reject: the case is not in the approved scope."
    if case in state["results"]:
        return f"reject: the case already has a result ({state['results'][case]['status']})."
    held = _claims.table(session).get(case)
    if held and held["fresh"]:
        return f"wait: {held['agent']} already works on it."
    analysis = {c["id"]: c for c in (read_json(session.dir / "analysis.json", {"cases": []}) or {"cases": []})["cases"]}
    for parent in (analysis.get(case) or {}).get("depends_on") or []:
        if state["results"].get(parent, {}).get("status") != "PASS":
            return f"wait: it continues from {parent}, which has not passed yet."
    taken, limit = slots(session)
    if taken >= limit:
        return f"wait: all {limit} worker slots are occupied ({taken}/{limit})."
    return ""


def pending(session):
    return [case for case, entry in load(session).items() if entry.get("status") in OPEN]


def resolve(session, case, status, note="", worker=""):
    """The coordinator's answer: accepted (worker named), waiting (why it cannot start yet), rejected (why it never can)."""
    with locked(_file(session)):
        data = load(session)
        if case not in data:
            return
        data[case] = {**data[case], "status": status, "note": note, "worker": worker, "resolved_at": _now()}
        write_json(_file(session), data)
    events.append(session.dir, "dispatch_resolved", case=case, status=status, **({"worker": worker} if worker else {}), **({"note": note} if note else {}))


def note_failure(session, case, message):
    """The worker stopped without a conclusion: keep what it said next to the case, apart from the case result."""
    with locked(_file(session)):
        data = load(session)
        data[case] = {**data.get(case, {}), "failure": {"at": _now(), "message": str(message)[:600]}}
        write_json(_file(session), data)


def of_case(session, case, data=None):
    entry = (data if data is not None else load(session)).get(case) or {}
    return {"kind": entry.get("kind", ""), "status": entry.get("status", ""), "note": entry.get("note", ""), "worker": entry.get("worker", ""),
            "at": entry.get("at", ""), "failure": entry.get("failure")}
