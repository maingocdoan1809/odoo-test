"""Replay results, kept apart from the live result of a case.

A live run is the evidence that a case passed or failed in the application. A replay only says whether the saved script still reproduces that run. The two never
share a field: replay.json lives beside state.json, `state.results` is never touched, and a case that has not been replayed is NOT RUN (never an implied pass).

replay.json = {schema_version, cases: {case: {latest: {...}, runs: [{...}]}}}
Each run: {status PASS|FAIL, env, at, run_id, reason, failed_step, headed, action_delay_ms, step_delay_ms, fast, timing}
timing = {total_s, pause_s, call_s, calls, steps: [{n, actions_s, check_s, pause_s, calls}]}: where a replay spent its time, so a slow replay is measured, not assumed."""
import time

from . import SCHEMA_VERSION
from .store import locked, read_json, write_json

NOT_RUN = "NOT RUN"
KEEP_RUNS = 20


def _file(session):
    return session.dir / "replay.json"


def load(session):
    return (read_json(_file(session), {}) or {}).get("cases", {})


def record(session, case, status, **run):
    """Keep one replay run. `env` (default main) says where it ran: the session's own `latest` is only the main environment's, so the Replay column of a
    session never shows another system's result; every environment also lands in the workspace's regression.json."""
    if status not in ("PASS", "FAIL"):
        raise ValueError("A replay is PASS or FAIL; a case never replayed is NOT RUN.")
    env = run.get("env") or "main"
    entry = {"status": status, "at": time.strftime("%Y-%m-%d %H:%M:%S"), **run, "env": env}
    with locked(_file(session)):
        data = read_json(_file(session), {}) or {"schema_version": SCHEMA_VERSION, "cases": {}}
        slot = data["cases"].setdefault(case, {"runs": []})
        slot["runs"] = [*slot["runs"], entry][-KEEP_RUNS:]
        slot["envs"] = {**slot.get("envs", {}), env: entry}
        if env == "main":
            slot["latest"] = entry
        write_json(_file(session), data)
    from . import regression

    state = session.state()
    regression.record(session.ws, regression.key_of(state["file"], case), env, entry)
    return entry


def latest(session, case):
    return (load(session).get(case) or {}).get("latest")


def status(session, case):
    found = latest(session, case)
    return found["status"] if found else NOT_RUN


def counts(session, cases):
    out = {}
    saved = load(session)
    for case in cases:
        label = (saved.get(case) or {}).get("latest", {}).get("status", NOT_RUN)
        out[label] = out.get(label, 0) + 1
    return out
