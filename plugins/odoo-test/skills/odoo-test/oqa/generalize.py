"""Make a case's saved script independent of the run that recorded it.

The worker writes its script during one live run, so the script is only right at that moment: a record id in an address, a click that depended on where the
previous step left the page, a position in a list. A second agent rewrites it as a generic script (run variables for ids, names from this run's own key, explicit
navigation, no positions or fixed dates), then replays the result on a fresh run, twice, and keeps it only if both replays pass. Otherwise the failure goes back to the agent
for another attempt, and when the attempts are used up the original script is restored.

oqa asks no model itself: `brief` gives the host's sub-agent its work, `submit` takes the script it wrote and checks it. The attempts are counted in the session's events."""
import re
import time

from . import events, live, record_names, workspace
from .state import GateError

RULES = """You rewrite a Playwright script of an Odoo test case so that it works on EVERY run, not only the run that recorded it.
The script was recorded by a worker during one live run. It will be replayed later against a database in a different state, with new records and on another day.

Hard rules:
1. Never write the id of a record. After a record is saved or opened, put a line `$OQA_CAPTURE(name)` (it stores the id of the record open on screen in the run variable `name`) and refer to it
   afterwards as `$OQA_ID(name)`, for example `await page.waitForURL(new RegExp('action-90/' + $OQA_ID(rec)));`. Prefer waiting for something visible instead of an address.
2. Record names and codes that start with the run prefix {prefix} are this run's own records: keep every such name exactly as written (the tool swaps the prefix for a fresh one on each replay).
   Never write another fixed name, code, date, or number that Odoo generates (sequences such as SO0042).
3. Find a record by its own name: search for it (or filter the list by the prefix) and click the cell with that exact name. Never rely on a position: no .first(), .nth(), "first row", row index,
   unless the list was first filtered by this run's prefix so that only this run's records remain.
4. Add navigation ONLY where the original script is missing it: when a step's first action needs a page that the earlier steps of the script do not leave you on (they reached it by clicks that
   were never recorded). Open that page by its menu or address WITHOUT a record id, then search for the record by name. NEVER repeat what an earlier step already did: do not redo a setup,
   an import, an upload, a save or a record creation, and do not navigate away from a page the previous step left you on when the step continues there.
5. Wait for visible conditions (waitFor, a state to be hidden, text to appear). Never waitForTimeout and never networkidle. After Save wait for the Save button to be hidden.
6. Keep the meaning: the same actions, the same checks, the same expected result of each step. Do not weaken or remove an assertion. `// shows:` must be a text that is identical on every run
   (a label, a status, a name that starts with the run prefix), never an id, a date or a generated number.
7. Keep the structure exactly: one block per step, in order. Preserve frame, frame-before, shows, profile, check-target, check-values and assert lines when present.
   Keep every fixture setup line (`await page.evaluate(async spec => ...`) exactly as it is.
8. The code runs inside the browser tool's sandbox: there is NO `require` and NO `import`, no `fs`, no `process`; `Buffer` and `page` exist. A file the page downloads must not be read from disk:
   take it from the download object, `const [download] = await Promise.all([page.waitForEvent('download'), <the click>]); let text = ''; for await (const chunk of await download.createReadStream()) text += chunk;`,
   edit `text` in JavaScript by the column names in its header row (never by an id or a position you saw in a previous run), and upload the result with
   `chooser.setFiles({{name: 'x.csv', mimeType: 'text/csv', buffer: Buffer.from(text)}})`.
   Values the file holds that belong to a record created by the run (an External ID, a code) must be read from the downloaded file, not copied from the old script.
9. Each code line starts with `await page.` (or `await Promise.all([page.waitForEvent(`), one statement per line.

Answer with the complete rewritten script and nothing else: no explanation, no markdown fences."""


STALE_AFTER = 30 * 60        # seconds: a rewrite reports at the start of every attempt, so one silent for this long has died


def effective(event):
    """A script_generalized event as the user should see it: a RUNNING one that has said nothing for STALE_AFTER seconds is STALE (its process is gone), not running forever."""
    if event and event.get("status") == "RUNNING":
        try:
            age = time.time() - time.mktime(time.strptime(event["ts"], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, ValueError):
            age = 0
        if age > STALE_AFTER:
            return {**event, "status": "STALE"}
    return event


def latest(session, case):
    return effective(next((e for e in reversed(events.read(session.dir)) if e["kind"] == "script_generalized" and e.get("case") == case), None))


def running(session, case):
    return (latest(session, case) or {}).get("status") == "RUNNING"


def build_prompt(session, case, original, steps, prefix, failure=""):
    plan = "\n".join(f"  {s['n']}. {s['action']}  ->  {s['expected']}" for s in steps)
    again = (f"\nYour previous version failed. Fix exactly this and answer with the complete script again:\n{failure}\n" if failure else "")
    return (RULES.format(prefix=record_names.fixture_prefix(session, case, prefix)) +
            f"\n\nThe test case's planned steps (the script must keep doing these):\n{plan}\n{again}\nThe script to rewrite:\n{original}\n")


def _clean(text):
    text = re.sub(r"^\s*```[a-zA-Z]*\s*$", "", text, flags=re.M)         # a fence the model added anyway
    text = re.sub(r"^\s*\$OQA_CAPTURE\((\w+)\)\s*;?\s*$", lambda m: live.capture_id_code(m.group(1)), text, flags=re.M)
    return live.expand_vars(text).strip() + "\n"


EFFECTS = (r"setFiles\(", r"o_form_button_save'?\)?\.click", r"name: ['\"]Import['\"]", r"evaluate\(async spec")        # actions that change data: the rewrite must not do any of them more often


def _count(text):
    return [len(re.findall(pattern, text)) for pattern in EFFECTS]


def check(session, case, text, steps, prefix, original=""):
    """What makes a candidate unfit before any browser is used: the structure, record ids, delays, fixed record names."""
    blocks, problems = live.parse_script(text, steps)
    for number, line in enumerate(text.splitlines(), 1):
        code = line.strip()
        if code.startswith("//") or not code:
            continue
        if re.search(r"\brequire\s*\(|^\s*import\s", code):
            problems.append(f"line {number}: require/import does not exist in the sandbox; read a download with download.createReadStream()")
        if re.search(r"\.waitForTimeout\s*\(|\bnetworkidle\b", code):
            problems.append(f"line {number}: a fixed delay; wait for a visible condition")
        if live.RECORD_ID_URL.search(code):
            problems.append(f"line {number}: a record id in an address; use $OQA_CAPTURE(name) and $OQA_ID(name)")
    if original:
        if not problems:
            previous, _ = live.parse_script(original, steps)
            problems.extend(live.preserved_checks(previous, blocks))
        for pattern, now, before in zip(EFFECTS, _count(text), _count(original)):
            if now > before:
                problems.append(f"the rewrite repeats an action that changes data ({pattern}: {now} times, the original does it {before}); do not redo what an earlier step already did")
    fixed = record_names.foreign(session, case, text, prefix)
    if fixed:
        problems.append("fixed record names that are not this run's own: " + ", ".join(fixed[:3]))
    return problems


def _log(session):
    return [e for e in events.read(session.dir) if e["kind"] == "script_generalized"]


def _open_attempt(session, case):
    """The attempt a rewrite of `case` is waiting for, or None: the latest event of the case says RUNNING (or has gone STALE)."""
    last = next((e for e in reversed(_log(session)) if e.get("case") == case), None)
    return last if last and last.get("status") == "RUNNING" else None


def candidate_file(session, case):
    return session.dir / "generalize" / f"{re.sub(r'[^A-Za-z0-9._-]+', '_', case)}.candidate.js"


def _ready(session, case):
    """The case, its saved script and its steps, or a GateError that says why a rewrite cannot start."""
    item = live._strategy_case(session, case)
    path = live.script_file(session, case)
    if not path.is_file():
        raise GateError(f"{case} has no saved script to rewrite (a PASS keeps one).")
    if (session.state()["results"].get(case) or {}).get("status") != "PASS":
        raise GateError(f"{case} has no PASS result: its script may still be in use or be unfinished.")
    return item, path


def _agent_brief(session, case, text):
    from .dispatch import _oqa_preamble

    target = candidate_file(session, case)
    return (f"You rewrite the saved script of case {case} (oqa session {session.id}, workspace {session.ws}) so that it works on every run. The live result and the pictures are final: "
            f"do not run the case again and do not touch them.\n{_oqa_preamble(session)}\n"
            f"Write the complete rewritten script to {target} (create the folder if needed), then run: oqa live regenerate {case} --candidate {target}\n"
            "That command checks the script, replays it on a fresh run (twice) and prints PASS, or RETRY with what failed and a new brief: fix exactly that, write the file again and submit again. "
            "Stop when it says PASS or FAIL. Reply in Vietnamese with the final result and what you changed.\n\n" + text)


def brief(session, case, attempts=3):
    """Start a rewrite of `case`: the brief for ONE host agent. The first script stays recoverable whatever happens next."""
    item, path = _ready(session, case)
    if running(session, case):
        raise GateError(f"{case}: a rewrite of this script is already running.")
    original = path.read_text(encoding="utf-8")
    backup = path.with_name(path.stem + ".original.js")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    prefix = workspace.load(session.ws)["test_prefix"]
    events.append(session.dir, "script_generalized", case=case, status="RUNNING", attempt=1, attempts=attempts, note=f"attempt 1 of {attempts}: waiting for the agent's script")
    return _agent_brief(session, case, build_prompt(session, case, original, item["steps"], prefix))


def submit(session, case, text, replays=2):
    """The script an agent wrote for `case`: kept only when the static checks and `replays` fresh replays in a row pass. Returns a summary; a failed attempt returns status RETRY with the
    reason and a new brief, and the original script stays in place; the last failed attempt raises GateError."""
    item, path = _ready(session, case)
    open_ = _open_attempt(session, case)
    if not open_:
        raise GateError(f"{case}: no rewrite is open. Start one with: oqa live regenerate {case} --brief")
    attempt, attempts = open_.get("attempt") or 1, open_.get("attempts") or 3
    original = path.read_text(encoding="utf-8")
    backup = path.with_name(path.stem + ".original.js")
    prefix = workspace.load(session.ws)["test_prefix"]
    started, kept = time.time(), False
    candidate = _clean(text)
    failure = "; ".join(check(session, case, candidate, item["steps"], prefix, original))[:700]
    try:
        if not failure:
            path.write_text(candidate, encoding="utf-8")
            if replays == 0:
                kept = True
                events.append(session.dir, "script_generalized", case=case, status="UNVERIFIED", attempt=attempt, note="rewritten, not replayed")
                return {"case": case, "status": "UNVERIFIED", "attempts": attempt, "replays": 0, "backup": str(backup)}
            for round_ in range(1, replays + 1):
                try:
                    live.replay(session, case, headed=False, fast=True, keep=False)     # a trial: a rejected candidate leaves no Replay result behind
                except GateError as error:
                    failure = f"replay {round_} of {replays} failed: {str(error)[:600]}"
                    break
            else:
                kept = True
                events.append(session.dir, "script_generalized", case=case, status="PASS", attempt=attempt, replays=replays, seconds=round(time.time() - started),
                              note=f"generic script kept after {replays} fresh replays")
                return {"case": case, "status": "PASS", "attempts": attempt, "replays": replays, "backup": str(backup)}
    except Exception as error:                                               # anything unforeseen: the original comes back below
        failure = f"unexpected {type(error).__name__}: {str(error)[:300]}"
    finally:
        if not kept:
            path.write_text(original, encoding="utf-8")
    if attempt >= attempts:
        events.append(session.dir, "script_generalized", case=case, status="FAIL", attempt=attempt, note="original script restored: " + failure[:300])
        raise GateError(f"{case}: no generic script passed {replays} fresh replays in {attempts} attempt(s); the original is restored. Last problem: {failure}")
    events.append(session.dir, "script_generalized", case=case, status="RUNNING", attempt=attempt + 1, attempts=attempts, note=f"attempt {attempt + 1} of {attempts}: {failure[:200]}")
    again = build_prompt(session, case, original, item["steps"], prefix, failure)
    return {"case": case, "status": "RETRY", "attempt": attempt, "failure": failure, "brief": _agent_brief(session, case, again)}


def cancel(session, case):
    """Close an open rewrite nobody finishes (the agent died): the original script is in place already."""
    if _open_attempt(session, case):
        events.append(session.dir, "script_generalized", case=case, status="FAIL", attempt=(_open_attempt(session, case).get("attempt") or 1), note="cancelled: original script kept")
