"""The ONE place that turns a session's files into "what is happening and what happened". The dashboard and the Excel report both read this, so
they can never disagree about a case, a step or a picture."""
import time

from . import analysis as analysis_mod
from . import captions, claims, dispatch_requests, events, generalize, knowledge, live, paths, record_names, refs, replays, workspace
from .state import MAX_REPAIRS, PHASES, Session
from .store import read_json

CLOSED = ("PASS", "FAIL", "PENDING", "BLOCKED", "NOT EXECUTED")
IDLE_HOURS = 6              # a session that is not finished and has written nothing for this long was most likely left behind
LIVE = ("RUNNING", "REPAIRING", "EXPLORING")
STALE_S = 600               # nothing written for this long: a case shown as working is not (the assistant or the runner is gone), so it is not shown as working
LIVE_S = 300                # a case whose own last event is more recent than this is being worked on, whoever else is working on other cases
TRY_S = 300                 # a `try` that started and never ended this long ago is not still running
WORK = ("queued", "try_started", "explore", "built", "case_started", "step", "attempt_finished")


def seconds_since(ts, now=None):
    try:
        return max(0, (now or time.time()) - time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")))
    except (ValueError, TypeError):
        return 0


def quiet_seconds(state, log, now=None):
    """Seconds since the session last wrote anything (0 when finished)."""
    if state["phase"] == "DONE":
        return 0
    return seconds_since(log[-1]["ts"] if log else state["created"], now)


def idle_hours(state, log, now=None):
    """Hours since the session last did anything, or 0 when it is finished (a closed session is not idle, it is done)."""
    if state["phase"] == "DONE":
        return 0
    last = log[-1]["ts"] if log else state["created"]
    try:
        return max(0, ((now or time.time()) - time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))) / 3600)
    except ValueError:
        return 0


def is_active(session):
    """True while something wrote to the session in the last two minutes: a delete then would pull the files from under a working agent."""
    log = events.read(session.dir)
    return bool(log) and seconds_since(log[-1]["ts"]) < 120


def interrupted(records, hours, quiet_s=0):
    """In an idle session (or one that wrote nothing for STALE_S) a case shown as working is not: nobody is working on it. The report is not changed by this,
    only what the dashboard shows, so a spinner on the page always means something is really running."""
    if hours >= IDLE_HOURS or quiet_s >= STALE_S:
        for r in records:
            if r["status"] in LIVE:
                r["status"] = "INTERRUPTED"
            r.get("explore", {})["trying"] = None
            for a in r.get("attempts", []):
                if a["status"] == "RUNNING":
                    a["status"] = "INTERRUPTED"
                for s in a["steps"]:
                    if s["status"] == "RUNNING":
                        s["status"] = "PENDING"
    return records


def _steps(planned, ran, carried=None, running=False):
    """Every step the analysis planned, with what the attempt did to it; a step that never ran is PENDING (or SKIPPED when the attempt stopped earlier).
    A run that picked up at a later step did not run the steps before it: those show what an earlier run did, marked `carried`."""
    by_n = {e["n"]: e for e in ran}
    out, going = [], running
    for step in planned:
        e = by_n.get(step["n"])
        kept = None if e else (carried or {}).get(step["n"])
        e = e or kept
        now_running = going and not e                 # the attempt is on: the first step with no result yet is the one being run
        going = going and not now_running
        actual = e["actual"] if e else ""
        if actual and actual == step.get("actual_source"):
            actual = step.get("actual", actual)
        out.append({"n": step["n"], "caption": step["action"], "expected": step["expected"], "evidence": step.get("evidence", ""), "status": e["status"] if e else ("RUNNING" if now_running else "PENDING"),
                    "actual": actual, "images": e["images"] if e else [], "checks": e.get("checks") if e else None, "carried": bool(kept)})
    return out


def _explore(session, ident, mine, current, state, log):
    """Where the case is before it is run: which steps ran well on the real page (`verified`), the step being tried this very moment (`trying`, only between
    an `oqa try` starting and ending), whether the script is built and waits for a run (`built`), and whether the assistant is working on this case now (`live`)."""
    verified = []
    marks = [(i, e) for i, e in enumerate(mine) if e["kind"] in ("queued", "try_started", "explore", "built")]
    last_try = max((i for i, e in marks if e["kind"] in ("queued", "try_started", "explore")), default=-1)
    last_built = max((i for i, e in marks if e["kind"] == "built"), default=-1)
    trying = None
    if marks and marks[-1][1]["kind"] == "try_started" and seconds_since(marks[-1][1]["ts"]) < TRY_S and not state.get("stopped"):
        trying = {"step": marks[-1][1]["step"], "mode": marks[-1][1].get("mode", "")}
    stage = ""                                                 # what a live case is doing: running a try, waiting for its turn in the runner, or between two tries
    if marks and not state.get("stopped") and seconds_since(marks[-1][1]["ts"]) < TRY_S:
        stage = {"try_started": "running", "queued": "queued", "explore": "between"}.get(marks[-1][1]["kind"], "")
    built = last_built >= 0 and last_built > last_try          # an explore after the build means the case is being explored again
    started = any(e["kind"] == "case_started" for e in mine)
    # several assistants can work on different cases of one session at once: a case is live when ITS OWN last event is recent (not only the one touched last)
    live = bool(marks) and not built and not started and not state.get("stopped") and state["phase"] == "EXECUTE" and seconds_since(mine[-1]["ts"]) < LIVE_S
    queued = marks[-1][1] if stage == "queued" else None
    return {"verified": verified, "trying": trying, "built": built, "live": live, "stage": stage if live else "", "queued_step": (queued or {}).get("step")}


def _latest_video(slot):
    """The newest replay run of the case that kept a video (a later run without one does not hide it): {video, at, status, frame_ms} or None."""
    for run in reversed((slot or {}).get("runs", [])):
        if run.get("video"):
            return {"video": run["video"], "at": run.get("at", ""), "status": run.get("status", ""), "env": run.get("env", "main")}
    return None


def _mtime(path):
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def worker_load(records):
    """How many case workers are on the session right now: a case with a live claim, or one the runner is working on. The limit is the claim limit."""
    active = [r["id"] for r in records if r["owner_fresh"] or r["status"] in LIVE]
    return {"active": len(active), "limit": claims.MAX_WORKERS, "cases": active}


def case_records(session: Session):
    """One record per case of the workbook, in the order the strategy runs them (cases held back come after)."""
    cases = session.cases()["cases"]
    analysis = {c["id"]: c for c in (read_json(session.dir / "analysis.json") or {"cases": []})["cases"]}
    strategy = read_json(session.dir / "strategy.json") or {"order": [], "excluded": []}
    state = session.state()
    log = events.read(session.dir)
    held = {e["id"]: e for e in strategy["excluded"]}
    deferred = set(strategy.get("deferred", []))
    by_id = {c["id"]: c for c in cases}
    ordered = [i for i in strategy["order"] if i in by_id]
    ordered += [i for i in analysis if i not in ordered and i in by_id] if analysis else []
    ordered += [c["id"] for c in cases if c["id"] not in ordered]
    out = []
    owners = claims.table(session)
    saved_replays = replays.load(session)
    asked = dispatch_requests.load(session)
    translations = captions.localized_cases(session)
    worked = [e for e in log if e["kind"] in WORK and e.get("case")]
    current = worked[-1]["case"] if worked else None          # the case the assistant touched last: the one being worked on, if any is
    for ident in ordered:
        case, item = by_id[ident], analysis.get(ident, {})
        translated = translations.get(ident, {})
        planned = captions.localized_steps(item.get("steps") or [], translated)
        mine = [e for e in log if e.get("case") == ident]
        attempts = []
        for n in sorted({e["attempt"] for e in mine if e["kind"] in ("case_started", "step", "attempt_finished")}):
            started = max((i for i, e in enumerate(mine) if e["kind"] == "case_started" and e["attempt"] == n), default=-1)
            # a run the user stopped and a later run of the same attempt number: only the end that comes after the latest start counts
            ended = next((e for i, e in reversed(list(enumerate(mine))) if e["kind"] == "attempt_finished" and e["attempt"] == n and i > started), None)
            from_step = mine[started].get("from_step", 1) if started >= 0 else 1
            carried = {}
            if from_step > 1:                         # the steps before it passed in an earlier run: the latest passing record of each
                for e in mine[:started]:
                    if e["kind"] == "step" and e["status"] == "PASS" and e["n"] < from_step:
                        carried[e["n"]] = e
            ran = [{**e, "images": list(e.get("images", []))} for e in mine if e["kind"] == "step" and e["attempt"] == n]
            for supplement in (e for e in mine if e["kind"] == "step_evidence" and e["attempt"] == n):
                for recorded in ran:
                    if recorded["n"] == supplement["n"]:
                        recorded["images"].extend(supplement["images"])
            attempts.append({"n": n, "status": ended["status"] if ended else ("STOPPED" if state.get("stopped") else "RUNNING"), "reason": ended["reason"] if ended else "", "failed_step": ended.get("failed_step") if ended else None,
                             "from_step": from_step,
                             "steps": _steps(planned or [{"n": i, "action": s, "expected": ""} for i, s in enumerate(case["steps"], 1)],
                                             ran, carried, running=not ended and not state.get("stopped"))})
        explore = _explore(session, ident, mine, current, state, log)
        result = state["results"].get(ident)
        if ident in state.get("skipped_reviews", {}):
            status = "NOT EXECUTED"
        elif result:
            status = result["status"]
        elif attempts:
            status = {"RUNNING": "RUNNING", "STOPPED": "STOPPED"}.get(attempts[-1]["status"], "REPAIRING")
        elif ident in strategy["order"]:
            status = "EXPLORING" if explore["live"] else ("BUILT" if explore["built"] else "PLANNED")
        elif ident in deferred:
            status = "DEFERRED"
        elif ident in held:
            status = "HELD"
        else:
            status = "NEW"
        if state.get("stopped") and status in LIVE:
            status = "STOPPED"                                 # the user stopped the test: no case is shown as running, repairing or exploring
        repairs = [{k: e[k] for k in ("ts", "attempt", "cause", "evidence", "change", "reanalysis", "probed", "timeout_s", "unchanged_reason") if k in e} for e in mine if e["kind"] == "repair"]
        generalized = next((generalize.effective({k: e.get(k) for k in ("status", "attempt", "note", "ts")}) for e in reversed(mine) if e["kind"] == "script_generalized"), None)
        saved = (saved_replays.get(ident) or {}).get("latest")
        reason = (result or {}).get("reason") or held.get(ident, {}).get("reason", "")
        if reason and reason == translated.get("reason_source"):
            reason = translated.get("reason", reason)
        out.append({"id": ident, "live_status": status, "replay_status": (saved or {}).get("status", replays.NOT_RUN), "replay": saved, "replay_video": _latest_video(saved_replays.get(ident)), "script_at": _mtime(live.script_file(session, ident)), "history": (state.get("history") or {}).get(ident, []), "category": (result or {}).get("category", ""), "can_replay": live.script_file(session, ident).is_file(), "generalized": generalized, "title": record_names.title(case, item, translated), "row": case["row"], "reuse": item.get("reuse", ""), "repairs": repairs, "timeout_s": state.get("timeouts", {}).get(ident) or item.get("timeout_s"),
                    "source": {"sheet": case.get("sheet", ""), "original_id": case.get("original_id", case["id"]), "category": case.get("category", ""), "type": case.get("type", ""), "pre": case.get("pre", ""), "steps": case.get("steps", []),
                              "expected": case.get("expected", ""), "note": case.get("note", ""), "role": case.get("role", "")}, "profile": item.get("profile", ""), "verdict": item.get("verdict", held.get(ident, {}).get("verdict", "")),
                    "request": dispatch_requests.of_case(session, ident, asked), "can_request": dispatch_requests.kind_for(session, ident, state), "last_ts": mine[-1]["ts"] if mine else "", "last_kind": mine[-1]["kind"] if mine else "", "explore": explore, "owner": (owners.get(ident) or {}).get("agent", ""), "owner_fresh": bool((owners.get(ident) or {}).get("fresh")), "status": status, "reason": reason, "attempts": attempts,
                    "planned": [{"n": s["n"], "caption": s["action"], "expected": s["expected"], "evidence": s.get("evidence", "")} for s in planned],
                    "depends_on": item.get("depends_on") or [], "edge_cases": item.get("edge_cases") or [], "edge_none": item.get("edge_cases_none_reason", ""),
                    "data_needs": item.get("data_needs") or [], "clarity": (item.get("clarity") or {}).get("score"), "sufficiency": (item.get("sufficiency") or {}).get("score"),
                    "questions": item.get("questions") or [],
                    "review_pending": ident in state.get("reviews", {}),
                    "review_skipped": state.get("skipped_reviews", {}).get(ident, ""),
                    "hints": (state.get("hints") or {}).get(ident, []), "used": state["attempts"].get(ident, 0),
                    "exhausted": state["attempts"].get(ident, 0) >= 1 + (1 if state.get("channel") else MAX_REPAIRS),
                    "can_hint": state["phase"] in ("EXECUTE", "REPORT") and ident in strategy["order"] and status != "PASS" and bool(attempts or result or state["attempts"].get(ident))})
    return out


def selection_view(session, state, phase):
    """What the dashboard needs to let the user tick the cases of the run (before approval) and to add the ones left out (after)."""
    strategy = read_json(session.dir / "strategy.json") or {"order": []}
    left = strategy.get("deferred", [])
    if phase != "CONFIRM" and not left:                      # nothing to tick and nothing left out: do not read the analysis and the cases on every poll
        return {"ready": [], "selected": strategy["order"], "deferred": [], "can_select": False, "can_add": False}
    doc = read_json(session.dir / "analysis.json") or {"cases": []}
    titles = {c["id"]: c.get("title", "") for c in session.cases()["cases"]}
    ready = [{"id": c["id"], "title": c.get("title") or titles.get(c["id"], ""), "depends_on": c.get("depends_on") or []} for c in doc["cases"] if c["verdict"] == "ready"]
    selected = strategy["order"] if strategy["order"] else [c["id"] for c in ready]
    return {"ready": ready, "selected": selected, "deferred": left,
            "can_select": phase == "CONFIRM" and len(ready) > 1,
            "can_add": phase in ("EXECUTE", "REPORT", "DONE") and bool(left) and not state.get("stopped") and not state["waiting"]}


def counts(records):
    out = {}
    for r in records:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def replay_counts(records):
    """Replay PASS / FAIL / NOT RUN over the cases that were run live (a case without a script is NOT RUN)."""
    out = {}
    for r in records:
        if r["live_status"] in ("PASS", "FAIL"):
            out[r["replay_status"]] = out.get(r["replay_status"], 0) + 1
    return out


def reports(session: Session):
    """The Excel evidence workbook written so far (one per session), or none."""
    folder = session.dir / "report"
    main = sorted(folder.glob("Result_*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True) if folder.is_dir() else []     # the testcase file as written back
    main = main or (sorted(folder.glob("Evidence_*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True) if folder.is_dir() else [])
    return [{"name": f.name, "size": f.stat().st_size, "main": True} for f in main[:1]]


FEED = ("queued", "try_started", "explore", "built", "case_started", "attempt_finished", "repair")


def activity(log, limit=40):
    """What the assistants did last, newest first, over every case: several agents can work on one session at the same time, so the dashboard shows one feed of who
    touched which case and what happened, instead of only the case that happened to be running when the page was drawn."""
    out = []
    for e in reversed(log):
        if e.get("kind") not in FEED or not e.get("case"):
            continue
        out.append({"ts": e.get("ts", ""), "case": e["case"], "kind": e["kind"], "step": e.get("step"), "ok": e.get("ok"), "mode": e.get("mode", ""),
                    "code": (e.get("code") or "")[:160], "error": (e.get("error") or e.get("reason") or "")[:200], "status": e.get("status", ""),
                    "cause": (e.get("cause") or "")[:200]})
        if len(out) >= limit:
            break
    return out


def session_view(session: Session):
    state = session.state()
    replay_language = workspace.load(session.ws).get("replay_caption_language", "vi")
    log = events.read(session.dir)
    hours = idle_hours(state, log)
    records = interrupted(case_records(session), hours, quiet_seconds(state, log))
    current = state["phase"]
    reached = {d["phase"]: d["at"] for d in state["done"]}
    here = PHASES.index(current)
    phases = [{"phase": p, "at": reached.get(p, ""), "state": "current" if p == current else ("done" if i < here else "todo")} for i, p in enumerate(PHASES)]
    latest = next((e for e in reversed(log) if e["kind"] == "phase"), None)
    strategy_md = session.dir / "strategy.md"
    conflict_list = analysis_mod.conflicts(read_json(session.dir / "analysis.json"), state.get("conflict_choices"))
    selection = selection_view(session, state, current)
    return {"selection": selection, "conflicts": conflict_list, "conflicts_pending": sum(1 for c in conflict_list if not c["choice"]), "workspace": session.ws, "session": session.id, "replay_caption_language": replay_language, "file": state["file"], "phase": current, "phases": phases, "waiting": state["waiting"], "stopped": state.get("stopped"), "can_stop": current != "DONE" and not state.get("stopped"),
            "confirmed": bool(state["confirmation"]), "created": state["created"], "updated": log[-1]["ts"] if log else state["created"], "idle_hours": round(hours, 1), "idle": hours >= IDLE_HOURS, "now": latest["text"] if latest else "",
            "counts": counts(records), "workers": worker_load(records), "replay_counts": replay_counts(records), "total": len(records), "cases": records, "activity": activity(log), "free_cases": claims.free_cases(session) if current == "EXECUTE" else [], "notes": claims.notes(session, 12), "connect": state.get("connect"),
            "reports": reports(session), "strategy": strategy_md.read_text(encoding="utf-8") if strategy_md.exists() else "", "can_confirm": current == "CONFIRM" and bool(state.get("strategy_sha")) and (session.dir / "strategy.json").exists()
            and state["strategy_sha"] is not None, "knowledge": len(knowledge.search(session.ws)),
            "refs": [{k: i.get(k) for k in ("name", "path", "kind", "by", "case")} for i in refs.items(session)]}


def list_sessions():
    """Every session of every workspace, newest first, with just enough to choose one."""
    out = []
    for ws in paths.workspace_names():
        for sid in paths.session_ids(ws):
            try:
                session = Session(ws, sid)
                state = session.state()
                hours = idle_hours(state, events.read(session.dir))
                records = interrupted(case_records(session), hours, quiet_seconds(state, events.read(session.dir)))
                out.append({"workspace": ws, "session": sid, "file": state["file"], "created": state["created"], "phase": state["phase"], "waiting": bool(state["waiting"]),
                            "idle": hours >= IDLE_HOURS, "idle_hours": round(hours, 1), "stopped": bool(state.get("stopped")), "reviews": len(state.get("reviews") or {}), "can_confirm": state["phase"] == "CONFIRM" and bool(state.get("strategy_sha")), "conflicts_pending": len(analysis_mod.pending_conflicts(read_json(session.dir / "analysis.json"), state.get("conflict_choices"))), "counts": counts(records), "total": len(records),
                            "workers": worker_load(records)["active"]})
            except Exception:
                continue                    # a session folder that is being written or damaged must not hide the others
    return sorted(out, key=lambda s: s["created"], reverse=True)
