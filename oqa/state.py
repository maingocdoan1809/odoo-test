"""The session state machine. Every command that does work asks `Session.require(...)` first, and a refusal (GateError) names the command that
comes next. This is where the process is enforced: hooks only stop shortcuts around it.

    ANALYZE -> CONNECT -> CONFIRM -> EXECUTE -> REPORT -> DONE        (INTAKE happens inside `session new`)

`waiting` is separate from the phase: while the assistant has asked the user something, nothing but `answer` / `status` / `ask` goes through.
"""
import difflib
import os
import re
import time
from contextlib import contextmanager

from . import SCHEMA_VERSION, analysis, events, memory, paths, refs
from .store import SchemaError, locked, read_json, write_json

PHASES = ("ANALYZE", "CONNECT", "CONFIRM", "EXECUTE", "REPORT", "DONE")
MAX_REPAIRS = 3          # after the first run of a case, at most this many repair-and-rerun rounds
MIN_NOTE = 12            # characters a repair note's cause / evidence / change must have to say anything
SAME_CAUSE = 0.85        # a cause this close to an earlier one for the same case is the same cause
NEXT = {
    "ANALYZE": "oqa analyze --template > analysis.json, fill it in, then oqa analyze --submit analysis.json",
    "CONNECT": "oqa connect",
    "CONFIRM": "oqa strategy, then ask the user to approve it (the user runs oqa confirm, or presses Confirm in the dashboard)",
    "EXECUTE": "oqa live start <case id> (one worker and one browser per case), then oqa live step / oqa live end",
    "REPORT": "oqa report",
    "DONE": "nothing: the session is finished",
}


class GateError(Exception):
    """The command is not allowed in the session's current state. The message says why and what to run instead."""


class Session:
    def __init__(self, ws, session_id):
        self.ws, self.id = ws, session_id
        self.dir = paths.session_dir(ws, session_id)
        self.file = self.dir / "state.json"

    # --- opening and creating -------------------------------------------------
    @classmethod
    def open(cls, ws=None, session=None):
        ws, session = paths.resolve_session(ws, session)
        return cls(ws, session)

    @classmethod
    def create(cls, ws, testcase_file, cases_doc, channel=None, make_current=True):
        """INTAKE is done: the workbook was read into `cases_doc`. Starts in ANALYZE and becomes the workspace's current session."""
        session = cls(ws, paths.new_session_id())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json(session.dir / "cases.json", cases_doc)
        write_json(session.file, {
            "schema_version": SCHEMA_VERSION, "id": session.id, "workspace": ws, "file": str(testcase_file), "created": now, "phase": "ANALYZE",
            "done": [{"phase": "INTAKE", "at": now}], "waiting": None, "strategy_sha": None, "confirmation": None, "attempts": {}, "runs": {}, "results": {}, "connect": None,
            "stopped": None, "hints": {}, "seen": 0, "timeouts": {}, "progress": {},
            "explore": "off" if os.environ.get("OQA_NO_EXPLORE_GATE") else "required",
            **({"channel": channel} if channel else {}),
        })
        if make_current:
            (paths.workspace_dir(ws) / "current").write_text(session.id, encoding="utf-8")
        events.append(session.dir, "session_started", workspace=ws, file=str(testcase_file))
        events.append(session.dir, "phase", phase="ANALYZE", text=f"Read {len(cases_doc['cases'])} cases; analysing them")
        return session

    # --- state ------------------------------------------------------------------
    def state(self):
        data = read_json(self.file)
        if data is None:
            raise SchemaError(f"session {self.id} has no state.json", "session")
        return data

    @contextmanager
    def update(self):
        with locked(self.file):
            data = self.state()
            yield data
            write_json(self.file, data)

    def cases(self):
        return read_json(self.dir / "cases.json")

    # --- gates ------------------------------------------------------------------
    def require(self, *phases, working=True):
        """Raise GateError unless the session is in one of `phases` (and, for `working` commands, not waiting for the user)."""
        state = self.state()
        if working and state.get("stopped"):
            raise GateError("The user stopped the test. Do nothing until they resume it: run `oqa wait`.")
        if working and state["waiting"]:
            raise GateError(f"The session is waiting for the user to answer: \"{state['waiting']['question']}\". Do not continue until `oqa answer` is run with their reply.")
        if state["phase"] not in phases:
            raise GateError(f"Not allowed in phase {state['phase']} (allowed: {', '.join(phases)}). Next: {NEXT[state['phase']]}")
        return state

    def _enter(self, state, phase, text):
        state["phase"] = phase
        state["done"].append({"phase": phase, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        events.append(self.dir, "phase", phase=phase, text=text)

    # --- ANALYZE / CONNECT / CONFIRM ------------------------------------------------
    def analysis_submitted(self, open_questions=None, reviews=None, document=None):
        """A valid analysis was saved. First time: go to CONNECT, unless a case still needs the user: then the session stays in ANALYZE and the questions are asked now
        (`open_questions` = the text shown to the user), because a plan built on a guess is the cause of blocked cases later. A later correction (before confirming)
        drops the strategy so it is built again."""
        with self.update() as state:
            if state["phase"] not in ("ANALYZE", "CONNECT", "CONFIRM"):
                raise GateError(f"The analysis can no longer change in phase {state['phase']}.")
            if state["waiting"]:
                raise GateError("The session is waiting for the user to answer; run `oqa answer` first.")
            if document is not None:
                saved = {key: value for key, value in document.items() if key not in ("_context_more", "_context_unavailable")}
                if isinstance(saved.get("cases"), list):
                    saved["cases"] = [{key: value for key, value in case.items() if key != "_context"}
                                      if isinstance(case, dict) else case for case in saved["cases"]]
                write_json(self.dir / "analysis.json", saved)
            state["strategy_sha"] = None
            if reviews:
                if state["phase"] != "ANALYZE":
                    self._enter(state, "ANALYZE", "Updated analysis needs case review")
                state["reviews"] = reviews
                state["waiting"] = {"question": "Review the cases in the dashboard.", "case": None, "review": True}
                for case, questions in reviews.items():
                    events.append(self.dir, "ask", question="\n".join(questions), case=case)
            elif open_questions and state["phase"] == "ANALYZE":
                state["waiting"] = {"question": open_questions, "case": None, "asked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                events.append(self.dir, "ask", question=open_questions)
            elif state["phase"] == "ANALYZE":
                self._enter(state, "CONNECT", "Analysis accepted; testing the connection")

    def connected(self, info):
        """Called by `oqa connect` when every profile signed in. `info` (no secrets) is kept for the dashboard."""
        with self.update() as state:
            if state["phase"] != "CONNECT":
                raise GateError(f"Not allowed in phase {state['phase']}. Next: {NEXT[state['phase']]}")
            state["connect"] = info
            self._enter(state, "CONFIRM", "Connection works; waiting for the user to approve the strategy")

    def strategy_ready(self, sha):
        with self.update() as state:
            if state["phase"] != "CONFIRM":
                raise GateError(f"Not allowed in phase {state['phase']}. Next: {NEXT[state['phase']]}")
            state["strategy_sha"] = sha

    def confirm(self, by, sha, has_cases=True):
        """The user's approval. `sha` must be the digest of the strategy on disk now, so what they approved is what runs."""
        with self.update() as state:
            if state["waiting"]:
                raise GateError("The session is waiting for the user to answer; run `oqa answer` first.")
            if state["phase"] != "CONFIRM":
                raise GateError(f"Nothing to confirm in phase {state['phase']}. Next: {NEXT[state['phase']]}")
            if not state["strategy_sha"] or state["strategy_sha"] != sha:
                raise GateError("The strategy changed or was never built; run `oqa strategy` and ask the user to review it again.")
            undecided = analysis.pending_conflicts(read_json(self.dir / "analysis.json"), state.get("conflict_choices"))
            if undecided:
                raise GateError(f"Còn {len(undecided)} chỗ testcase lệch BD chưa chọn theo BD hay theo testcase ("
                                + ", ".join(f"{c['case']} bước {c['step']}" for c in undecided[:5]) + "). Chọn trên dashboard trước khi duyệt.")
            state["confirmation"] = {"by": by, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "strategy_sha": sha}
            events.append(self.dir, "confirmed", by=by, strategy_sha=sha)
            self._enter(state, "EXECUTE" if has_cases else "REPORT",
                        "Confirmed; ready to run the approved cases" if has_cases else "Confirmed; there are no approved cases to run")

    def _write_strategy(self, state, selected):
        """Build the strategy for the chosen cases (None = every ready case), write it and remember its digest. Called with the state open."""
        from . import workspace

        data, text = analysis.strategy(read_json(self.dir / "analysis.json"), self.cases(), workspace.load(self.ws), selected)
        write_json(self.dir / "strategy.json", data)
        (self.dir / "strategy.md").write_text(text + "\n", encoding="utf-8")
        state["strategy_sha"] = data["sha"]
        return data

    def select_cases(self, cases, by="dashboard"):
        """Before the strategy is approved: which of the analysed (ready) cases belong to this run. Everything is ticked until the user unticks something.
        A case needs the cases it depends on, so those are added. The strategy is rebuilt, so what the user approves is what was ticked."""
        with self.update() as state:
            if state["phase"] != "CONFIRM":
                raise GateError(f"The cases of this run are chosen before the strategy is approved, not in phase {state['phase']}. To run more cases later use oqa run-more or the dashboard.")
            if state["waiting"]:
                raise GateError("The session is waiting for the user to answer; run `oqa answer` first.")
            doc = read_json(self.dir / "analysis.json")
            ready = [c["id"] for c in doc["cases"] if c["verdict"] == "ready"]
            unknown = [c for c in cases if c not in ready]
            if unknown or not cases:
                raise GateError("Choose at least one case to run." if not cases else f"Not a case that is ready to run: {', '.join(unknown)}.")
            closed, added = analysis.close_selection(doc, cases)
            state["selected"] = None if set(closed) == set(ready) else closed
            data = self._write_strategy(state, state["selected"])
            events.append(self.dir, "cases_selected", cases=data["order"], by=by, added=added)
        return {"selected": data["order"], "deferred": data.get("deferred", []), "added": added}

    def add_cases(self, cases, by="dashboard"):
        """After the approved cases ran (or while they run): the user adds cases that were left out. Same session, same connection, same evidence folder: the new cases
        join the queue and the session goes back to EXECUTE. Choosing them is the user's approval of them."""
        with self.update() as state:
            if state["phase"] not in ("EXECUTE", "REPORT", "DONE"):
                raise GateError(f"Cases are added after the approved ones have started, not in phase {state['phase']}.")
            if state.get("stopped"):
                raise GateError("The test is stopped: resume it first.")
            if state["waiting"]:
                raise GateError("The session is waiting for the user to answer; run `oqa answer` first.")
            strategy = read_json(self.dir / "strategy.json") or {"order": []}
            left = strategy.get("deferred", [])
            wrong = [c for c in cases if c not in left]
            if not cases or wrong:
                raise GateError("Choose at least one of the cases that were left out." if not cases else f"Not among the cases left out: {', '.join(wrong)}.")
            doc = read_json(self.dir / "analysis.json")
            passed = {c for c, r in state["results"].items() if r["status"] == "PASS"}
            closed, _ = analysis.close_selection(doc, cases, passed)
            new = [c for c in closed if c not in strategy["order"]]
            by_id = {c["id"]: c for c in doc["cases"]}
            for case in new:
                for parent in by_id[case].get("depends_on") or []:
                    if parent in state["results"] and state["results"][parent]["status"] != "PASS":
                        raise GateError(f"{case} depends on {parent}, which ended {state['results'][parent]['status']}: it cannot run until {parent} passes.")
            state["selected"] = list(strategy["order"]) + new
            data = self._write_strategy(state, state["selected"])
            state["confirmation"] = {"by": by, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "strategy_sha": data["sha"], "added": new}
            events.append(self.dir, "cases_added", cases=new, by=by)
            if state["phase"] != "EXECUTE":
                self._enter(state, "EXECUTE", f"{', '.join(new)} added to the run by the user")
        return {"added": new, "deferred": data.get("deferred", [])}

    def confirm_current(self, by):
        """The user approves the strategy that is on disk now (the terminal and the dashboard both come here). Refuses a strategy that was
        edited or no longer matches the analysis."""
        strategy_doc = read_json(self.dir / "strategy.json")
        if not strategy_doc or not analysis.verify_strategy(strategy_doc, read_json(self.dir / "analysis.json")):
            raise GateError("strategy.json is missing or no longer matches the analysis; run `oqa strategy` again.")
        self.confirm(by, strategy_doc["sha"], has_cases=bool(strategy_doc["order"]))
        return strategy_doc

    # --- asking the user ----------------------------------------------------------
    def ask(self, question, case=None):
        with self.update() as state:
            if state["phase"] == "DONE":
                raise GateError("The session is finished.")
            if state["waiting"]:
                raise GateError(f"Already waiting for the user: \"{state['waiting']['question']}\"")
            state["waiting"] = {"question": question, "case": case, "asked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            events.append(self.dir, "ask", question=question, **({"case": case} if case else {}))

    def review_case(self, case, text="", skip=False):
        with self.update() as state:
            pending = state.get("reviews", {})
            if state["phase"] != "ANALYZE" or case not in pending:
                raise GateError(f"Case {case} is not waiting for review.")
            if not skip and not text.strip():
                raise GateError("Write the answer first.")
            if skip:
                text = text.strip() or "User skipped this case because a prerequisite is missing."
                state.setdefault("skipped_reviews", {})[case] = text
            shared = [item["path"] for item in refs.register_from_text(
                self, text, by="answer", case=case)] if not skip else []
            del pending[case]
            if not pending:
                state["waiting"] = None
            events.append(self.dir, "answered", case=case, answer=text, files=shared, skipped=skip)

    def answer(self, text):
        """The user replied. A question about one case gives that case a fresh repair budget and, if it was left PENDING / BLOCKED, lets it run again."""
        with self.update() as state:
            waiting = state["waiting"]
            if not waiting:
                raise GateError("Nobody is waiting for an answer.")
            if waiting.get("review"):
                raise GateError("Answer or skip each case in the dashboard.")
            case = waiting.get("case")
            state["waiting"] = None
            if case:
                state["attempts"].pop(case, None)
                state.setdefault("progress", {}).pop(case, None)
                if state["results"].get(case, {}).get("status") in ("PENDING", "BLOCKED"):
                    del state["results"][case]
                    if state["phase"] == "REPORT":
                        self._enter(state, "EXECUTE", f"{case} can run again after the answer")
            shared = [i["path"] for i in refs.register_from_text(self, text, by="answer", case=case)]          # a path in the answer is a reference: nothing to learn, nothing to upload
            events.append(self.dir, "answered", answer=text, **({"case": case} if case else {}), **({"files": shared} if shared else {}))
            return waiting

    # --- EXECUTE ------------------------------------------------------------------
    def begin_attempt(self, case):
        """Count one run of `case`. The first run plus MAX_REPAIRS reruns are allowed; the next is refused and the assistant must ask the user."""
        with self.update() as state:
            if state.get("stopped"):
                raise GateError("The user stopped the test. Do nothing until they resume it: run `oqa wait`.")
            if state["waiting"]:
                raise GateError(f"The session is waiting for the user: \"{state['waiting']['question']}\"")
            if state["phase"] != "EXECUTE":
                raise GateError(f"Not allowed in phase {state['phase']}. Next: {NEXT[state['phase']]}")
            strategy = read_json(self.dir / "strategy.json") or {"order": []}
            if case not in strategy["order"]:
                raise GateError(f"{case} is not in the confirmed strategy.")
            if state["results"].get(case, {}).get("status") == "PASS":
                raise GateError(f"{case} already passed.")
            used = state["attempts"].get(case, 0)
            allowed_repairs = 1 if state.get("channel") else MAX_REPAIRS
            if used >= 1 + allowed_repairs:
                raise GateError(f"{case} used its first run and {allowed_repairs} recovery attempt(s). Record the outcome with oqa finish or ask the user with oqa ask --case {case}.")
            if used >= 1:
                log = self._log(case)
                if state.get("channel") and (not log["last"] or log["last"]["attempt"] != state.get("runs", {}).get(case)):
                    raise GateError(f"{case} still has an open attempt. Check the worker and use oqa live recover if it stopped.")
                if log["last"] and log["last"]["status"] == "FAIL" and not log["noted"]:
                    raise GateError(f"{case} failed on run {log['last']['attempt']} and no repair note was recorded. Do not rerun on a guess: look at the picture and `oqa probe`, "
                                    f"then say what you found: oqa repair {case} --cause \"...\" --evidence \"...\" --change \"...\"")
            state["attempts"][case] = used + 1
            runs = state.setdefault("runs", {})                  # the run NUMBER never goes back, even when the user gives the case a fresh budget:
            runs[case] = runs.get(case, 0) + 1                   # two different runs must never share a number, a picture folder or a row in the report
            return runs[case]

    # --- repairing: what the assistant found, before it may run again -------------------------------
    def _log(self, case):
        """What the event log says about `case`'s runs: the last finished run, whether a repair note came after it, whether the page was probed since,
        every earlier run and every repair note."""
        log = {"last": None, "noted": False, "probed": False, "runs": [], "repairs": []}
        for e in events.read(self.dir):
            if e["kind"] in ("probe", "explore") and e.get("case") in (None, case):
                log["probed"] = True                 # the assistant looked at, or tried things on, the real page after the failure
            elif e.get("case") != case:
                continue
            elif e["kind"] == "attempt_finished":
                log.update(last=e, noted=False, probed=False)
                log["runs"].append(e)
            elif e["kind"] == "repair":
                log["noted"] = True
                log["repairs"].append(e)
        return log

    def awaiting_repair(self):
        """Cases whose last run failed and that have no repair note yet."""
        state = self.state()
        waiting = []
        for case in (read_json(self.dir / "strategy.json") or {"order": []})["order"]:
            log = self._log(case)
            if log["last"] and log["last"]["status"] == "FAIL" and not log["noted"] and case not in state["results"]:
                waiting.append(case)
        return waiting

    @staticmethod
    def _signature(text):
        return re.sub(r"\d+", "#", (text or "").lower()).strip()[:100]

    def repair(self, case, cause, evidence, change, reanalysis="", script_sha="", timeout_s=None, unchanged_reason=""):
        """The assistant's note after a failed run, required before the case runs again: the cause it found, how it knows, what it changed and, when the same failure
        came twice, how it now reads the case. Refuses a note that only repeats an earlier one or a repair that changed nothing."""
        cause, evidence, change, reanalysis, unchanged_reason = (x.strip() for x in (cause, evidence, change, reanalysis, unchanged_reason))
        with self.update() as state:
            if state.get("stopped"):
                raise GateError("The user stopped the test. Do nothing until they resume it: run `oqa wait`.")
            if state["waiting"]:
                raise GateError(f"The session is waiting for the user to answer: \"{state['waiting']['question']}\"")
            if state["phase"] != "EXECUTE":
                raise GateError(f"Not allowed in phase {state['phase']}. Next: {NEXT[state['phase']]}")
            if case not in (read_json(self.dir / "strategy.json") or {"order": []})["order"]:
                raise GateError(f"{case} is not in the confirmed strategy.")
            log = self._log(case)
            last = log["last"]
            if not last or last["status"] != "FAIL" or case in state["results"]:
                raise GateError(f"{case} has no failed run waiting for a repair. Run it first: oqa run {case}")
            if log["noted"]:
                raise GateError(f"The repair note for run {last['attempt']} of {case} is already recorded. Next: oqa run {case}")
            for label, text in (("--cause", cause), ("--evidence", evidence), ("--change", change)):
                if len(text) < MIN_NOTE:
                    raise GateError(f"{label} says too little. Write what you actually found: the cause of the failure, what showed it (the picture, the probe, the page text) and what you changed.")
            plain = lambda text: re.sub(r"\W+", " ", text.lower()).strip()
            for earlier in log["repairs"]:
                if difflib.SequenceMatcher(None, plain(cause), plain(earlier["cause"])).ratio() >= SAME_CAUSE:
                    raise GateError(f"That is the cause you gave after run {earlier['attempt']}: \"{earlier['cause']}\". The change it led to did not fix the failure, so the cause is something else. "
                                    "Look again (the picture, `oqa probe`), or ask the user.")
            twice = len(log["runs"]) >= 2 and log["runs"][-2]["status"] == "FAIL" and self._signature(log["runs"][-2]["reason"]) == self._signature(last["reason"])
            if twice and len(reanalysis) < MIN_NOTE:
                raise GateError("The same failure came twice. Say how you now read the case (--reanalysis \"...\"): what the step really needs, what you assumed that was wrong.")
            if timeout_s is not None and not (analysis.MIN_TIMEOUT <= timeout_s <= analysis.MAX_TIMEOUT):
                raise GateError(f"--timeout is seconds from {analysis.MIN_TIMEOUT} to {analysis.MAX_TIMEOUT}.")
            if script_sha and last.get("script_sha") == script_sha and timeout_s is None and not unchanged_reason:
                raise GateError(f"The script of {case} is the same as the one that just failed. Change it, or say what else you changed (--timeout, or --unchanged \"data created through the UI\").")
            if timeout_s is not None:
                state.setdefault("timeouts", {})[case] = timeout_s
            note = {"case": case, "attempt": last["attempt"], "cause": cause, "evidence": evidence, "change": change, "probed": log["probed"]}
            for name, value in (("reanalysis", reanalysis), ("script_sha", script_sha), ("timeout_s", timeout_s), ("unchanged_reason", unchanged_reason)):
                if value not in ("", None):
                    note[name] = value
            events.append(self.dir, "repair", **note)
            memory.remember_repair(self.ws, memory.stem_of(state["file"]), case, note, self.id)
            return note

    def advance(self, case, step):
        """A run of `case` failed at `step`. When that is FURTHER than any earlier run got, it was not a failed repair: the case moved on and the next problem is a
        new one, so the run does not use up one of the repairs (the very first run of a case is always counted). Returns True when it got further."""
        with self.update() as state:
            best = state.setdefault("progress", {}).get(case, 0)
            if step <= best:
                return False
            state["progress"][case] = step
            if best and state["attempts"].get(case, 0) > 1:
                state["attempts"][case] -= 1
            return bool(best)

    def refund_attempt(self, case):
        """An attempt the user cut short does not count against the case's budget."""
        with self.update() as state:
            if state["attempts"].get(case):
                state["attempts"][case] -= 1
                state.setdefault("runs", {})[case] = max(0, state.get("runs", {}).get(case, 1) - 1)
                if not state["attempts"][case]:
                    del state["attempts"][case]

    # --- what only the user does: stop, resume, hint ------------------------------------
    def stop(self, by):
        """The user stops the test: every working command is refused until `resume`, the session's workers, Playwright daemons and browsers are ended, every claim
        is given back and each run left open is recorded as STOPPED (see halt.py). Returns what was cleaned up."""
        with self.update() as state:
            if state["phase"] == "DONE":
                raise GateError("The session is finished.")
            if state.get("stopped"):
                raise GateError("Already stopped.")
            state["stopped"] = {"by": by, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            events.append(self.dir, "stopped", by=by)
        from . import halt

        return halt.stop_work(self)                      # outside the lock: the flag is already on, so nothing new starts while the old work is ended

    def resume(self, by):
        from . import halt

        if self.state().get("stopped"):
            halt.close_open_attempts(self, "Stopped by the user")       # a session stopped by an older version left its runs open
        with self.update() as state:
            if not state.get("stopped"):
                raise GateError("The test is not stopped.")
            state["stopped"] = None
            events.append(self.dir, "resumed", by=by)

    def hint(self, case, text, by="dashboard"):
        """The user's advice for a case that failed (or was left BLOCKED / PENDING): it goes back in the queue with a fresh budget and without its old result,
        and the advice is shown to the assistant next time it runs the case. A question the assistant had asked about this case counts as answered."""
        text = (text or "").strip()
        if not text:
            raise GateError("Write the hint first.")
        with self.update() as state:
            if state["phase"] not in ("EXECUTE", "REPORT"):
                raise GateError(f"A hint is for a case that already ran; the session is in phase {state['phase']}.")
            if case not in (read_json(self.dir / "strategy.json") or {"order": []})["order"]:
                raise GateError(f"{case} is not in the confirmed strategy.")
            if state["results"].get(case, {}).get("status") == "PASS":
                raise GateError(f"{case} already passed.")
            refs.register_from_text(self, text, by="answer", case=case)          # a hint that points at a file ("use C:\data\x.xlsx") makes it a reference too
            state.setdefault("hints", {}).setdefault(case, []).append({"text": text, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by})
            self.archive_result(state, case, f"reopened by the user's hint: {text}")
            existing_result = state["results"].pop(case, None)
            last = self._log(case)["last"]
            if not state.get("channel") or existing_result or (last and last["attempt"] == state.get("runs", {}).get(case)):
                from . import claims

                state["attempts"].pop(case, None)
                state.setdefault("progress", {}).pop(case, None)
                claims.release(self, case, missing_ok=True)
            if state["waiting"] and state["waiting"].get("case") == case:
                state["waiting"] = None
                events.append(self.dir, "answered", answer=text, case=case)
            events.append(self.dir, "hint", case=case, text=text)
            memory.remember_hint(self.ws, memory.stem_of(state["file"]), case, text, self.id)       # the next session on this file starts knowing it
            if state["phase"] == "REPORT":
                self._enter(state, "EXECUTE", f"{case} goes back in the queue after the user's hint")

    def news(self):
        """What the user did since the assistant was last told: confirmed, answered, hint, stopped, resumed (oldest first)."""
        seen = self.state().get("seen", 0)
        return [e for i, e in enumerate(events.read(self.dir)) if i >= seen and e["kind"] in events.USER_KINDS]

    def mark_seen(self):
        with self.update() as state:
            state["seen"] = len(events.read(self.dir))

    def consume_news(self):
        with self.update() as state:
            log = events.read(self.dir)
            pending = log[state.get("seen", 0):]
            news = [event for event in pending if event["kind"] in events.USER_KINDS]
            if news:
                state["seen"] = len(log)
            return news

    @staticmethod
    def archive_result(state, case, why):
        """A conclusion that is replaced or reopened stays on record: what it said, when, and what superseded it. Evidence pictures are never deleted either."""
        old = state["results"].get(case)
        if old:
            state.setdefault("history", {}).setdefault(case, []).append({**old, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "superseded_by": (why or "")[:300]})

    def finish_case(self, case, status, reason="", category="", basis=""):
        """Record the end of a case. When every case of the strategy has a result, the session moves to REPORT.
        `category` says what kind of conclusion it is (see events.CATEGORIES): an application defect, an automation issue, or a missing test precondition."""
        if category and category not in events.CATEGORIES:
            raise GateError(f"category must be one of {', '.join(events.CATEGORIES)}")
        if status not in events.CASE_STATUSES:
            raise GateError(f"status must be one of {', '.join(events.CASE_STATUSES)}")
        with self.update() as state:
            if state["phase"] != "EXECUTE":
                raise GateError(f"Not allowed in phase {state['phase']}. Next: {NEXT[state['phase']]}")
            order = (read_json(self.dir / "strategy.json") or {"order": []})["order"]
            if case not in order:
                raise GateError(f"{case} is not in the confirmed strategy.")
            attempts = state["attempts"].get(case, 0)
            if not attempts and status in ("PASS", "FAIL"):
                raise GateError(f"{case} was never run; there is nothing to record.")
            self.archive_result(state, case, f"new conclusion {status}")
            state["results"][case] = {"status": status, "attempts": attempts, **({"reason": reason} if reason else {}), **({"category": category} if category else {}),
                                      **({"basis": basis} if basis else {})}
            if state.get("channel"):
                from . import claims

                claims.release(self, case, missing_ok=True)
            events.append(self.dir, "case_finished", case=case, status=status, attempts=attempts, **({"reason": reason} if reason else {}), **({"category": category} if category else {}))
            memory.remember_result(self.ws, memory.stem_of(state["file"]), case, status, reason, self.id)
            if all(c in state["results"] for c in order):
                self._enter(state, "REPORT", "Every case has a result; building the report")

    def done(self, counts):
        with self.update() as state:
            if state["phase"] != "REPORT":
                raise GateError(f"Not allowed in phase {state['phase']}. Next: {NEXT[state['phase']]}")
            events.append(self.dir, "session_finished", counts=counts)
            self._enter(state, "DONE", "Report written; the session is finished")

    def declare(self, case, status, reason, category=""):
        """The assistant's own verdict when it stops working on a case: FAIL (the application is wrong: needs a run that failed with evidence),
        BLOCKED (cannot be done here) or PENDING (needs the user). PASS can only come from a run."""
        if status not in ("FAIL", "BLOCKED", "PENDING"):
            raise GateError("Only FAIL, BLOCKED or PENDING are declared; PASS comes from `oqa run`.")
        if not reason.strip():
            raise GateError("Say why (--reason).")
        if status == "FAIL":
            mine = [e for e in events.read(self.dir) if e.get("case") == case]
            attempts = [e for e in mine if e["kind"] == "attempt_finished"]
            if not attempts or attempts[-1]["status"] != "FAIL" or not any(e["kind"] == "step" and e["status"] == "FAIL" and e["images"] for e in mine):
                raise GateError(f"FAIL needs a run of {case} that failed with a picture of the failing step. Run it first.")
        if status in ("BLOCKED", "PENDING") and category not in ("automation", "precondition"):
            raise GateError(f"{status} is for an automation issue or a missing test precondition: pass --category automation|precondition (an application defect is FAIL).")
        if status == "FAIL":
            category = "application"
        self.finish_case(case, status, reason, category)

    def summary(self):
        state = self.state()
        order = (read_json(self.dir / "strategy.json") or {}).get("order", [])
        counts = {}
        for result in state["results"].values():
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        return {"workspace": self.ws, "session": self.id, "file": state["file"], "phase": state["phase"], "waiting": state["waiting"], "stopped": state.get("stopped"),
                "confirmed": bool(state["confirmation"]), "cases_planned": len(order), "cases_done": len(state["results"]), "counts": counts,
                "attempts": state["attempts"], "next": NEXT[state["phase"]],
                "needs_repair": self.awaiting_repair() if state["phase"] == "EXECUTE" else []}
