"""Read and verify a query in the workspace's PgAdmin, then keep the query and result picture as evidence."""
import json
import re
from contextlib import contextmanager
from urllib.parse import urljoin

from . import browser
from .secrets import SecretStore
from .state import GateError
from .workspace import pgadmin_secret_ref

SLOW_MS = 60000
TREE_MS = 20000          # the tree is already on the page: a name that is not there after this long is not coming
READ_ONLY = {"select", "with", "show", "explain", "values", "table"}
FORBIDDEN = {"insert", "update", "delete", "drop", "alter", "truncate", "create", "grant", "revoke", "copy", "call", "do", "vacuum", "reindex", "cluster", "comment", "merge", "lock", "refresh", "reassign", "security", "set", "listen", "notify", "into"}
_TOKENS = re.compile(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/)", re.S)     # quoted text and comments, read left to right so neither hides the other
_DATABASE_STEP = re.compile(
    r"\bpg\s*admin\b|\b(?:xác nhận|kiểm tra|đối chiếu|verify|validate|confirm|check)\s+"
    r"(?:(?:[^.\n]{0,75}\s)?(?:trong|trên|tại|in|on|against|via)\s+)?(?:the\s+)?(?:database|postgresql|cơ sở dữ liệu|db)\b", re.I)
_QUERY_STATUS = ".StatusBar-padding.StatusBar-divider"
_QUERY_MESSAGES = ".dock-tabpane-active[role=tabpanel] .dock-pane-cache > div[tabindex='0']"


secret_ref = pgadmin_secret_ref


def is_database_step(step):
    return "database" in step or any(_DATABASE_STEP.search(str(step.get(key) or "")) for key in ("action", "expected", "evidence"))


def validate_expectation(rows):
    """Expect an exact row count and named cells for each row. SQL aliases identify the cells."""
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not row or any(
            not isinstance(key, str) or not key.strip() or "\n" in key or (value is not None and not isinstance(value, str))
            for key, value in row.items()) for row in rows
    ):
        raise GateError('Database expectation must be an array of rows with named text or null values, such as [{"phone":"0901234567"}], or [] for no rows.')
    return rows


def verify_result(result, expected):
    """Compare the observed grid by column name. A missing column or incomplete grid cannot establish FAIL or PASS."""
    rows, count = result["rows"], result["total"]
    result_check = {"status": "UNVERIFIED", "expected_rows": expected, "actual_count": count, "actual_rows": []}
    if result["error"] or count is None:
        return {**result_check, "detail": result["error"] or "PgAdmin did not report the result row count."}
    if not count and not expected:
        return {**result_check, "status": "PASS", "detail": "Expected and got zero rows."}
    header = rows[0] if rows else []
    columns = [cell.partition("\n")[0].strip() for cell in header[1:]]
    fields = dict.fromkeys(key for row in expected for key in row)
    if not expected:
        fields = dict.fromkeys(column for column in columns if column and columns.count(column) == 1)
    unavailable = [field for field in fields if columns.count(field) != 1]
    if unavailable:
        return {**result_check, "detail": "Missing or ambiguous result column(s): " + ", ".join(unavailable) + ". Use unique SQL aliases."}
    indexes = {field: columns.index(field) + 1 for field in fields}
    result_check["actual_rows"] = [
        {field: row[index] for field, index in indexes.items() if index < len(row)}
        for row in rows[1:] if indexes
    ]
    if count != len(expected):
        return {**result_check, "status": "FAIL", "detail": f"Expected {len(expected)} row(s), got {count}."}
    actual = result_check["actual_rows"]
    if len(actual) != count or any(len(row) != len(header) for row in rows[1:]):
        return {**result_check, "detail": "PgAdmin did not show all requested cells. Narrow the query so the result fits the grid."}
    mismatches = [f"row {n}, {field}: expected {json.dumps(value, ensure_ascii=False)}, got {json.dumps(observed[field], ensure_ascii=False)}"
                  for n, (wanted, observed) in enumerate(zip(expected, actual), 1) for field, value in wanted.items() if value != observed[field]]
    return {**result_check, "status": "FAIL" if mismatches else "PASS", "detail": "; ".join(mismatches) or "All expected rows and fields match."}


def check_summary(check):
    expected, actual = (json.dumps(check[key], ensure_ascii=False) for key in ("expected_rows", "actual_rows"))
    return f"PgAdmin {check['status']} — {check['detail']}\nExpected ({len(check['expected_rows'])} rows): {expected}\nActual ({check['actual_count']} rows): {actual}"


def check_sql(sql):
    """Only a reading query may run: the evidence must never change the data it is checking. Returns the SQL without a trailing semicolon."""
    plain = _TOKENS.sub(lambda m: "''" if m.group(0)[0] in "'\"" else " ", sql or "")
    words = re.findall(r"[a-z_]+", plain.lower())
    statements = [s for s in plain.split(";") if s.strip()]
    if not words:
        raise GateError("The SQL is empty.")
    if len(statements) > 1:
        raise GateError("Give one SQL statement per picture.")
    if words[0] not in READ_ONLY:
        raise GateError(f"Only a reading query is allowed (SELECT, WITH, SHOW, EXPLAIN); this one starts with {words[0].upper()}.")
    bad = sorted(set(words) & FORBIDDEN)
    if bad:
        raise GateError(f"The SQL contains {', '.join(w.upper() for w in bad)}: the evidence query must only read data. Quote a column of that name or rephrase.")
    return sql.strip().rstrip(";").strip()


def format_sql(sql):
    """One clause per line, so a long statement fits the editor without scrolling sideways and the whole query is in the picture. Quoted text is never touched."""
    if "\n" in sql:
        return sql
    parts = _TOKENS.split(sql)
    if any(part.startswith("--") for part in parts[1::2]):
        return sql                     # a line comment runs to the end of the line: a new line inside it would turn the rest into SQL
    clause = re.compile(r"\s+(FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|UNION|(?:LEFT\s+|RIGHT\s+|INNER\s+|FULL\s+)?JOIN)\b", re.I)
    return "".join(part if index % 2 else clause.sub(lambda m: "\n" + m.group(1), part) for index, part in enumerate(parts))


MEASURE_COLUMNS = """() => [...document.querySelectorAll('[role=columnheader]')].map((h, i) => { const r = h.getBoundingClientRect();
    const over = Math.max(0, ...[...document.querySelectorAll('[role=gridcell][aria-colindex="' + (i + 1) + '"]')].map(c => { const g = document.createRange(); g.selectNodeContents(c); return g.getBoundingClientRect().width + 20 - c.clientWidth; }));
    return {i, right: r.right, top: r.top, h: r.height, over}; })"""


def fit_columns(page, frame):
    """PgAdmin cuts a long value with an ellipsis. Drag each too-narrow column wider (the way a person does) until every value is written out in the picture."""
    offset = frame.frame_element().bounding_box() or {"x": 0, "y": 0}
    for _ in range(10):
        todo = [c for c in frame.evaluate(MEASURE_COLUMNS) if c["over"] > 2 and c["i"] > 0]          # column 0 is the row number
        if not todo:
            return
        c = todo[0]
        x, y = offset["x"] + c["right"] - 3, offset["y"] + c["top"] + c["h"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + c["over"] + 24, y, steps=8)
        page.mouse.up()
        page.wait_for_timeout(250)


def settings(ws, store: SecretStore):
    """Everything the sign-in needs, or a sentence saying which part is missing."""
    missing = [label for key, label in (("pgadmin_url", "PgAdmin address"), ("pgadmin_login", "PgAdmin login")) if not ws.get(key)]
    database = ws.get("pgadmin_db") or ws.get("db")
    if not database:
        missing.append("database name")
    if missing:
        raise GateError(f"PgAdmin is not set up for workspace {ws['name']}: missing {', '.join(missing)}. Run oqa init and fill the PgAdmin section.")
    password = store.get(secret_ref(ws["name"]))
    if password is None:
        raise GateError(f"No PgAdmin password is saved for workspace {ws['name']}: run oqa init and enter it.")
    return {"url": ws["pgadmin_url"], "login": ws["pgadmin_login"], "password": password, "server": ws.get("pgadmin_server", ""),
            "database": database, "db_password": store.get(secret_ref(ws["name"], "db"))}


def _exact(text):
    return re.compile(r"^\s*" + re.escape(text) + r"\s*$")


class PgAdmin:
    """One signed-in PgAdmin page with the target database selected. Open it once per run, then call query() for each SQL."""

    def __init__(self, page, cfg):
        self.page, self.cfg = page, cfg

    def entry(self, name):
        return self.page.locator(".file-entry", has=self.page.locator(".file-name", has_text=_exact(name))).first

    def _answer_password(self):
        dialog = self.page.locator(".MuiDialog-root:visible")
        if dialog.count() and dialog.locator("input[type=password]").count():
            asked = dialog.first.inner_text()
            wanted = self.cfg["server"]
            if wanted and f'"{wanted}"' not in asked:
                raise GateError(f"PgAdmin asks for the password of another server ({' '.join(asked.split())[:140]}), not of {wanted}: check the server name in oqa init.")
            if self.cfg["db_password"] is None:
                raise GateError("PgAdmin asks for the database password and none is saved: run oqa init and enter it in the PgAdmin section.")
            dialog.locator("input[type=password]").fill(self.cfg["db_password"])
            dialog.locator("button:has-text('OK')").last.click()
            self.page.wait_for_timeout(2500)
            again = self.page.locator(".MuiDialog-root:visible")
            if again.count() and again.locator("input[type=password]").count():
                reason = " ".join(again.first.inner_text().split())
                reason = re.sub(r"\s*Cancel\s*OK\s*$", "", reason.split("Save Password")[-1].strip() or reason)
                raise GateError(f"PgAdmin could not connect to the database server {wanted or ''}: {reason[:220]}. Check the database password, and that the server in PgAdmin points to the right host, port and user.")

    def _server_names(self):
        return [t.strip() for t in self.page.locator(".file-entry[data-depth='2'] .file-name").all_inner_texts()]

    def _open(self, name):
        self._answer_password()
        entry = self.entry(name)
        entry.wait_for(timeout=TREE_MS)
        if "open" not in (entry.locator(".directory-toggle").get_attribute("class") or ""):
            entry.locator(".directory-toggle").click()
        self.page.wait_for_timeout(1200)
        self._answer_password()

    def login(self):
        page, cfg = self.page, self.cfg
        page.goto(urljoin(cfg["url"].rstrip("/") + "/", "login"), wait_until="domcontentloaded")
        page.wait_for_selector("input[name=email], .file-entry", timeout=SLOW_MS)
        if page.locator("input[name=email]").count():
            page.fill("input[name=email]", cfg["login"])
            page.fill("input[name=password]", cfg["password"])
            page.click("button[type=submit]")
            try:
                page.wait_for_selector(".file-entry", timeout=SLOW_MS)
            except Exception as error:
                raise GateError("PgAdmin did not sign in (wrong login or password, or two-factor): check them in oqa init") from error
        return self

    def select_database(self):
        cfg = self.cfg
        servers = self.entry("Servers")
        servers.wait_for(timeout=SLOW_MS)
        if "open" not in (servers.locator(".directory-toggle").get_attribute("class") or ""):
            servers.locator(".directory-toggle").click()
        self.page.wait_for_selector(".file-entry[data-depth='2']", timeout=TREE_MS)
        names = self._server_names()
        if cfg["server"] and cfg["server"] not in names:
            raise GateError(f"PgAdmin has no server named {cfg['server']}. Servers it shows: {', '.join(names) or 'none'}. Fix the server name in oqa init, or register the server in PgAdmin.")
        if cfg["server"] and names.count(cfg["server"]) > 1:
            raise GateError(f"PgAdmin has {names.count(cfg['server'])} servers named {cfg['server']}: delete the extra one in PgAdmin (or give them different names), so the tool cannot open the wrong one.")
        if cfg["server"]:
            self._open(cfg["server"])
        else:
            self._answer_password()
            first = self.page.locator(".file-entry.depth-2, .file-entry[data-depth='2']").first
            first.wait_for(timeout=SLOW_MS)
            if "open" not in (first.locator(".directory-toggle").get_attribute("class") or ""):
                first.locator(".directory-toggle").click()
            self.page.wait_for_timeout(1200)
            self._answer_password()
        self._open("Databases")
        database = self.entry(cfg["database"])
        try:
            database.wait_for(timeout=TREE_MS)
        except Exception as error:
            raise GateError(f"PgAdmin shows no database named {cfg['database']}" + (f" on server {cfg['server']}" if cfg["server"] else "") + ": check the names in oqa init") from error
        database.click()
        self.close_query_tabs(settle=3000)
        return self

    def close_query_tabs(self, settle=0):
        """PgAdmin brings back the Query Tool tabs of the last session. Close them so each picture shows one tab only; a tab with unsaved text asks first: discard it."""
        tabs = self.page.locator(".dock-tab:has([title^='Query Tool']) button")
        self.page.wait_for_timeout(settle)                  # the old tabs come back a moment after sign-in
        for _ in range(30):
            if not tabs.count():
                return
            tabs.first.click()
            self.page.wait_for_timeout(500)
            for frame in self.page.frames:                  # the "Save query changes?" question is drawn inside the tab's own frame
                try:
                    discard = frame.locator("button:has-text(\"Don't save\")")
                    if discard.count():
                        discard.first.click()
                        self.page.wait_for_timeout(400)
                        break
                except Exception:
                    pass

    def _tool_frame(self):
        """The Query Tool of the newest tab. PgAdmin draws each tab in its own iframe, and the older tabs stay in the page hidden."""
        for _ in range(SLOW_MS // 500):
            for handle in reversed(self.page.locator("iframe:visible").element_handles()):
                frame = handle.content_frame()
                if frame and frame.locator(".cm-content").count():
                    return frame
            self.page.wait_for_timeout(500)
        raise GateError("PgAdmin did not open the Query Tool: open it by hand once to see what it shows.")

    def query(self, sql, picture):
        """Run `sql` in a fresh Query Tool tab (so nothing from an earlier query can be mistaken for this result) and save the screen to `picture`.
        Returns {rows, total, error, box}: the rows read from the grid, PgAdmin's own row count, its error text, and the result panel as [x, y, w, h]."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page = self.page
        self.entry(self.cfg["database"]).click()
        page.wait_for_timeout(800)
        before = page.locator("iframe").count()
        page.get_by_text("Tools", exact=True).first.click()                    # the menu is steadier than the Alt+Shift+Q shortcut
        page.locator("[role=menuitem]:has-text('Query Tool')").first.click()
        for _ in range(40):                                                     # wait for the new tab, not for an old one
            if page.locator("iframe").count() > before:
                break
            page.wait_for_timeout(250)
        else:
            raise GateError("PgAdmin did not open a new Query Tool tab.")
        frame = self._tool_frame()
        frame.wait_for_function("() => { const e = document.querySelector('.cm-content'); return !!e && e.getAttribute('aria-readonly') !== 'true'; }", timeout=SLOW_MS)   # read-only until the tab has connected
        frame.locator(".cm-content").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(format_sql(sql))
        page.keyboard.press("F5")
        error = ""
        try:
            frame.wait_for_function(
                "selector => [...document.querySelectorAll(selector)].some(e => /^(Query complete|Query cancel)/i.test(e.innerText.trim()))",
                arg=_QUERY_STATUS, timeout=SLOW_MS)
        except PlaywrightTimeout:
            error = "PgAdmin did not finish the query before the timeout."
        statuses = frame.locator(_QUERY_STATUS).all_inner_texts()
        if not error and any(text.strip().lower().startswith("query cancel") for text in statuses):
            error = "PgAdmin cancelled the query."
        total = next((match for text in statuses if (match := re.fullmatch(r"Total rows:\s*([\d,]+)", text.strip()))), None)
        if not error and total is None:
            messages = frame.get_by_text("Messages", exact=True)
            if messages.count():
                messages.first.click()
            error = next((text.strip() for text in frame.locator(_QUERY_MESSAGES).all_inner_texts()
                          if text.lstrip().startswith("ERROR:")), "PgAdmin did not report a completed result grid.")
        rows = frame.evaluate("""() => [...document.querySelectorAll('[role=row]:has([role=columnheader], [role=gridcell])')].slice(0, 60)
            .map(r => [...r.querySelectorAll('[role=columnheader], [role=gridcell]')]
                .map(c => c.getAttribute('role') === 'columnheader' ? c.innerText.trim()
                    : c.querySelector('.Formatters-disabledCell')?.textContent === '[null]' ? null : c.textContent))""")
        box = frame.evaluate("""() => { const t = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && /^Data Output$/.test(e.innerText || ''));
            const p = t && (t.closest('.dock-panel') || t.parentElement.parentElement);
            if (!p) return null; const b = p.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; }""")
        if not error:
            try:
                fit_columns(page, frame)
            except Exception:                                  # a picture with a cut value is still a picture: never lose the result for a cosmetic step
                pass
        offset = page.locator("iframe:visible").last.bounding_box() or {"x": 0, "y": 0}
        if box:
            box = [box[0] + round(offset["x"]), box[1] + round(offset["y"]), box[2], box[3]]
        page.screenshot(path=str(picture))
        self.close_query_tabs()
        return {"rows": rows, "total": int(total.group(1).replace(",", "")) if total else None, "error": error, "box": box}


@contextmanager
def session(ws, store: SecretStore, headless=True):
    """Sign in and select the database; yields a PgAdmin ready for query(). Closes the browser on exit."""
    cfg = settings(ws, store)
    with browser.launch(headless) as new_page:
        _context, page = new_page()
        yield PgAdmin(page, cfg).login().select_database()


def shot(run, ws, store: SecretStore, case, n, sql, caption=None, headless=True, *, expected_rows=None):
    """Run `sql` on PgAdmin and keep the picture as evidence of step `n` of `case`.
    Take it BEFORE recording the step (n is the next step): `oqa live step` then writes what the rows showed and the picture is already part of the step.
    A step that is already recorded gets it as supplemental evidence (the same place `oqa live shot` puts one). The picture is the whole PgAdmin screen with the result panel framed."""
    import uuid

    from . import evidence, events, live
    run.require("EXECUTE")
    attempt = live._attempt(run, case)
    steps = live._strategy_case(run, case)["steps"]
    if not isinstance(n, int) or not 1 <= n <= len(steps):
        raise GateError("Choose a planned step number for the database evidence.")
    done = live._recorded(run, case, attempt)
    if n not in done and n != len(done) + 1:
        raise GateError(f"Record the steps in order: the next step of {case} is {len(done) + 1}, not {n}.")
    spec = steps[n - 1]
    if n not in done:
        planned = spec.get("database")
        if planned is not None:
            if expected_rows is not None and expected_rows != planned:
                raise GateError("--expect must match the approved database expectation for this step.")
            expected_rows = planned
        elif expected_rows is None and is_database_step(spec):
            raise GateError('This approved step needs an exact database check. Give --expect with the named columns and expected rows.')
    if expected_rows is not None:
        expected_rows = validate_expectation(expected_rows)
    sql = check_sql(sql)
    folder = run.dir / "evidence" / re.sub(r"[^\w.-]+", "_", case) / f"a{attempt}"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:8]
    raw, annotated = folder / f"s{n}_supp_{suffix}_raw.png", folder / f"s{n}_supp_{suffix}_annotated.png"
    with globals()["session"](ws, store, headless) as pg:
        result = pg.query(sql, raw)
    check = verify_result(result, expected_rows) if expected_rows is not None else None
    if check is not None:
        result = {**result, "check": check}
    verdict = f" {check['status']}" if check else ""
    label = f"PgAdmin{verdict}: " + (caption or result["error"] or f"{result['total']} row(s) - " + " ".join(sql.split())[:90])
    evidence.annotate(raw, [tuple(result["box"] or (0, 0, browser.VIEWPORT["width"], browser.VIEWPORT["height"]))], label, annotated,
                      ok=check["status"] == "PASS" if check else not result["error"])
    error = evidence.check(raw, annotated)
    if error:
        raise GateError(error)
    image = {"raw": str(raw.relative_to(run.dir)), "annotated": str(annotated.relative_to(run.dir))}
    if n in done:
        events.append(run.dir, "step_evidence", case=case, attempt=attempt, n=n, source="pgadmin", sql=sql, images=[image])
    else:
        events.append(run.dir, "evidence_captured", case=case, attempt=attempt, n=n, source="pgadmin", sql=sql,
                      pgadmin_result={key: result[key] for key in ("rows", "total", "error", "check") if key in result},
                      images=[{**image, "caption": label, "target": "PgAdmin", "box": list(result["box"] or [])}])
    return result, annotated


def summary(result, where):
    """What the assistant reads after a query: the row count (or PgAdmin's error) and the first rows, so it can judge PASS or FAIL from them."""
    if result.get("check"):
        return f"{check_summary(result['check'])}\nPicture {where}."
    if result["error"]:
        return f"PgAdmin ERROR: {result['error']}\nThe picture of the error is {where}."
    lines = [f"PgAdmin returned {result['total']} row(s). Picture {where}."]
    rows = result["rows"]
    if rows:
        lines += [" | ".join("NULL" if cell is None else cell.split(chr(10))[0] for cell in row[1:]) for row in rows[:21]]
    return "\n".join(lines)
