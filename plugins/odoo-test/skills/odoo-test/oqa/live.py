"""EXECUTE with playwright-cli: one agent per case, one browser per case, no exploring pass and no script to repair.

    oqa live start ID                 opens the case's own browser (signed in) and opens the case's attempt
    oqa live shot  ID --n N --target  (optional) the picture BEFORE an action that leaves the page: the element is framed while it is still there
    oqa live step  ID --n N --status PASS|FAIL --actual "what the page showed" [--target <locator>]
                                      the picture of the step: oqa asks the browser where the element is and draws the red frame itself
    oqa live end   ID --status ...    closes the case; PASS only when every planned step has a PASS and a framed picture

The agent reads the page (`playwright-cli -s=oqa-ID snapshot`), acts on what it reads, fixes a wrong move in the browser, and tells oqa only the outcome of each step. The
frame comes from the element's real position as reported by the browser, never from the agent, and a step with no visible element cannot be recorded as PASS."""
import json
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from . import analysis as analysis_mod
from . import captions, connect, evidence, events, memory, paths, record_names, replays, textfix, workspace
from .state import GateError, Session
from .store import read_json, write_json

WORK = Path.home() / ".odoo-test" / "pw-work"            # playwright-cli only reads and writes under the folder it was opened in


def _home():
    return Path(os.environ.get("ODOO_TEST_HOME") or (Path.home() / ".odoo-test")) / "pw-work"


def sname(case):
    return f"oqa-{re.sub(r'[^A-Za-z0-9_.-]+', '_', str(case))}"


def browser_case(session, case):
    if not session.state().get("channel"):
        return case
    digest = hashlib.sha256(str(case).encode("utf-8")).hexdigest()[:8]
    return f"{session.id}-{str(case)[:30]}-{digest}"


def _cli():
    found = shutil.which("playwright-cli")
    if not found:
        raise GateError("playwright-cli is not installed: npm install -g @playwright/cli@latest")
    return found


def _run(case, *args, timeout=120):
    work = _home()
    work.mkdir(parents=True, exist_ok=True)
    executable = Path(_cli())
    command = [str(executable)]
    if os.name == "nt" and executable.suffix.lower() == ".cmd":
        entry = executable.parent / "node_modules" / "@playwright" / "cli" / "playwright-cli.js"
        node = shutil.which("node")
        if node and entry.is_file():
            command = [node, str(entry)]
    try:
        done = subprocess.run([*command, f"-s={sname(case)}", *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=str(work),
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as error:
        raise GateError(f"playwright-cli {args[0]} did not answer within {timeout} s.") from error
    out = textfix.strip_control((done.stdout or "") + (done.stderr or ""))         # colour codes would reach the notes, the events and the report
    if done.returncode or "### Error" in out:
        raise GateError(f"playwright-cli {args[0]} failed: {out[-400:].strip()}")
    return textfix.strip_control(done.stdout or "")


_JSON_STRING = r'"(?:\\.|[^"\\])*"'
_CSV_ACTION = re.compile(
    r"^await Promise\.all\(\[page\.waitForEvent\('filechooser'\), "
    r"page\.getByRole\('button', \{name: (" + _JSON_STRING + r"), exact: true\}\)\.click\(\)\]\)"
    r"\.then\(\(\[chooser\]\) => chooser\.setFiles\(\{name: (" + _JSON_STRING
    + r"), mimeType: 'text/csv', buffer: Buffer\.from\((" + _JSON_STRING + r")\)\}\)\);$"
)
_RELATIVE_GOTO = re.compile(r"""^(await page\.goto\(\s*)(['"])(/(?!/)[^'"\\]*)\2""")


def literal_check(text, timeout):
    """Wait until the visible text contains `text`. Whitespace is compared after normalising, because Odoo renders values such as the pager
    "1-1 / 1" in separate elements and innerText joins them with line breaks or non-breaking spaces."""
    return ("await page.waitForFunction(text => { const norm = value => value.replace(/[\\s\\u00a0\\u202f]+/g, ' ').trim(); "
            "return norm(document.body.innerText).includes(norm(text)); }, "
            + json.dumps(text) + f", {{timeout: {timeout}}});")


NUMBER_FORMS_JS = r"""const forms = token => { const out = [token]; const sign = /^[+-]/.test(token) ? token[0] : ''; const body = token.replace(/^[+-]/, '');
          const pad = value => value.padStart(2, '0'); const parts = body.split(/[\/-]/);
          if (parts.length === 3 && parts.every(part => /^\d+$/.test(part))) {
            if (parts[0].length === 4) out.push('D' + parts[0] + '-' + pad(parts[1]) + '-' + pad(parts[2]));
            else if (parts[2].length === 4 && parts[0].length <= 2 && parts[1].length <= 2) {
              const [first, second, year] = parts;
              if ((+first === +second || +first > 12) && +first <= 31 && +second >= 1 && +second <= 12)
                out.push('D' + year + '-' + pad(second) + '-' + pad(first));
              else if (+second > 12 && +second <= 31 && +first >= 1 && +first <= 12)
                out.push('D' + year + '-' + pad(first) + '-' + pad(second));
            }
            return out; }
          if (!/^\d+(?:[.,]\d+)*$/.test(body)) return out;
          const number = (whole, fraction) => { whole = whole.replace(/^0+(?=\d)/, ''); fraction = (fraction || '').replace(/0+$/, '');
            return 'N' + (sign === '-' && /[1-9]/.test(whole + fraction) ? '-' : '') + whole + (fraction ? '.' + fraction : ''); };
          const seps = body.match(/[.,]/g) || []; const last = Math.max(body.lastIndexOf('.'), body.lastIndexOf(','));
          if (!seps.length) out.push(number(body));
          else if (/^[1-9]\d{0,2}(?:,\d{3})+\.\d+$|^[1-9]\d{0,2}(?:\.\d{3})+,\d+$/.test(body))
            out.push(number(body.slice(0, last).replace(/[.,]/g, ''), body.slice(last + 1)));
          else if (/^[1-9]\d{0,2}([.,])\d{3}(?:\1\d{3})+$/.test(body)) out.push(number(body.replace(/[.,]/g, '')));
          else if (seps.length === 1 && !(body.length - last - 1 === 3 && /^[1-9]\d{0,2}$/.test(body.slice(0, last))))
            out.push(number(body.slice(0, last), body.slice(last + 1)));
          return out; };"""


def number_forms(token):
    """Keep literal matches and normalize unambiguous display formats. Mirrors NUMBER_FORMS_JS."""
    out = {token}
    sign = token[0] if token[:1] in "+-" else ""
    body = token[1:] if sign else token
    parts = re.split(r"[/-]", body)
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        if len(parts[0]) == 4:
            out.add(f"D{parts[0]}-{parts[1]:0>2}-{parts[2]:0>2}")
        elif len(parts[2]) == 4 and len(parts[0]) <= 2 and len(parts[1]) <= 2:
            first, second, year = parts
            if (int(first) == int(second) or int(first) > 12) and int(first) <= 31 and 1 <= int(second) <= 12:
                out.add(f"D{year}-{second:0>2}-{first:0>2}")
            elif 12 < int(second) <= 31 and 1 <= int(first) <= 12:
                out.add(f"D{year}-{first:0>2}-{second:0>2}")
        return out
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", body):
        return out

    def number(whole, fraction=""):
        whole, fraction = re.sub(r"^0+(?=\d)", "", whole), fraction.rstrip("0")
        negative = "-" if sign == "-" and re.search(r"[1-9]", whole + fraction) else ""
        return f"N{negative}{whole}" + (f".{fraction}" if fraction else "")

    seps = re.findall(r"[.,]", body)
    last = max(body.rfind("."), body.rfind(","))
    if not seps:
        out.add(number(body))
    elif re.fullmatch(r"[1-9]\d{0,2}(?:,\d{3})+\.\d+|[1-9]\d{0,2}(?:\.\d{3})+,\d+", body):
        out.add(number(re.sub(r"[.,]", "", body[:last]), body[last + 1:]))
    elif re.fullmatch(r"[1-9]\d{0,2}([.,])\d{3}(?:\1\d{3})+", body):
        out.add(number(re.sub(r"[.,]", "", body)))
    elif len(seps) == 1 and not (len(body) - last - 1 == 3 and re.fullmatch(r"[1-9]\d{0,2}", body[:last])):
        out.add(number(body[:last], body[last + 1:]))
    return out


def outcome_check(shows, values, timeout, frame=None):
    """Retried until the timeout: the value a step waits for often arrives with an RPC that ends after the page looked settled."""
    locator = _locator(frame or "body")
    expect = json.dumps({"shows": shows, "values": values}, ensure_ascii=False)
    check = (
        r"""(element, expected) => {
          const normal = value => value.normalize('NFKC').replace(/\s+/g, ' ').trim();
          if (!element.getClientRects().length || getComputedStyle(element).visibility !== 'visible') throw new Error('The outcome target is hidden');
          const controls = element.matches('input, textarea, select') ? [element] : [...element.querySelectorAll('input, textarea, select')];
          const current = [element.innerText || '', ...controls.filter(control => control.getClientRects().length
            && getComputedStyle(control).visibility === 'visible' && control.type !== 'password').map(control =>
            control.tagName === 'SELECT' ? (control.selectedOptions[0]?.text || '') : control.value)];
          const observed = normal(current.join(' '));
          if (!observed.includes(normal(expected.shows))) throw new Error('Expected visible outcome: ' + expected.shows);
          """ + NUMBER_FORMS_JS + r"""
          const seen = new Set((observed.match(/[+-]?\d+(?:[.,/:\-]\d+)*/g) || []).flatMap(forms));
          const absent = expected.values.filter(value => /^[+-]?\d+(?:[.,/:\-]\d+)*$/.test(normal(value))
            ? !forms(normal(value)).some(form => seen.has(form)) : !observed.includes(normal(value)));
          if (absent.length) throw new Error('Expected values missing from the result: ' + absent.join(', '));
        }"""
    )
    return (f"await (async () => {{ const deadline = Date.now() + {int(timeout)}; for (;;) {{ try {{ "
            f"return await {locator}.evaluate({' '.join(check.split())}, {expect}, {{timeout: Math.max(1, deadline - Date.now())}}); "
            f"}} catch (error) {{ if (Date.now() >= deadline) throw error; await new Promise(resolve => setTimeout(resolve, 250)); }} }} }})();")


def replay_checks(block, timeout):
    frame = block["frame"] if block.get("check_target") else None
    values = block.get("check_values", [])
    check = outcome_check(block["shows"], values, timeout, frame) if frame or values else literal_check(block["shows"], timeout)
    return [SETTLE, check, *block.get("assertions", [])]


SETTLE_MAX_MS = 3000
SETTLE = ("await page.waitForFunction(() => new Promise(resolve => { const started = performance.now(); let last = started; "
          "const watch = new MutationObserver(() => { last = performance.now(); }); "
          "watch.observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true}); "
          "const tick = () => { const now = performance.now(); "
          "const busy = document.querySelector('.o_loading_indicator, .o_blockUI, .o_form_saving, .o_loading'); "
          "if ((now - last >= 300 && !busy) || now - started >= " + str(SETTLE_MAX_MS) + ") { watch.disconnect(); resolve(true); } else setTimeout(tick, 100); }; "
          "tick(); }), null, {timeout: " + str(SETTLE_MAX_MS + 5000) + "});")
"""Wait for the page to stop changing before a step is judged: after Save, Discard or an onchange Odoo re-renders the form, and a value read in between is stale.
An application wait, not a viewing pause: it applies in fast mode too, and is bounded."""


def _upload_csv(browser_id, button, name, content):
    if Path(name).name != name or not name.lower().endswith(".csv"):
        raise GateError("Choose a CSV file with a simple filename.")
    click = "async page => page.getByRole('button', {name: " + json.dumps(button) + ", exact: true}).click()"
    try:
        _run(browser_id, "run-code", click)
    except GateError as error:
        if "file chooser" not in str(error).lower():
            raise
    with tempfile.TemporaryDirectory(prefix="upload-", dir=_home()) as folder:
        staged = Path(folder) / name
        staged.write_bytes(content.encode("utf-8"))
        return _run(browser_id, "upload", str(staged))


def run_actions(browser_id, lines):
    """Run a fragment. File choosers are handled by the CLI's native upload command."""
    result, pending = "", []

    def flush():
        if pending:
            payload = "async page => {\n" + "\n".join(pending) + "\n}"
            pending.clear()
            return _run(browser_id, "run-code", payload)
        return ""

    for line in lines:
        upload = _CSV_ACTION.fullmatch(line.strip())
        if upload:
            result = flush() or result
            result = _upload_csv(browser_id, *(json.loads(field) for field in upload.groups())) or result
        else:
            pending.append(_RELATIVE_GOTO.sub(
                lambda match: match[1] + "await page.evaluate(path => new URL(path, location.href).href, " + json.dumps(match[3]) + ")",
                line, count=1))
    return flush() or result


def _strategy_case(session, case):
    if case not in read_json(session.dir / "strategy.json")["order"]:
        raise GateError(f"{case} is not in the confirmed strategy.")
    return {c["id"]: c for c in read_json(session.dir / "analysis.json")["cases"]}[case]


def _env_state(session, ws, profile, headed):
    """The signed-in state of `profile` on another environment than main: signed in fresh with that environment's own login and password, kept under the
    session (auth_env/, never read by the assistant). Nothing from the main environment's saved sign-in is reused there."""
    from . import browser
    from .secrets import default_store

    account = next(account for account in ws["profiles"] if account["name"] == profile)
    target = session.dir / "auth_env" / ws["env"] / f"{profile}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with browser.launch(headless=not headed) as new_page:
            context, page = new_page()
            connect.login(page, ws, account, default_store())
            context.storage_state(path=str(target))
    except Exception as error:
        raise GateError(f"Could not sign {profile} in on environment {ws['env']} ({ws['url']}): {str(error).splitlines()[0]}") from error
    os.chmod(target, 0o600)
    return str(target)


def open_browser(session, case, headed=False, refresh=True, profile_override=None, browser_id=None, env=None):
    """The case's own browser, already signed in as its profile. oqa loads the saved sign-in from a private copy that is removed at once: nobody reads it.
    `env` names another environment of the workspace: its address, login and password are used instead of the main ones."""
    ws = workspace.for_env(workspace.load(session.ws), env)
    item = _strategy_case(session, case)
    profile = profile_override or item.get("profile") or ws["profiles"][0]["name"]
    if not any(account["name"] == profile for account in ws["profiles"]):
        raise GateError(f"Unknown workspace profile {profile}.")
    state = _env_state(session, ws, profile, headed) if ws["env"] != workspace.MAIN_ENV else connect.storage_path(session, profile)
    if not state:
        raise GateError(f"No signed-in state for profile {profile}: run `oqa connect` first.")
    work = _home()
    (work / ".playwright-cli").mkdir(parents=True, exist_ok=True)
    chromium = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))      # the Chromium oqa itself uses: no Chrome has to be installed
    browser_id = browser_id or browser_case(session, case)
    config = work / f"config-{sname(browser_id)}.json"
    from .browser import VIEWPORT, headless_channel

    channel = headless_channel()
    binary_options = ({"executablePath": str(chromium[-1])} if chromium else
                      {"channel": channel} if channel else {})
    write_json(config, {"browser": {"browserName": "chromium", "launchOptions": {"headless": not headed, **binary_options},
                                    "contextOptions": {"viewport": dict(VIEWPORT)}}})
    private = work / ".playwright-cli" / f"state-{sname(browser_id)}-{os.getpid()}.json"
    shutil.copyfile(state, private)
    os.chmod(private, 0o600)
    try:
        try:
            _run(browser_id, "close")
        except GateError:
            pass
        _run(browser_id, "open", f"--config={config}", *(["--headed"] if headed else []))
        _run(browser_id, "state-load", str(private))
        out = _run(browser_id, "goto", ws["url"].rstrip("/"))               # not /web: Odoo reads "web" as an action and shows a warning that hides the menu
    finally:
        private.unlink(missing_ok=True)
    landed = re.search(r"Page URL: (\S+)", out)
    if landed and "/login" in landed.group(1):
        if refresh:
            from . import browser
            from .secrets import default_store

            account = next(account for account in ws["profiles"] if account["name"] == profile)
            try:
                with browser.launch(headless=not headed) as new_page:
                    context, page = new_page()
                    connect.login(page, ws, account, default_store())
                    context.storage_state(path=str(state))
            except Exception as error:
                raise GateError(f"Could not refresh the sign-in for {profile}: {str(error).splitlines()[0]}") from error
            return open_browser(session, case, headed=headed, refresh=False,
                                profile_override=profile, browser_id=browser_id, env=env)
        raise GateError(f"The saved sign-in did not work (landed on {landed.group(1)}): run `oqa init` to save the password, or sign in again.")
    return profile


def _recorded(session, case, attempt):
    """{n: step event} of this attempt."""
    return {e["n"]: e for e in events.read(session.dir) if e["kind"] == "step" and e.get("case") == case and e.get("attempt") == attempt}


def passed_steps(session, case):
    """The steps the open attempt of `case` already recorded as PASS (none when there is no open attempt or a step failed): a worker that stopped after
    them is replaced by a handover, which keeps them, instead of a recovery, which throws the attempt away and makes the next worker start from step 1."""
    found = _open_attempt(session, case)
    if not found:
        return []
    done = _recorded(session, case, found[0])
    return [] if any(e["status"] != "PASS" for e in done.values()) else sorted(done)


def _attempt(session, case, log=None):
    log = events.read(session.dir) if log is None else log
    mine = [e for e in log if e["kind"] == "case_started" and e.get("case") == case]
    if not mine:
        raise GateError(f"{case} was not started: oqa live start {case}")
    attempt = mine[-1]["attempt"]
    if any(e["kind"] == "attempt_finished" and e.get("case") == case and e.get("attempt") == attempt for e in log):
        raise GateError(f"{case} attempt {attempt} is already closed.")
    return attempt


def start(session, case):
    session.require("EXECUTE")
    item = _strategy_case(session, case)
    if session.state().get("channel"):
        from . import claims

        if not claims.table(session).get(case, {}).get("fresh"):
            raise GateError(f"Claim {case} with `oqa claim --agent WORKER --case {case}` before starting.")
        results = session.state()["results"]
        needed = [parent for parent in item.get("depends_on") or [] if results.get(parent, {}).get("status") != "PASS"]
        if needed:
            raise GateError(f"Wait for the prerequisites of {case} to pass: {', '.join(needed)}.")
    resumed = _handed_over(session, case)
    if resumed:
        return _resume(session, case, item, resumed)
    attempt = session.begin_attempt(case)
    try:
        profile = open_browser(session, case)
    except (GateError, OSError, subprocess.TimeoutExpired):
        session.refund_attempt(case)
        raise
    browser_name = sname(browser_case(session, case))
    events.append(session.dir, "case_started", case=case, attempt=attempt)
    lines = [f"{case}: {len(item['steps'])} step(s). Browser `{browser_name}` is open and signed in as {profile}.",
             f"Read the page: playwright-cli -s={browser_name} snapshot   (labels are in the page's language). Act on what you read, fix mistakes in the browser.",
             f"After each planned step: oqa live step {case} --n N --status PASS|FAIL --actual \"what the page showed\" --target \"<locator of the element the step is about>\"."
             f" If the click leaves the page, take the picture first: oqa live shot {case} --n N --target \"<locator>\"."]
    if workspace.load(session.ws).get("pgadmin_url"):
        lines.append(f"A step that confirms on the database: take the picture FIRST with oqa pgadmin shot {case} --n N --sql \"SELECT ...\" (MCP: pgadmin_shot; N is the next step), then record the step with live step (--actual = what the rows showed). Use this run's fixture key in the SQL. Do not open PgAdmin yourself.")
    for step in item["steps"]:
        lines.append(f"  {step['n']}. {step['action']}   ->   {step['expected']}")
        if step.get("evidence"):
            lines.append(f"     Evidence: {step['evidence']}")
    return {"case": case, "attempt": attempt, "browser": browser_name}, "\n".join(lines)


def _open_attempt(session, case):
    """(attempt, index of its case_started event) when the case has a run that has not finished, else None."""
    log = events.read(session.dir)
    started = next(((i, e) for i, e in reversed(list(enumerate(log))) if e["kind"] == "case_started" and e.get("case") == case), None)
    if not started:
        return None
    index, event = started
    if any(e["kind"] == "attempt_finished" and e.get("case") == case and e.get("attempt") == event["attempt"] for e in log[index:]):
        return None
    return event["attempt"], index


def _handed_over(session, case):
    """The open attempt of a case that was handed over and not yet taken up, else None."""
    found = _open_attempt(session, case)
    if not found:
        return None
    attempt, index = found
    log = events.read(session.dir)
    handovers = [e for e in log[index:] if e["kind"] == "handover" and e.get("case") == case and e.get("attempt") == attempt]
    return {"attempt": attempt, "handover": handovers[-1]} if handovers else None


def handover(session, case, reason, to=""):
    """Pass a case that is running to another worker. Unlike `recover`, the open attempt stays open: the steps already recorded, the evidence, the browser and
    the attempt count are kept, so nothing is redone and no recovery budget is spent. The new owner continues with `live start`."""
    from . import claims

    session.require("EXECUTE")
    _strategy_case(session, case)
    if len(reason.strip()) < 12:
        raise GateError("Say why the case changes hands and where the work stands (at least 12 characters).")
    found = _open_attempt(session, case)
    if not found:
        raise GateError(f"{case} has no running attempt to hand over. Use `oqa claim` for a case that has not started.")
    attempt = found[0]
    owner = (claims.table(session).get(case) or {}).get("agent", "")
    if to:
        claims.assign(session, case, to)
    else:
        claims.release(session, case, missing_ok=True)
    done = sorted(_recorded(session, case, attempt))
    events.append(session.dir, "handover", case=case, attempt=attempt, reason=reason.strip(), **{"from": owner, "to": to})
    claims.add_note(session, "coordinator", f"HANDOVER {case}: {reason.strip()} (steps recorded: {done or 'none'}; attempt {attempt} stays open)", case=case)
    return ({"case": case, "attempt": attempt, "steps_recorded": done},
            f"{case} handed over{' to ' + to if to else ''}. Attempt {attempt} and its {len(done)} recorded step(s) are kept; the new worker runs `oqa live start {case}` to continue.")


def _resume(session, case, item, taken):
    attempt = taken["attempt"]
    browser_name = sname(browser_case(session, case))
    try:
        _run(browser_case(session, case), "snapshot")
        browser = "still open"
    except GateError:
        profile = open_browser(session, case)
        browser = f"reopened as {profile} (the page position was lost: navigate back to where step {len(_recorded(session, case, attempt)) + 1} starts)"
    done = _recorded(session, case, attempt)
    nxt = len(done) + 1
    if nxt > len(item["steps"]) or any(step["status"] != "PASS" for step in done.values()):
        nxt = None
    lines = [f"{case}: attempt {attempt} CONTINUES after a handover ({taken['handover'].get('reason', '')}). Browser `{browser_name}` {browser}.",
             f"Steps already recorded: {sorted(done) or 'none'}. Do not redo them.",
             f"Next is step {nxt}." if nxt else "Review the recorded steps and close the case with the appropriate verdict."]
    for step in item["steps"]:
        mark = done[step["n"]]["status"] if step["n"] in done else "todo"
        lines.append(f"  {step['n']}. [{mark}] {step['action']}   ->   {step['expected']}")
    return {"case": case, "attempt": attempt, "browser": browser_name, "resumed": True, "next_step": nxt}, "\n".join(lines)


def recover(session, case, reason):
    """Close a worker's unfinished attempt after the coordinator diagnosed it."""
    from . import claims

    session.require("EXECUTE", "REPORT")           # the last case to finish moves the session to REPORT; reopening it moves the session back
    _strategy_case(session, case)
    if len(reason.strip()) < 12:
        raise GateError("Describe the worker failure and what should change before recovery.")
    state = session.state()
    result = state.get("results", {}).get(case)
    if result:
        if state.get("attempts", {}).get(case, 0) >= 2:
            raise GateError("This case has already used its one recovery attempt.")
        attempt = state.get("runs", {}).get(case)
        if not attempt:
            raise GateError("There is no live worker attempt to recover.")
        with session.update() as current:
            session.archive_result(current, case, f"coordinator diagnosis: {reason}")
            current["results"].pop(case, None)
            session._reconcile_dependencies(current)
            if current["phase"] == "REPORT":
                session._enter(current, "EXECUTE", f"{case} reopened after the coordinator's diagnosis")
        events.append(session.dir, "repair", case=case, attempt=attempt,
                      cause="Coordinator diagnosed an incorrect worker conclusion",
                      evidence=reason, change="Reassign and rerun with corrected interpretation/setup",
                      previous_status=result["status"])
        claims.release(session, case, missing_ok=True)
        return {"case": case, "attempt": attempt}, f"Reopened {case} after worker diagnosis. Prior evidence is retained; reassign using oqa claim."
    if state["phase"] == "REPORT":
        raise GateError(f"{case} has no result to reopen.")
    attempt = _attempt(session, case)
    try:
        _run(browser_case(session, case), "close")
    except GateError:
        pass
    events.append(session.dir, "attempt_finished", case=case, attempt=attempt, status="STOPPED", reason=reason)
    if case in claims.table(session):
        claims.release(session, case)
    return {"case": case, "attempt": attempt}, f"Closed attempt {attempt} for {case}. Reassign using oqa claim."


RECORD_ID_URL = re.compile(r"(?:waitForURL|goto)\s*\([^)]*?(?:action-\d+|/odoo/[\w.-]+)\\?/\d+|(?:waitForURL|goto)\s*\([^)]*?[#&?]id=\d+")
VAR_REF = re.compile(r"\$OQA_ID\((\w+)\)")
VAR_STORE = "oqa:var:"            # a run variable lives in the page's sessionStorage: it survives navigation within the tab and is created again by every replay

CAPTURE_ID_CODE = (
    "await page.evaluate(name => { "
    r"const m = location.href.match(/\/(\d+)(?:[?#]|$)/) || location.href.match(/[#&?]id=(\d+)/); "
    "if (!m) throw new Error('No record id in the address: open or save the record first'); "
    "sessionStorage.setItem('" + VAR_STORE + "' + name, m[1]); }, %s);")


def capture_id_code(name):
    """Playwright code that stores the id of the record open on screen in the run variable `name`. Recorded in the script, so every replay captures the id of ITS record."""
    if not re.fullmatch(r"\w+", name or ""):
        raise GateError("A variable name is letters, digits and underscore.")
    return CAPTURE_ID_CODE % json.dumps(name)


def expand_vars(code):
    """`$OQA_ID(name)` in a fragment becomes the lookup of that run variable, so the script never holds the id of a record a run created."""
    return VAR_REF.sub(lambda m: "(await page.evaluate(k => sessionStorage.getItem('" + VAR_STORE + "' + k), " + json.dumps(m.group(1)) + "))", code or "")


def act(session, case, command, arguments, code=None, record=False):
    session.require("EXECUTE")
    _strategy_case(session, case)
    attempt = _attempt(session, case)
    if record and command != "run-code":
        raise GateError("Record a stable Playwright fragment with command=run-code.")
    if command == "run-code":
        code = expand_vars(code)
    if record and command == "run-code" and RECORD_ID_URL.search(code or ""):
        raise GateError("Do not put a record id in a recorded URL: the id belongs to the record this run created, and every replay creates a different one. Store the id in a run variable "
                        "right after the record is saved (capture_id) and refer to it as $OQA_ID(name), for example new RegExp('action-90/' + $OQA_ID(rec)); or wait for something visible "
                        "(the record's key in the title, a field value, the statusbar).")
    fixed = record_names.foreign(session, case, code or " ".join(str(a) for a in arguments), workspace.load(session.ws)["test_prefix"])
    if fixed:
        raise GateError(f"This action types a fixed record name ({', '.join(fixed[:3])}). A name that is the same on every run meets what an earlier run left (a unique constraint, a record that cannot "
                        "be deleted), and the case then judges leftover data, not the application. Prepare this run's own data: get a key with fixture(action=key, label=...) (oqa fixture key) "
                        "and use it as the record's name and code, then create the record through the screen.")
    if command == "run-code":
        if not code:
            raise GateError("Use --code or --verified-code with an action fragment.")
        if any(not line.strip().startswith(ACTION_PREFIXES) for line in code.splitlines() if line.strip()):
            raise GateError("Action fragments use one await page action per line.")
        if record and re.search(r"\.waitForTimeout\s*\(|\bnetworkidle\b", code):
            raise GateError("Record a visible condition instead of a fixed delay.")
        output = run_actions(browser_case(session, case), code.splitlines())
        if record:
            events.append(session.dir, "action_recorded", case=case, attempt=attempt, code=code)
        return {"case": case, "output": output}, output
    elif command not in ("snapshot", "click", "fill", "press", "goto", "select", "type",
                         "check", "uncheck", "dialog-accept", "dialog-dismiss"):
        raise GateError("Choose a live browser action with --browser-command.")
    output = _run(browser_case(session, case), command, *arguments)
    return {"case": case, "output": output}, output


def select_profile(session, case, profile):
    session.require("EXECUTE")
    if not profile:
        raise GateError("Choose a workspace profile with --profile.")
    attempt = _attempt(session, case)
    current = open_browser(session, case, profile_override=profile)
    events.append(session.dir, "profile_selected", case=case, attempt=attempt, profile=current)
    return {"case": case, "profile": current}, f"Browser signed in as {current}."


_BOX = (
    "el => { el.scrollIntoView({block: 'center', inline: 'center'}); const r = el.getBoundingClientRect(); "
    "return JSON.stringify({x: r.x, y: r.y, w: r.width, h: r.height, vw: innerWidth, vh: innerHeight}); }"
)
_CODE = ("async page => {{ const el = {locator}; if (await el.count() !== 1) throw new Error('Evidence target must match exactly one element'); "
         "await el.scrollIntoViewIfNeeded({{timeout: 3000}}); const r = await el.boundingBox(); "
         "const v = page.viewportSize(); return JSON.stringify(r ? {{x: r.x, y: r.y, w: r.width, h: r.height, vw: v.width, vh: v.height}} : null); }}")


def _locator(target):
    """A snapshot ref (e15) goes to `eval`; anything else (a CSS selector, getByRole(...), locator(...)) is a Playwright locator run on the page."""
    if re.fullmatch(r"e\d+", target.strip()):
        return None
    if re.match(r"(getBy\w+|locator|frameLocator)\(", target.strip()):
        return "page." + target.strip()
    return "page.locator(" + json.dumps(target) + ")"


def _box(case, target):
    """Where the element is on the page now (viewport pixels), asked of the browser. None when it is not on the screen."""
    locator = _locator(target)
    try:
        out = _run(case, "--raw", "eval", _BOX, target.strip()) if locator is None else _run(case, "--raw", "run-code", _CODE.format(locator=locator))
    except GateError:
        return None
    found = re.search(r"\{[^{}]*\}", out.replace('\\"', '"'))
    if not found:
        return None
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return None
    x0, y0, x1, y1 = max(data["x"], 0), max(data["y"], 0), min(data["x"] + data["w"], data["vw"]), min(data["y"] + data["h"], data["vh"])
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (x0, y0, x1 - x0, y1 - y0), (data["vw"], data["vh"])


def _shot(case, path):
    work = _home()
    name = f"shot-{sname(case)}.png"
    _run(case, "screenshot", f"--filename={name}")
    produced = work / ".playwright-cli" / name
    if not produced.exists():
        produced = work / name
    if not produced.exists():
        raise GateError("playwright-cli took no screenshot.")
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(produced), str(path))


def _scale(box, viewport, picture):
    from PIL import Image
    width, _height = Image.open(picture).size
    k = width / viewport[0] if viewport[0] else 1
    x, y, w, h = box
    return (int(x * k), int(y * k), max(int(w * k), 2), max(int(h * k), 2))


def shot(session, case, n, target, from_pre=False, caption=None):
    session.require("EXECUTE")
    attempt = session.state().get("runs", {}).get(case) if from_pre else _attempt(session, case)
    steps = _strategy_case(session, case)["steps"]
    if not isinstance(n, int) or not 1 <= n <= len(steps):
        raise GateError("Choose a planned step number for the evidence.")
    language = workspace.load(session.ws).get("evidence_language", "vi")
    label = caption or captions.text(steps[n - 1], language, total=len(steps))
    if from_pre:
        if n not in _recorded(session, case, attempt):
            raise GateError("A supplemental pre-action picture requires an already recorded step.")
        folder = session.dir / "evidence" / re.sub(r"[^\w.-]+", "_", case) / f"a{attempt}"
        source, meta = folder / f"s{n}_pre_raw.png", read_json(folder / f"s{n}_pre.json", {})
        if not source.is_file() or not meta.get("box"):
            raise GateError("There is no saved live pre-action picture for this step.")
        suffix = uuid.uuid4().hex[:8]
        raw, annotated = folder / f"s{n}_supp_{suffix}_raw.png", folder / f"s{n}_supp_{suffix}_annotated.png"
        shutil.copyfile(source, raw)
        evidence.annotate(raw, [meta["box"]], label, annotated)
        error = evidence.check(raw, annotated)
        if error:
            raise GateError(error)
        events.append(session.dir, "step_evidence", case=case, attempt=attempt, n=n, source="saved_live_shot",
                      images=[{"raw": str(raw.relative_to(session.dir)), "annotated": str(annotated.relative_to(session.dir))}])
        return {"case": case, "n": n}, f"Saved live pre-action picture attached to step {n}; earlier evidence and verdict are retained."
    browser_id = browser_case(session, case)
    found = _box(browser_id, target)
    if not found:
        raise GateError(f"Nothing visible matches {target!r}: read the page (snapshot) and name the element again.")
    folder = session.dir / "evidence" / re.sub(r"[^\w.-]+", "_", case) / f"a{attempt}"
    if n in _recorded(session, case, attempt):
        suffix = uuid.uuid4().hex[:8]
        raw, annotated = folder / f"s{n}_supp_{suffix}_raw.png", folder / f"s{n}_supp_{suffix}_annotated.png"
        _shot(browser_id, raw)
        evidence.annotate(raw, [_scale(found[0], found[1], raw)], label, annotated)
        error = evidence.check(raw, annotated)
        if error:
            raise GateError(error)
        events.append(session.dir, "step_evidence", case=case, attempt=attempt, n=n, target=target,
                      images=[{"raw": str(raw.relative_to(session.dir)), "annotated": str(annotated.relative_to(session.dir))}])
        return {"case": case, "n": n}, f"Supplemental live evidence added to recorded step {n}; the earlier evidence is retained."
    if n != len(_recorded(session, case, attempt)) + 1:
        raise GateError("Capture evidence for the next planned step, in order.")
    suffix = uuid.uuid4().hex[:8]
    raw, annotated = folder / f"s{n}_pre_{suffix}_raw.png", folder / f"s{n}_pre_{suffix}_annotated.png"
    _shot(browser_id, raw)
    box = _scale(found[0], found[1], raw)
    evidence.annotate(raw, [box], label, annotated)
    # Keep every checkpoint; legacy paths identify the latest pre-action view.
    shutil.copyfile(raw, folder / f"s{n}_pre_raw.png")
    write_json(folder / f"s{n}_pre.json", {"box": box, "target": target})
    rel = lambda p: p.relative_to(session.dir).as_posix()
    events.append(session.dir, "evidence_captured", case=case, attempt=attempt, n=n,
                  images=[{"raw": rel(raw), "annotated": rel(annotated), "caption": label,
                           "target": target, "box": box}])
    return {"case": case, "n": n}, f"Picture of step {n} taken before the action (element framed). Now act, then: oqa live step {case} --n {n} --status PASS|FAIL --actual \"...\""


def _step_actions(log, case, attempt, n, verified_code):
    code = [line.strip() for line in expand_vars(verified_code).splitlines() if line.strip()]
    setup = {}
    recorded_actions = []
    recorded_path = []
    profile = None
    for event in log:
        if event.get("case") != case or event.get("attempt") != attempt:
            continue
        if event["kind"] == "step":
            setup.clear()
            recorded_actions.clear()
            recorded_path.clear()
        elif event["kind"] == "fixture_prepared":
            if event["key"] not in setup:
                setup[event["key"]] = event["code"]
                recorded_path.extend(event["code"].splitlines())
        elif event["kind"] == "action_recorded":
            fragment = [line.strip() for line in event["code"].splitlines() if line.strip()]
            recorded_actions.extend(fragment)
            recorded_path.extend(fragment)
        elif event["kind"] == "profile_selected":
            profile = event["profile"]
    if code:
        submitted = iter(line.rstrip(";") for line in code)
        for action in recorded_actions:
            if not any(line == action.rstrip(";") for line in submitted):
                raise GateError(f"Verified code omitted or reordered a recorded action ({action[:120]}). Include recorded actions in order, or omit --verified-code to use them directly.")
        flat = [line.strip().rstrip(";") for line in code]

        def included(fragment):                               # the whole fragment, in order: single generic lines (a Save click) say nothing
            lines = [line.strip().rstrip(";") for line in fragment.splitlines() if line.strip()]
            return any(flat[i:i + len(lines)] == lines for i in range(len(flat) - len(lines) + 1))

        missing = [fragment for fragment in setup.values() if not included(fragment)]
        own_page = [line for fragment in missing if fragment.lstrip().startswith("await page.goto(") for line in fragment.splitlines() if line.strip()]
        in_page = [line for fragment in missing if not fragment.lstrip().startswith("await page.goto(") for line in fragment.splitlines() if line.strip()]
        offset = 1 if n == 1 and code[0].startswith("await page.goto(") else 0
        code[offset:offset] = in_page                         # an in-page (RPC) fixture needs the step's page loaded first
        code[0:0] = own_page                                  # a UI fixture opens its own form: it runs before the step goes to its screen
    else:
        code = recorded_path
    return code, profile


def step(session, case, n, status, actual, target=None, verified_code=None, shows=None, assertion_code=None, *, require_checks=False):
    session.require("EXECUTE")
    attempt = _attempt(session, case)
    item = _strategy_case(session, case)
    steps = item["steps"]
    if not 1 <= n <= len(steps):
        raise GateError(f"{case} has {len(steps)} step(s); there is no step {n}.")
    done = _recorded(session, case, attempt)
    if n in done:
        raise GateError(f"Step {n} is already recorded ({done[n]['status']}). Fix things in the browser BEFORE recording; a step is recorded once.")
    if n != len(done) + 1:
        raise GateError(f"Record the steps in order: next is step {len(done) + 1}. Steps recorded in this attempt: {sorted(done) or 'none'}. "
                        "Do not skip ahead; if you checked later steps in the browser, redo them as steps in order (the page is still there), recording each.")
    if any(e["status"] == "FAIL" for e in done.values()):
        raise GateError("A step already failed: close the case with `oqa live end` (the steps after it are marked as not run).")
    if status not in ("PASS", "FAIL"):
        raise GateError("--status is PASS or FAIL.")
    if len(re.findall(r"\w+", actual or "")) < 3:
        raise GateError("--actual says what the page SHOWED for this step (a value, a message, a count), in a few words: it is compared with the expected result.")
    spec = steps[n - 1]
    folder = session.dir / "evidence" / re.sub(r"[^\w.-]+", "_", case) / f"a{attempt}"
    log = events.read(session.dir)
    captured = [event for event in log if event["kind"] == "evidence_captured" and event.get("case") == case
                and event.get("attempt") == attempt and event.get("n") == n]
    db = next((event.get("pgadmin_result") for event in reversed(captured) if event.get("source") == "pgadmin"), None)
    if status == "PASS" and db and db["error"]:
        raise GateError("The PgAdmin query failed. Capture a successful query before recording PASS.")
    if status == "PASS" and require_checks and spec.get("evidence") and not shows:
        raise GateError("Give --shows with the observed outcome for this step. Use --assert-code for a calculated value or an absence.")
    browser_id = browser_case(session, case)
    script_step = None
    checks = 0
    found = None
    if verified_code is not None or shows is not None or assertion_code is not None:
        if not target or not shows or "\n" in shows or "\n" in target:
            raise GateError("A replayable step needs a stable target and a single-line expected page text.")
        code, profile = _step_actions(log, case, attempt, n, verified_code)
        assertions = [line.strip() for line in expand_vars(assertion_code).splitlines() if line.strip()]
        values = specific_values(spec.get("expected"))
        if db and status == "PASS":
            check_database_values(db, values)
        found = _box(browser_id, target)
        script_step = [f"// step {n}", f"// frame: {target}", f"// shows: {shows}"]
        if found:
            script_step.append("// check-target: yes")
        if values and not assertions and not db:
            script_step.append("// check-values: " + json.dumps(values, ensure_ascii=False))
        script_step.extend(f"// assert: {line}" for line in assertions)
        script_step.extend(code)
        if profile:
            script_step.insert(2, f"// profile: {profile}")
        blocks, problems = parse_script("\n".join(script_step), [spec], check_search=True)
        if problems:
            raise GateError("\n".join(problems))
        if status == "PASS":
            commands = replay_checks(blocks[0], 1000 * (item.get("timeout_s") or 8))
            try:
                run_actions(browser_id, commands)
            except GateError as error:
                raise GateError(f"The replay check could not find 'shows' verbatim or verify the expected outcome: {error}") from error
            checks = len(commands) - 1 + len(blocks[0].get("check_values", [])) + (1 if db else 0)
    raw, annotated = folder / f"s{n}_raw.png", folder / f"s{n}_annotated.png"
    boxes, how = None, ""
    if target:
        if not script_step:
            found = _box(browser_id, target)
        if found:
            _shot(browser_id, raw)
            boxes, how = [_scale(found[0], found[1], raw)], "now"
    if boxes is None and status == "PASS" and spec.get("evidence"):
        raise GateError(f"Step {n} needs a visible result target for its evidence plan. A pre-action picture alone cannot prove the outcome; capture the resulting field, status, message or parent view.")
    if boxes is None and status == "PASS" and (folder / f"s{n}_pre.json").exists():  # compatibility for older plans with pre-action evidence only
        shutil.copyfile(folder / f"s{n}_pre_raw.png", raw)
        boxes, how = [tuple(read_json(folder / f"s{n}_pre.json")["box"])], "before"
    if boxes is None:
        if status == "PASS":
            raise GateError(f"No element is framed for step {n}: give --target \"<locator>\" of an element that is on the screen now (or take `oqa live shot` before the action). "
                            "A PASS without a framed element cannot be recorded.")
        _shot(browser_id, raw)
        from PIL import Image
        width, height = Image.open(raw).size
        boxes = [(0, 0, width, height)]
    language = workspace.load(session.ws).get("evidence_language", "vi")
    label = captions.text(spec, language, total=len(steps), status=status)
    evidence.annotate(raw, boxes, label, annotated, ok=status == "PASS")
    rel = lambda p: Path(p).relative_to(session.dir).as_posix()
    images = [image for event in captured for image in event["images"]]
    if how != "before" or not images:
        images.append({"raw": rel(raw), "annotated": rel(annotated),
                       "caption": label, "target": target, "box": boxes[0]})
    if script_step and how == "before":
        script_step.insert(2, "// frame-before: yes")
    events.append(session.dir, "step", case=case, attempt=attempt, n=n, status=status, caption=spec["action"], expected=spec["expected"], actual=actual.strip(),
                  checks=checks, images=images,
                  **({"verified_script": script_step} if script_step else {}))
    return {"case": case, "n": n, "status": status, "frame": how}, f"Step {n} recorded as {status} (frame drawn by oqa, {how or 'whole screen'}). " + (
        f"Next: step {n + 1}." if status == "PASS" and n < len(steps) else f"Next: oqa live end {case} --status {'PASS' if status == 'PASS' else 'FAIL'} --reason \"...\"")


def script_file(session, case, *, for_write=False):
    """Find the case script. Older workspace scripts can be read when their workbook and case are unambiguous."""
    state = session.state()
    name = re.sub(r"[^\w.-]+", "_", case)
    if state.get("channel"):
        fingerprint = hashlib.sha256(case.encode("utf-8")).hexdigest()[:8]
        return session.dir / "scripts" / f"{name}-{fingerprint}.spec.js"
    from . import regression

    base = paths.workspace_dir(session.ws) / "scripts"
    target = base / (regression.key_of(state["file"], case) + ".spec.js")
    if for_write or target.is_file():
        return target
    old_key = regression.legacy_key(state["file"], case)
    old = base / (old_key + ".spec.js")
    if old.is_file():
        for sid in paths.session_ids(session.ws):
            other = Session(session.ws, sid)
            other_state = other.state()
            if other_state.get("channel") or memory.stem_of(other_state["file"]) != memory.stem_of(state["file"]):
                continue
            if any(regression.legacy_key(other_state["file"], c["id"]) == old_key
                   and regression.key_of(other_state["file"], c["id"]) != regression.key_of(state["file"], case)
                   for c in other.cases()["cases"]):
                return target
        return old
    return target


SCRIPT_HELP = ("Script format, one block per planned step, in order:\n"
               "  // step 1\n  // frame: <a CSS selector or getByRole(...) of the element the step is about; not a snapshot ref>\n"
               "  // frame-before: yes          (only when the action leaves the page: the picture is taken before it)\n"
               "  // shows: <text the page must contain once the step is done>\n  await page.getByRole('button', { name: 'Save' }).click();   (the lines playwright-cli printed)")

ACTION_PREFIXES = ("await page.", "await Promise.all([page.waitForEvent(")
LITERAL_RECORD = re.compile(
    r"""^await page\.goto\(\s*['"](?:https?://[^/'"]+)?/(?:odoo|system)/(?:action-\d+|[\w.-]+)/\d+(?:[/?#'"]|$)"""
)
SEARCH_INPUT = re.compile(r"""^await page\.getByRole\(['"]searchbox['"]""")
SEARCH_ENTER = re.compile(r"""\.press\(['"]Enter['"]\)""")
SEARCH_RESULT = re.compile(r"""^await page\.getByRole\(['"](?:cell|row)['"]""")


def parse_script(text, steps, *, check_search=False):
    """The blocks of a case script: [{n, frame, before, shows, code}], and the problems that would make a replay meaningless."""
    blocks, current, problems = {}, None, []
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"//\s*step\s+(\d+)\b", line, re.I)
        if m:
            current = blocks.setdefault(int(m.group(1)), {"n": int(m.group(1)), "frame": "", "before": False, "shows": "", "code": []})
            continue
        if current is None or not line:
            continue
        meta = re.match(r"//\s*(frame-before|frame|shows|profile|check-target|check-values|assert)\s*:\s*(.*)$", line, re.I)
        if meta:
            key, value = meta.group(1).lower(), meta.group(2).strip()
            if key == "frame-before":
                current["before"] = value.lower() in ("yes", "true", "1")
            elif key == "check-target":
                current["check_target"] = value == "yes"
            elif key == "check-values":
                try:
                    values = json.loads(value)
                    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                        raise ValueError
                    current["check_values"] = values
                except ValueError:
                    problems.append(f"step {current['n']}: check-values must be a JSON list of visible values")
            elif key == "assert":
                if value.startswith("await page.waitForFunction("):
                    current.setdefault("assertions", []).append(value)
                else:
                    problems.append(f"step {current['n']}: assert must be an await page.waitForFunction(...) check")
            else:
                current[key] = value
        elif line.startswith(ACTION_PREFIXES):
            if re.search(r"\.waitForTimeout\s*\(|\bnetworkidle\b", line):
                problems.append(f"step {current['n']}: wait for an observable condition instead of a fixed delay")
            if LITERAL_RECORD.search(line):
                problems.append(f"step {current['n']}: open the run's record by its fixture key or search its unique code instead of using a literal record ID")
            current["code"].append(line if line.endswith(";") else line + ";")
        elif not line.startswith("//"):
            problems.append(f"step {current['n']}: only `await page...` lines are replayed, not: {line[:60]}")
    for step_ in steps:
        b = blocks.get(step_["n"])
        if not b:
            problems.append(f"step {step_['n']} has no block (// step {step_['n']})")
            continue
        if not b["frame"] or re.fullmatch(r"e\d+", b["frame"]):
            problems.append(f"step {step_['n']}: `// frame:` needs a locator that survives a reload (CSS or getByRole(...)), not a snapshot ref")
        if not b["shows"]:
            problems.append(f"step {step_['n']}: `// shows:` is missing: a replay has nothing to check without it")
        if check_search:
            for index, action in enumerate(b["code"]):
                if SEARCH_INPUT.match(action) and ").fill(" in action:
                    next_action = b["code"][index + 1] if index + 1 < len(b["code"]) else ""
                    if SEARCH_ENTER.search(next_action) or SEARCH_RESULT.match(next_action):
                        problems.append(f"step {b['n']}: use the search helper to click the Odoo search suggestion after filling the search box")
    first = blocks.get(steps[0]["n"]) if steps else None
    if first and first["n"] == 1 and not (first["code"] and first["code"][0].startswith("await page.goto(")):
        problems.append("step 1 must start with `await page.goto('<url of the screen>');`: a replay starts on a blank page and cannot rely on where your browser happened to be")
    return [blocks[s_["n"]] for s_ in steps if s_["n"] in blocks], problems


def preserved_checks(original, replacement):
    problems = []
    for before, after in zip(original, replacement):
        if before.get("check_target") and not after.get("check_target"):
            problems.append(f"step {before['n']}: keep the outcome check scoped to the result target")
        if not set(before.get("check_values", ())) <= set(after.get("check_values", ())):
            problems.append(f"step {before['n']}: keep all checked outcome values")
        if len(after.get("assertions", ())) < len(before.get("assertions", ())):
            problems.append(f"step {before['n']}: keep the explicit outcome assertions")
    return problems


def clone_for_replay(src, cases):
    """A new session that starts already confirmed, with the same account (sign-in), workspace and analysis, for running the saved scripts again. The report, the pictures
    and the dashboard then work on it as on any session. The workspace's current session stays what it was."""
    ws = workspace.load(src.ws)
    cases_doc, analysis = src.cases(), read_json(src.dir / "analysis.json")
    keep = set(cases)
    sub_cases = {**cases_doc, "cases": [c for c in cases_doc["cases"] if c["id"] in keep]}
    sub = {**analysis, "cases": [{**c, "depends_on": [d for d in c.get("depends_on", []) if d in keep]} for c in analysis["cases"] if c["id"] in keep]}
    channel = "path" if src.state().get("channel") else None
    new = Session.create(src.ws, src.state()["file"], sub_cases, channel=channel, make_current=False)
    write_json(new.dir / "analysis.json", sub)
    new.analysis_submitted()
    if (src.dir / "auth").is_dir():
        shutil.copytree(src.dir / "auth", new.dir / "auth", dirs_exist_ok=True)
    new.connected(src.state()["connect"] or {"profiles": {}, "version": ""})
    doc, text = analysis_mod.strategy(sub, sub_cases, ws)
    write_json(new.dir / "strategy.json", doc)
    (new.dir / "strategy.md").write_text(text, encoding="utf-8")
    new.strategy_ready(doc["sha"])
    new.confirm_current("replay")
    with new.update() as state:
        state["replay_of"] = src.id
    return new


DEFAULT_ACTION_DELAY_MS = 700
DEFAULT_STEP_DELAY_MS = 1500


def replay_delay(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 60000:
        raise GateError(f"{name} must be a whole number from 0 to 60000 milliseconds.")
    return value


def replay_delays(action_delay_ms=DEFAULT_ACTION_DELAY_MS, step_delay_ms=DEFAULT_STEP_DELAY_MS, fast=False):
    """The two viewing pauses of a replay. Fast mode is one switch that sets both to 0; application waits (`timeout_s`, settle) are not viewing pauses and stay."""
    if fast:
        return 0, 0
    return replay_delay(action_delay_ms, "action_delay_ms"), replay_delay(step_delay_ms, "step_delay_ms")


VIDEO_MODES = ("off", "on", "fail")        # off: no video; on: keep it; fail: record every run and keep the video only of a FAIL

# The step note drawn over the page, so a video (or a picture) explains itself. It lives in sessionStorage and is drawn again by an init script after every
# page load, because Odoo navigates and a note put on the page once would vanish. It never decides a result: a note that cannot be drawn is ignored.
_NOTE_DRAW = ("(() => { if (window.__oqaDraw) return; const draw = () => { try { const t = sessionStorage.getItem('__oqa_note'); let el = document.getElementById('__oqa_note');"
              " if (!t) { if (el) el.remove(); return; } if (!el) { el = document.createElement('div'); el.id = '__oqa_note'; el.style.cssText = 'position:fixed;left:0;right:0;"
              "bottom:0;z-index:2147483647;padding:10px 16px;background:rgba(20,20,19,.88);color:#fff;font:600 16px/1.4 sans-serif;pointer-events:none';"
              " (document.body || document.documentElement).appendChild(el); } el.textContent = t; } catch (e) {} }; window.__oqaDraw = draw;"
              " document.addEventListener('DOMContentLoaded', draw); window.addEventListener('load', draw); })()")


def _note(browser_id, text):
    """Show `text` over the page and put a chapter marker in the video. Best effort: never raises."""
    payload = ("async page => { await page.context().addInitScript(" + json.dumps(_NOTE_DRAW) + "); await page.evaluate(" + json.dumps(_NOTE_DRAW) + "); "
               "await page.evaluate(t => { try { sessionStorage.setItem('__oqa_note', t); } catch (e) {} window.__oqaDraw && window.__oqaDraw(); }, " + json.dumps(text) + "); }")
    for command in (("run-code", payload), ("video-chapter", text)):
        try:
            _run(browser_id, *command)
        except GateError:
            pass


class _Clock:
    """Where a replay spends its time: viewing pauses, calls into the browser (each one starts a playwright-cli process) and the rest."""

    def __init__(self):
        self.started = time.monotonic()
        self.steps, self.pause_s, self.call_s, self.calls = [], 0.0, 0.0, 0
        self.step = None

    def begin(self, n):
        self.step = {"n": n, "actions_s": 0.0, "check_s": 0.0, "pause_s": 0.0, "calls": 0}
        self.steps.append(self.step)

    def run(self, browser_id, lines, kind="actions_s"):
        began = time.monotonic()
        try:
            return run_actions(browser_id, lines)
        finally:
            spent = time.monotonic() - began
            self.call_s += spent
            self.calls += 1
            self.step[kind] += spent
            self.step["calls"] += 1

    def pause(self, ms):
        if ms:
            time.sleep(ms / 1000)
            self.pause_s += ms / 1000
            self.step["pause_s"] += ms / 1000

    def report(self):
        total = time.monotonic() - self.started
        r = lambda value: round(value, 2)
        return {"total_s": r(total), "pause_s": r(self.pause_s), "call_s": r(self.call_s), "calls": self.calls,
                "steps": [{k: (r(v) if isinstance(v, float) else v) for k, v in step.items()} for step in self.steps]}


def video_file(ws, script_key, env, run_id):
    """workspaces/<ws>/videos/<script>/<env>-<run>.webm: one folder per script, the environment and run in the name."""
    return paths.workspace_dir(ws) / "videos" / script_key.replace("/", "__") / f"{env}-{run_id}.webm"


def _visible_replay(session, ws, item, path, blocks, problems, run, movie, video, fresh, keep=True):
    from . import replay_browser

    def record(*args, **kwargs):
        if keep:
            replays.record(*args, **kwargs)

    case = item["id"]
    timing, failed, kept = {}, None, {}
    try:
        if problems:
            raise GateError("\n".join(problems))
        actions = {step["n"]: step for step in item["steps"]}
        timeout = 1000 * (item.get("timeout_s") or 8)
        prepared = []
        for block in blocks:
            prepared.append({**block, "code": [fresh(line) for line in block["code"]],
                             "frame_locator": _locator(fresh(block["frame"])),
                             "check": [fresh(line) for line in replay_checks(block, timeout)],
                             "label": captions.text(actions[block["n"]], run["caption_language"], total=len(blocks))})
        initial = item.get("profile") or ws["profiles"][0]["name"]
        with tempfile.TemporaryDirectory(prefix="oqa-replay-") as temp:
            output = replay_browser.run(session, ws, initial, prepared, run, Path(temp) if movie else None)
            timing = output["timing"]
            failed = output.get("failed_step")
            outcome = output["status"]
            if movie and (video == "on" or outcome == "FAIL"):
                for index, candidate in enumerate(output.get("videos", []), 1):
                    file = Path(candidate).resolve()
                    if Path(temp).resolve() not in file.parents or not file.is_file():
                        continue
                    destination = movie if not kept else movie.with_name(f"{movie.stem}-part{index}.webm")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(file), str(destination))
                    kept.setdefault("video", str(destination))
                    kept.setdefault("videos", []).append(str(destination))
            reason = output.get("reason")
            if outcome == "FAIL":
                reason = reason or "Replay failed."
                raise GateError(f"Step {failed} in {path.name}: {reason}" if failed else reason)
            record(session, case, outcome, timing=timing, **run, **kept,
                   **({"reason": reason, "failed_step": failed} if reason else {}))
    except GateError as error:
        record(session, case, "FAIL", reason=str(error)[:600], failed_step=failed, timing=timing, **run, **kept)
        raise
    return {"session": session.id, "case": case, "env": run["env"], "status": "CANCELLED" if outcome == "CANCELLED" else "COMPLETED",
            "replay": outcome, "action_delay_ms": run["action_delay_ms"], "step_delay_ms": run["step_delay_ms"],
            "timing": timing, **({"reason": reason} if reason else {})}


def replay_text(session, config, target_url, refresh_keys):
    connected = session.state().get("connect") or {}
    original_urls = [connected.get("url"), config["url"],
                     *(profile.get("landing_url") for profile in connected.get("profiles", {}).values())]
    origins = set()
    for url in filter(None, original_urls):
        original = urlsplit(url)
        if original.scheme and original.netloc:
            origins.add(f"{original.scheme}://{original.netloc}")
    target = urlsplit(target_url)
    new_origin = f"{target.scheme}://{target.netloc}"
    origin = re.compile("(?:" + "|".join(map(re.escape, sorted(origins))) + r")(?=$|[/#?\s'\"\x60])", re.I)
    return lambda text: refresh_keys(origin.sub(lambda _match: new_origin, text))


def replay(session, case, headed=True, action_delay_ms=DEFAULT_ACTION_DELAY_MS,
           step_delay_ms=DEFAULT_STEP_DELAY_MS, fast=False, env=None, video="off", caption_language=None, keep=True):
    """Open the saved script in a separate browser, keeping the original test session. The outcome goes to replay.json (Replay PASS/FAIL); the live result is not touched.
    keep=False runs it as a trial (a candidate script that may still be thrown away): nothing is recorded."""
    action_delay_ms, step_delay_ms = replay_delays(action_delay_ms, step_delay_ms, fast)
    if video not in VIDEO_MODES:
        raise GateError(f"video must be one of {', '.join(VIDEO_MODES)}.")
    config = workspace.load(session.ws)
    ws = workspace.for_env(config, env)
    env = ws["env"]
    language = caption_language or ws.get("replay_caption_language", "vi")
    if language not in captions.LANGUAGES:
        raise GateError("Caption language must be vi, en or ja.")
    item = _strategy_case(session, case)
    translation = captions.localized_cases(session).get(case, {})
    name = record_names.case_slug(session, case, item, translation)
    item = {**item, "steps": captions.localized_steps(item["steps"], translation)}
    path = script_file(session, case)
    if not path.is_file():
        raise GateError(f"{case} has no saved script.")
    key = hashlib.sha256(f"{session.ws}:{session.id}:{case}:{env}".encode("utf-8")).hexdigest()[:16]
    browser_id = f"replay-{key}"
    prefix = ws["test_prefix"]
    stamp = uuid.uuid4().hex[:10]
    replay_prefix = record_names.replay_stem(prefix, name) + f"r{stamp}_"
    sources = record_names.replay_sources(session, case, prefix, name=name)
    fresh = replay_text(session, config, ws["url"], record_names.rewriter(sources, replay_prefix))
    run = {"run_id": f"r{stamp}", "env": env, "prefix": replay_prefix, "headed": headed, "fast": bool(fast),
           "action_delay_ms": action_delay_ms, "step_delay_ms": step_delay_ms, "caption_language": language,
           "fixture_sources": sources}
    clock, failed = _Clock(), None
    blocks, problems = parse_script(path.read_text(encoding="utf-8"), item["steps"])
    timeout = 1000 * (item.get("timeout_s") or 8)
    from . import regression

    movie = video_file(session.ws, regression.key_of(session.state()["file"], case), env, run["run_id"]) if video != "off" else None
    if headed:
        return _visible_replay(session, ws, item, path, blocks, problems, run, movie, video, fresh, keep)
    capture = _home() / ".playwright-cli" / "videos" / f"{browser_id}-{run['run_id']}.webm" if movie else None
    recording = False

    def stop_video(status):
        """Close the recording; keep the file when asked to (`on`), or when the run failed (`fail`). Returns the path kept, else None."""
        nonlocal recording
        if not recording:
            return None
        recording = False
        try:
            _run(browser_id, "video-stop")
        except GateError:
            pass
        if capture.is_file():
            movie.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(capture), str(movie))
        if movie.is_file() and (video == "on" or status == "FAIL"):
            return str(movie)
        movie.unlink(missing_ok=True)
        return None

    try:
        if problems:
            raise GateError("\n".join(problems))
        profile = open_browser(session, case, headed=headed, browser_id=browser_id, env=env)
        if movie:
            capture.parent.mkdir(parents=True, exist_ok=True)
            try:
                _run(browser_id, "video-start", str(capture))
                recording = True
            except GateError:
                pass                                                         # no video is not a reason to fail the run
        for block in blocks:
            clock.begin(block["n"])
            try:
                if recording:
                    spec = next((x for x in item["steps"] if x["n"] == block["n"]), {})
                    _note(browser_id, captions.text(spec, language, total=len(item["steps"]))[:200])
                if block.get("profile") and block["profile"] != profile:
                    profile = open_browser(session, case, headed=headed, profile_override=block["profile"],
                                           browser_id=browser_id, env=env)
                code = [fresh(line) for line in block["code"]]
                if action_delay_ms:
                    for line in code:
                        clock.run(browser_id, [line])
                        clock.pause(action_delay_ms)
                else:
                    clock.run(browser_id, code)
                clock.run(browser_id, [fresh(line) for line in replay_checks(block, timeout)], "check_s")
                clock.pause(step_delay_ms)
            except GateError as error:
                failed = block["n"]
                raise GateError(f"Step {block['n']} in {path.name}: {error}") from error
        kept = stop_video("PASS")             # inside the try: the `finally` below must not meet a recording that already succeeded
    except GateError as error:
        kept = stop_video("FAIL")
        if keep:
            replays.record(session, case, "FAIL", reason=str(error)[:600], failed_step=failed, timing=clock.report(), **({"video": kept} if kept else {}), **run)
        raise
    finally:
        if recording:
            stop_video("FAIL")
        try:
            _run(browser_id, "close")
        except GateError:
            pass
    timing = clock.report()
    if keep:
        replays.record(session, case, "PASS", timing=timing, **({"video": kept} if kept else {}), **run)
    return {"session": session.id, "case": case, "env": env, "status": "COMPLETED", "replay": "PASS", "browser": sname(browser_id),
            "action_delay_ms": action_delay_ms, "step_delay_ms": step_delay_ms, "timing": timing}


def replay_all(session, cases=None, headed=False, action_delay_ms=DEFAULT_ACTION_DELAY_MS,
               step_delay_ms=DEFAULT_STEP_DELAY_MS, fast=False, env=None, video="off", caption_language=None):
    """Replay every case of the session that has a saved script (or the listed ones). Each outcome is recorded; one failure does not stop the rest."""
    action_delay_ms, step_delay_ms = replay_delays(action_delay_ms, step_delay_ms, fast)
    order = read_json(session.dir / "strategy.json")["order"]
    wanted = [case for case in order if (cases is None or case in cases) and script_file(session, case).is_file()]
    if not wanted:
        raise GateError("This session has no saved scripts to replay.")
    out = []
    for case in wanted:
        try:
            result = replay(session, case, headed=headed, action_delay_ms=action_delay_ms, step_delay_ms=step_delay_ms,
                            fast=fast, env=env, video=video, caption_language=caption_language)
            if result["replay"] == "CANCELLED":
                out.append({"case": case, "replay": "CANCELLED", "reason": result["reason"]})
                break
            out.append({"case": case, "replay": "PASS", "total_s": result["timing"]["total_s"]})
        except GateError as error:
            out.append({"case": case, "replay": "FAIL", "reason": str(error)[:300]})
    return out


def verify_replay(src, case, headed=False):
    """Run a case's saved script again, on the same account and Odoo, in a new session, with a picture of every step. Stops at the first step that does not show what it did."""
    item = _strategy_case(src, case)
    path = script_file(src, case)
    if not path.is_file():
        raise GateError(f"{case} has no saved script yet (a PASS keeps it): run the case first.")
    blocks, problems = parse_script(path.read_text(encoding="utf-8"), item["steps"])
    if problems:
        raise GateError("The saved script cannot be replayed:\n  " + "\n  ".join(problems) + "\n" + SCRIPT_HELP)
    new = clone_for_replay(src, [case])
    ws = workspace.load(src.ws)
    stamp = uuid.uuid4().hex[:10]
    prefix = ws["test_prefix"]
    name = record_names.case_slug(src, case, item)
    target = record_names.replay_stem(prefix, name) + f"r{stamp}_"
    fresh = record_names.rewriter(record_names.replay_sources(src, case, prefix, name=name), target)
    attempt = new.begin_attempt(case)
    profile = open_browser(new, case, headed=headed)
    browser_id = browser_case(new, case)
    events.append(new.dir, "case_started", case=case, attempt=attempt)
    failed, recorded = "", False
    for b in blocks:
        frame, shows, code = fresh(b["frame"]), fresh(b["shows"]), [fresh(c) for c in b["code"]]
        try:
            if b.get("profile") and b["profile"] != profile:
                profile = open_browser(new, case, headed=headed, profile_override=b["profile"])
            if b["before"] and code:                                  # the picture is taken just before the LAST action (the one that leaves the page), when its element is there
                if len(code) > 1:
                    run_actions(browser_id, code[:-1])
                try:
                    shot(new, case, b["n"], frame)
                except GateError:
                    pass                                              # not there yet: the frame is taken after the step instead
                code = code[-1:]
            checks = [fresh(line) for line in replay_checks(b, 1000 * (item.get("timeout_s") or 8))]
            run_actions(browser_id, [*code, *checks])
            step(new, case, b["n"], "PASS", f"Trang hiển thị «{shows}».", frame)
        except GateError as error:
            failed = f"step {b['n']}: {str(error)[:200]}"
            for where in (frame, "body"):
                try:
                    step(new, case, b["n"], "FAIL", f"Replay stopped: {str(error)[:300]}", where)
                    recorded = True
                    break
                except GateError:
                    continue                                          # even the picture failed (a dialog blocks the browser): the case still ends, as BLOCKED
            break
    if headed:
        time.sleep(3)                                              # the user is watching: leave the last screen up for a moment
    if failed and not recorded:
        diagnose(new, case, "automation", "Replay could not take a picture of the failing step: " + failed, tried="the step frame and the whole page body")
        end(new, case, "BLOCKED", "Replay could not take a picture: " + failed)
    elif failed:
        end(new, case, "FAIL", "Replay: " + failed, script=str(path), control="the saved script's own controls, replayed exactly as recorded")
    else:
        end(new, case, "PASS", "", script=str(path))
    return new, ("BLOCKED" if failed and not recorded else "FAIL" if failed else "PASS"), failed


def keep_script(session, case, script):
    """Replace the saved script of a case (after fixing it by hand): it must pass the same check as the one a PASS keeps."""
    steps = _strategy_case(session, case)["steps"]
    source = Path(script).expanduser()
    if not source.is_file():
        raise GateError(f"{script} is not a file.")
    blocks, problems = parse_script(source.read_text(encoding="utf-8"), steps)
    previous = script_file(session, case)
    if previous.is_file() and not problems:
        original, _ = parse_script(previous.read_text(encoding="utf-8"), steps)
        problems.extend(preserved_checks(original, blocks))
    if problems:
        raise GateError("The script cannot be replayed:\n  " + "\n  ".join(problems) + "\n" + SCRIPT_HELP)
    target = script_file(session, case, for_write=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    return {"case": case}, f"Script of {case} kept at {target}."


def diagnose(session, case, category, detail, tried="", need=""):
    """A worker that is stuck says what it found, concretely, before it may close the case as BLOCKED or PENDING. The coordinator reads it in the shared notes.
    category: automation = our script/selector/browser is the problem; precondition = the test lacks data, a document or a decision (needs the user)."""
    from . import claims

    session.require("EXECUTE")
    attempt = _attempt(session, case)
    if category not in ("automation", "precondition"):
        raise GateError("category is automation (our selector, script or browser) or precondition (missing data, document or decision). An application defect is a FAIL with its evidence.")
    if len(re.findall(r"\w+", detail or "")) < 6:
        raise GateError("Describe the diagnosis in a sentence: which control or condition, what you saw, what you expected.")
    if category == "automation" and len(re.findall(r"\w+", tried or "")) < 4:
        raise GateError("An automation diagnosis lists what was already tried (--tried), for example the other locators and what each one matched.")
    if category == "precondition" and len(re.findall(r"\w+", need or "")) < 3:
        raise GateError("A precondition diagnosis says what is needed to continue (--need): the data, the document or the decision.")
    events.append(session.dir, "diagnosis", case=case, attempt=attempt, category=category, detail=detail.strip(),
                  **({"tried": tried.strip()} if tried else {}), **({"need": need.strip()} if need else {}))
    owner = (claims.table(session).get(case) or {}).get("agent", "worker")
    claims.add_note(session, owner, f"DIAGNOSIS [{category}] {detail.strip()}" + (f" | tried: {tried.strip()}" if tried else "") + (f" | needs: {need.strip()}" if need else ""), case=case)
    return {"case": case, "category": category}, f"Diagnosis recorded and sent to the coordinator. Fix what you can; close as BLOCKED/PENDING only if it cannot be done: oqa live end {case} --status BLOCKED --category {category} --reason \"...\""


_NUMBER = re.compile(r"[+-]?\d+(?:[.,/:\-]\d+)*")
_QUOTED = re.compile(r"[\"'“‘「『]([^\"'”’」』]{2,60})[\"'”’」』]")
_DOC_CODE = re.compile(r"\bERR[_-]?\d+\b", re.I)


def specific_values(expected):
    """The exact numbers, dates and quoted values named in the expected result. Message codes
    that exist only in the design document (ERR_xxx) are not UI text and are never required to appear."""
    text = _DOC_CODE.sub(" ", expected or "")
    values = [m.group(0) for m in _NUMBER.finditer(text)]
    values += [q for q in _QUOTED.findall(text) if not _NUMBER.fullmatch(q)]
    return list(dict.fromkeys(values))


def check_database_values(result, values):
    """The numbers and dates of the expected result must be among the rows. Words are not checked here: the database keeps keys (state = 'sale'), not the labels
    the screen shows. The first cell of each row is PgAdmin's row number, not data."""
    observed = " ".join(str(value) for row in result["rows"] for value in row[1:])
    seen = {form for token in _NUMBER.findall(observed) for form in number_forms(token)}
    if result["total"] is not None:
        seen |= number_forms(str(result["total"]))
    missing = [v for v in values if _NUMBER.fullmatch(v) and not number_forms(v) & seen]
    if missing:
        raise GateError("The PgAdmin result is missing expected values: " + ", ".join(missing))


def end(session, case, status, reason, script=None, category=None, control=None):
    session.require("EXECUTE")
    attempt = _attempt(session, case)
    steps = _strategy_case(session, case)["steps"]
    done = _recorded(session, case, attempt)
    if status == "PASS":
        missing = [s["n"] for s in steps if done.get(s["n"], {}).get("status") != "PASS"]
        if missing:
            raise GateError(f"PASS needs every step recorded as PASS with its picture; missing or not passed: step(s) {missing}. Record them (oqa live step) or close as FAIL / BLOCKED.")
    elif status == "FAIL" and not any(e["status"] == "FAIL" for e in done.values()):
        raise GateError("FAIL needs a step recorded as FAIL with its picture (oqa live step ... --status FAIL).")
    if status != "PASS" and not (reason or "").strip():
        raise GateError("Say why (--reason).")
    category = "" if status == "PASS" else category or ""
    if status == "FAIL":
        if category not in ("", "application"):
            raise GateError("FAIL means the application behaves differently from the expected result. A problem in our script or a missing precondition is BLOCKED/PENDING with a diagnosis.")
        if len(re.findall(r"\w+", control or "")) < 4:
            raise GateError("Before a functional FAIL, verify you operated the right control: --control says which element was used (its label or locator) and how you confirmed it is the one "
                            "the testcase means (snapshot, role, position), for example that you clicked the link inside the cell rather than the empty cell area.")
        category = "application"
    elif status in ("BLOCKED", "PENDING"):
        diagnoses = [e for e in events.read(session.dir) if e["kind"] == "diagnosis" and e.get("case") == case and e.get("attempt") == attempt]
        if not diagnoses:
            raise GateError(f"{status} needs a concrete diagnosis first, so the coordinator can help instead of the case being closed because a selector was not found or time ran out: "
                            f"oqa live diagnose {case} --category automation|precondition --detail \"...\" --tried \"...\" (precondition: --need \"...\").")
        category = category or diagnoses[-1]["category"]
        if category not in ("automation", "precondition"):
            raise GateError(f"{status} is an automation issue or a missing test precondition: --category automation|precondition.")
        if diagnoses[-1]["category"] != category:
            raise GateError(f"The diagnosis says {diagnoses[-1]['category']}; the conclusion says {category}. Diagnose again or use the same category.")
    complete = bool(steps) and all(done.get(step["n"], {}).get("verified_script") for step in steps)
    if status == "PASS" or (status == "FAIL" and (script or complete)):
        source = Path(script).expanduser() if script else None
        if source is None and complete:
            name = re.sub(r"[^\w.-]+", "_", case)
            source = session.dir / "logs" / f"case-{name}-a{attempt}.js"
            source.parent.mkdir(exist_ok=True)
            source.write_text("\n\n".join("\n".join(done[n]["verified_script"]) for n in sorted(done)) + "\n",
                              encoding="utf-8")
        if not source or not source.is_file() or "await page" not in source.read_text(encoding="utf-8"):
            raise GateError("A PASS keeps the script of the case: --script <file> with the Playwright code playwright-cli printed for your actions. The next run replays it without an agent.\n" + SCRIPT_HELP)
        blocks, problems = parse_script(source.read_text(encoding="utf-8"), steps)
        if complete and not problems:
            original, _ = parse_script("\n".join("\n".join(done[n]["verified_script"]) for n in sorted(done)), steps)
            problems.extend(preserved_checks(original, blocks))
        if problems:
            raise GateError("The script cannot be replayed:\n  " + "\n  ".join(problems) + "\n" + SCRIPT_HELP)
        target_file = script_file(session, case, for_write=True)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target_file.resolve():
            shutil.copyfile(source, target_file)
    for s in steps:                                                              # what was not reached is shown as not run, never as passed
        if s["n"] not in done:
            events.append(session.dir, "step", case=case, attempt=attempt, n=s["n"], status="SKIPPED", caption=s["action"], expected=s["expected"], actual="not run", checks=0, images=[])
    failed = next((n for n, e in sorted(done.items()) if e["status"] == "FAIL"), None)
    basis = ""
    if status == "FAIL" and failed:                                              # what the failed step was judged against: the design first, else the testcase
        basis = analysis_mod._basis(next((s for s in steps if s["n"] == failed), {}))
        if basis and basis not in (reason or ""):
            reason = f"{reason} [{basis}]"
    events.append(session.dir, "attempt_finished", case=case, attempt=attempt, status=status, reason=reason or "", script_sha="live", **({"failed_step": failed} if failed else {}),
                  **({"category": category} if category else {}), **({"control": control.strip()} if control else {}), **({"basis": basis} if basis else {}))
    session.finish_case(case, status, reason or "", category, basis)
    try:
        _run(browser_case(session, case), "close")
    except GateError:
        pass
    return {"case": case, "status": status}, f"{case} recorded as {status}. Next: " + session.summary()["next"]
