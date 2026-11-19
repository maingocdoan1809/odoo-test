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

from . import analysis as analysis_mod
from . import connect, evidence, events, memory, paths, workspace
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
    done = subprocess.run([*command, f"-s={sname(case)}", *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=str(work),
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    out = (done.stdout or "") + (done.stderr or "")
    if done.returncode or "### Error" in out:
        raise GateError(f"playwright-cli {args[0]} failed: {out[-400:].strip()}")
    return done.stdout or ""


_JSON_STRING = r'"(?:\\.|[^"\\])*"'
_CSV_ACTION = re.compile(
    r"^await Promise\.all\(\[page\.waitForEvent\('filechooser'\), "
    r"page\.getByRole\('button', \{name: (" + _JSON_STRING + r"), exact: true\}\)\.click\(\)\]\)"
    r"\.then\(\(\[chooser\]\) => chooser\.setFiles\(\{name: (" + _JSON_STRING
    + r"), mimeType: 'text/csv', buffer: Buffer\.from\((" + _JSON_STRING + r")\)\}\)\);$"
)
_RELATIVE_GOTO = re.compile(r"""^(await page\.goto\(\s*)(['"])(/(?!/)[^'"\\]*)\2""")


def literal_check(text, timeout):
    return ("await page.waitForFunction(text => document.body.innerText.includes(text), "
            + json.dumps(text) + f", {{timeout: {timeout}}});")


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
                lambda match: match[1] + "new URL(" + json.dumps(match[3]) + ", page.url()).href",
                line, count=1))
    return flush() or result


def _strategy_case(session, case):
    if case not in read_json(session.dir / "strategy.json")["order"]:
        raise GateError(f"{case} is not in the confirmed strategy.")
    return {c["id"]: c for c in read_json(session.dir / "analysis.json")["cases"]}[case]


def open_browser(session, case, headed=False, refresh=True, profile_override=None, browser_id=None):
    """The case's own browser, already signed in as its profile. oqa loads the saved sign-in from a private copy that is removed at once: nobody reads it."""
    ws = workspace.load(session.ws)
    item = _strategy_case(session, case)
    profile = profile_override or item.get("profile") or ws["profiles"][0]["name"]
    if not any(account["name"] == profile for account in ws["profiles"]):
        raise GateError(f"Unknown workspace profile {profile}.")
    state = connect.storage_path(session, profile)
    if not state:
        raise GateError(f"No signed-in state for profile {profile}: run `oqa connect` first.")
    work = _home()
    (work / ".playwright-cli").mkdir(parents=True, exist_ok=True)
    chromium = sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))      # the Chromium oqa itself uses: no Chrome has to be installed
    browser_id = browser_id or browser_case(session, case)
    config = work / f"config-{sname(browser_id)}.json"
    from .browser import headless_channel

    channel = headless_channel()
    binary_options = ({"executablePath": str(chromium[-1])} if chromium else
                      {"channel": channel} if channel else {})
    write_json(config, {"browser": {"browserName": "chromium", "launchOptions": {"headless": not headed, **binary_options},
                                    "contextOptions": {"viewport": {"width": 1440, "height": 900}}}})
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
                                profile_override=profile, browser_id=browser_id)
        raise GateError(f"The saved sign-in did not work (landed on {landed.group(1)}): run `oqa init` to save the password, or sign in again.")
    return profile


def _recorded(session, case, attempt):
    """{n: step event} of this attempt."""
    return {e["n"]: e for e in events.read(session.dir) if e["kind"] == "step" and e.get("case") == case and e.get("attempt") == attempt}


def _attempt(session, case):
    mine = [e for e in events.read(session.dir) if e["kind"] == "case_started" and e.get("case") == case]
    if not mine:
        raise GateError(f"{case} was not started: oqa live start {case}")
    attempt = mine[-1]["attempt"]
    if any(e["kind"] == "attempt_finished" and e.get("case") == case and e.get("attempt") == attempt for e in events.read(session.dir)):
        raise GateError(f"{case} attempt {attempt} is already closed.")
    return attempt


def start(session, case):
    session.require("EXECUTE")
    item = _strategy_case(session, case)
    if session.state().get("channel"):
        from . import claims

        if not claims.table(session).get(case, {}).get("fresh"):
            raise GateError(f"Claim {case} with `oqa claim --agent WORKER --case {case}` before starting.")
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
    for step in item["steps"]:
        lines.append(f"  {step['n']}. {step['action']}   ->   {step['expected']}")
    return {"case": case, "attempt": attempt, "browser": browser_name}, "\n".join(lines)


def recover(session, case, reason):
    """Close a worker's unfinished attempt after the coordinator diagnosed it."""
    from . import claims

    session.require("EXECUTE")
    _strategy_case(session, case)
    attempt = _attempt(session, case)
    if len(reason.strip()) < 12:
        raise GateError("Describe the worker failure and what should change before recovery.")
    try:
        _run(browser_case(session, case), "close")
    except GateError:
        pass
    events.append(session.dir, "attempt_finished", case=case, attempt=attempt, status="STOPPED", reason=reason)
    if case in claims.table(session):
        claims.release(session, case)
    return {"case": case, "attempt": attempt}, f"Closed attempt {attempt} for {case}. Reassign using oqa claim."


def act(session, case, command, arguments, code=None, record=False):
    session.require("EXECUTE")
    _strategy_case(session, case)
    attempt = _attempt(session, case)
    if record and command != "run-code":
        raise GateError("Record a stable Playwright fragment with command=run-code.")
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
_CODE = ("async page => {{ const el = {locator}.first(); await el.scrollIntoViewIfNeeded({{timeout: 3000}}); const r = await el.boundingBox(); "
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


def shot(session, case, n, target):
    session.require("EXECUTE")
    attempt = _attempt(session, case)
    browser_id = browser_case(session, case)
    found = _box(browser_id, target)
    if not found:
        raise GateError(f"Nothing visible matches {target!r}: read the page (snapshot) and name the element again.")
    folder = session.dir / "evidence" / re.sub(r"[^\w.-]+", "_", case) / f"a{attempt}"
    _shot(browser_id, folder / f"s{n}_pre_raw.png")
    box = _scale(found[0], found[1], folder / f"s{n}_pre_raw.png")
    write_json(folder / f"s{n}_pre.json", {"box": box})
    return {"case": case, "n": n}, f"Picture of step {n} taken before the action (element framed). Now act, then: oqa live step {case} --n {n} --status PASS|FAIL --actual \"...\""


def step(session, case, n, status, actual, target=None, verified_code=None, shows=None):
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
        raise GateError(f"Record the steps in order: next is step {len(done) + 1}.")
    if any(e["status"] == "FAIL" for e in done.values()):
        raise GateError("A step already failed: close the case with `oqa live end` (the steps after it are marked as not run).")
    if status not in ("PASS", "FAIL"):
        raise GateError("--status is PASS or FAIL.")
    if len(re.findall(r"\w+", actual or "")) < 3:
        raise GateError("--actual says what the page SHOWED for this step (a value, a message, a count), in a few words: it is compared with the expected result.")
    spec = steps[n - 1]
    folder = session.dir / "evidence" / re.sub(r"[^\w.-]+", "_", case) / f"a{attempt}"
    script_step = None
    if verified_code is not None or shows is not None:
        if not target or not shows or "\n" in shows or "\n" in target:
            raise GateError("A replayable step needs a stable target and a single-line expected page text.")
        code = [line.strip() for line in (verified_code or "").splitlines() if line.strip()]
        setup = {}
        recorded_actions = []
        profile = None
        for event in events.read(session.dir):
            if event.get("case") != case or event.get("attempt") != attempt:
                continue
            if event["kind"] == "step":
                setup.clear()
                recorded_actions.clear()
            elif event["kind"] == "fixture_prepared":
                setup.setdefault(event["key"], event["code"])
            elif event["kind"] == "action_recorded":
                recorded_actions.extend(line.strip() for line in event["code"].splitlines() if line.strip())
            elif event["kind"] == "profile_selected":
                profile = event["profile"]
        if recorded_actions and not code:
            code = recorded_actions
        setup_code = [line for line in setup.values() if line not in code]
        offset = 1 if n == 1 and code and code[0].startswith("await page.goto(") else 0
        code[offset:offset] = setup_code
        script_step = [f"// step {n}", f"// frame: {target}", f"// shows: {shows}", *code]
        if profile:
            script_step.insert(2, f"// profile: {profile}")
        _, problems = parse_script("\n".join(script_step), [spec], check_search=True)
        if problems:
            raise GateError("\n".join(problems))
        if status == "PASS" and session.state().get("channel"):
            try:
                run_actions(browser_case(session, case), [literal_check(shows, 1500)])
            except GateError as error:
                raise GateError("The replay check could not find 'shows' verbatim on the page. Use a short visible label or value for shows, keep the observation in actual, and put specific checks in the Playwright code. The step has not been recorded.") from error
    raw, annotated = folder / f"s{n}_raw.png", folder / f"s{n}_annotated.png"
    browser_id = browser_case(session, case)
    boxes, how = None, ""
    if target:
        found = _box(browser_id, target)
        if found:
            _shot(browser_id, raw)
            boxes, how = [_scale(found[0], found[1], raw)], "now"
    if boxes is None and (folder / f"s{n}_pre.json").exists():                  # the element is gone after the action: show the page where it was acted on
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
    evidence.annotate(raw, boxes, f"Step {n}/{len(steps)} {status}: {spec['action']}", annotated, ok=status == "PASS")
    rel = lambda p: Path(p).relative_to(session.dir).as_posix()
    if script_step and how == "before":
        script_step.insert(2, "// frame-before: yes")
    events.append(session.dir, "step", case=case, attempt=attempt, n=n, status=status, caption=spec["action"], expected=spec["expected"], actual=actual.strip(),
                  checks=1, images=[{"raw": rel(raw), "annotated": rel(annotated)}],
                  **({"verified_script": script_step} if script_step else {}))
    return {"case": case, "n": n, "status": status, "frame": how}, f"Step {n} recorded as {status} (frame drawn by oqa, {how or 'whole screen'}). " + (
        f"Next: step {n + 1}." if status == "PASS" and n < len(steps) else f"Next: oqa live end {case} --status {'PASS' if status == 'PASS' else 'FAIL'} --reason \"...\"")


def script_file(session, case):
    """Where the case's script is kept: scripts/<workbook>/<case>.spec.js, beside what earlier sessions of the same workbook left."""
    state = session.state()
    name = re.sub(r"[^\w.-]+", "_", case)
    if state.get("channel"):
        fingerprint = hashlib.sha256(case.encode("utf-8")).hexdigest()[:8]
        return session.dir / "scripts" / f"{name}-{fingerprint}.spec.js"
    folder = paths.workspace_dir(session.ws) / "scripts" / memory.stem_of(state["file"])
    return folder / (name + ".spec.js")


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
        meta = re.match(r"//\s*(frame-before|frame|shows|profile)\s*:\s*(.*)$", line, re.I)
        if meta:
            key, value = meta.group(1).lower(), meta.group(2).strip()
            if key == "frame-before":
                current["before"] = value.lower() in ("yes", "true", "1")
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


def replay(session, case, headed=True):
    """Open the saved script in a separate browser, keeping the original test session."""
    item = _strategy_case(session, case)
    path = script_file(session, case)
    if not path.is_file():
        raise GateError(f"{case} has no saved script.")
    blocks, problems = parse_script(path.read_text(encoding="utf-8"), item["steps"])
    if problems:
        raise GateError("\n".join(problems))
    key = hashlib.sha256(f"{session.ws}:{session.id}:{case}".encode("utf-8")).hexdigest()[:16]
    browser_id = f"replay-{key}"
    try:
        profile = open_browser(session, case, headed=headed, browser_id=browser_id)
        for block in blocks:
            try:
                if block.get("profile") and block["profile"] != profile:
                    profile = open_browser(session, case, headed=headed, profile_override=block["profile"],
                                           browser_id=browser_id)
                run_actions(browser_id, block["code"])
            except GateError as error:
                raise GateError(f"Step {block['n']} in {path.name}: {error}") from error
    finally:
        if not headed:
            try:
                _run(browser_id, "close")
            except GateError:
                pass
    return {"session": session.id, "case": case, "status": "COMPLETED", "browser": sname(browser_id)}


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
    prefix = ws.get("test_prefix") or "ZZ_AUTOTEST_"
    fresh = lambda text: text.replace(prefix, f"{prefix}r{stamp}_")
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
            wait = literal_check(shows, 1000 * (item.get("timeout_s") or 8))
            run_actions(browser_id, [*code, wait])
            step(new, case, b["n"], "PASS", f"The page showed: {shows}", frame)
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
        end(new, case, "BLOCKED", "Replay could not take a picture: " + failed)
    elif failed:
        end(new, case, "FAIL", "Replay: " + failed, script=str(path))
    else:
        end(new, case, "PASS", "", script=str(path))
    return new, ("BLOCKED" if failed and not recorded else "FAIL" if failed else "PASS"), failed


def keep_script(session, case, script):
    """Replace the saved script of a case (after fixing it by hand): it must pass the same check as the one a PASS keeps."""
    steps = _strategy_case(session, case)["steps"]
    source = Path(script).expanduser()
    if not source.is_file():
        raise GateError(f"{script} is not a file.")
    _blocks, problems = parse_script(source.read_text(encoding="utf-8"), steps)
    if problems:
        raise GateError("The script cannot be replayed:\n  " + "\n  ".join(problems) + "\n" + SCRIPT_HELP)
    target = script_file(session, case)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    return {"case": case}, f"Script of {case} kept at {target}."


def end(session, case, status, reason, script=None):
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
        _blocks, problems = parse_script(source.read_text(encoding="utf-8"), steps)
        if problems:
            raise GateError("The script cannot be replayed:\n  " + "\n  ".join(problems) + "\n" + SCRIPT_HELP)
        target_file = script_file(session, case)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target_file.resolve():
            shutil.copyfile(source, target_file)
    for s in steps:                                                              # what was not reached is shown as not run, never as passed
        if s["n"] not in done:
            events.append(session.dir, "step", case=case, attempt=attempt, n=s["n"], status="SKIPPED", caption=s["action"], expected=s["expected"], actual="not run", checks=0, images=[])
    failed = next((n for n, e in sorted(done.items()) if e["status"] == "FAIL"), None)
    events.append(session.dir, "attempt_finished", case=case, attempt=attempt, status=status, reason=reason or "", script_sha="live", **({"failed_step": failed} if failed else {}))
    session.finish_case(case, status, reason or "")
    try:
        _run(browser_case(session, case), "close")
    except GateError:
        pass
    return {"case": case, "status": status}, f"{case} recorded as {status}. Next: " + session.summary()["next"]
