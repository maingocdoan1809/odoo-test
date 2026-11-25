"""PgAdmin evidence: sign in to the team's PgAdmin web, open a Query Tool on the right server and database, run one read-only SQL and take the picture.
The AI only passes the SQL: the address, server, database and passwords come from the workspace (oqa init), the steps below are the only place that knows the PgAdmin screen."""
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
FORBIDDEN = {"insert", "update", "delete", "drop", "alter", "truncate", "create", "grant", "revoke", "copy", "call", "do", "vacuum", "reindex", "cluster", "comment", "merge", "lock", "refresh", "reassign", "security", "set", "listen", "notify"}


secret_ref = pgadmin_secret_ref


def check_sql(sql):
    """Only a reading query may run: the evidence must never change the data it is checking. Returns the SQL without a trailing semicolon."""
    plain = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql or "", flags=re.S))
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
        dialog = self.page.locator(".MuiDialog-root")
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
            again = self.page.locator(".MuiDialog-root")
            if again.count() and again.locator("input[type=password]").count():
                reason = " ".join(again.first.inner_text().split())
                reason = re.sub(r"\s*Cancel\s*OK\s*$", "", reason.split("Save Password")[-1].strip() or reason)
                raise GateError(f"PgAdmin could not connect to the database server {wanted or ''}: {reason[:220]}. Check the database password, and that the server in PgAdmin points to the right host, port and user.")

    def _server_names(self):
        return [t.strip() for t in self.page.locator(".file-entry[data-depth='2'] .file-name").all_inner_texts()]

    def _open(self, name):
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
        frame = self._tool_frame()
        frame.wait_for_function("() => { const e = document.querySelector('.cm-content'); return !!e && e.getAttribute('aria-readonly') !== 'true'; }", timeout=SLOW_MS)   # read-only until the tab has connected
        frame.locator(".cm-content").click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(sql)
        page.keyboard.press("F5")
        try:
            frame.wait_for_function("() => /Total rows: \\d|Query returned successfully|Query complete|ERROR:/.test(document.body.innerText)", timeout=SLOW_MS)
        except Exception:
            pass
        page.wait_for_timeout(800)
        text = frame.evaluate("() => document.body.innerText")
        failed = re.search(r"ERROR:[^\n]*", text)
        if failed:
            messages = frame.get_by_text("Messages", exact=True)
            if messages.count():
                messages.first.click()
                page.wait_for_timeout(500)
        rows = frame.evaluate("""() => [...document.querySelectorAll('[role=row]')].slice(0, 60)
            .map(r => [...r.querySelectorAll('[role=columnheader], [role=gridcell]')].map(c => c.innerText.trim()))""")
        total = re.search(r"Total rows:\s*([\d,]+)", text)
        box = frame.evaluate("""() => { const t = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && /^Data Output$/.test(e.innerText || ''));
            const p = t && (t.closest('.dock-panel') || t.parentElement.parentElement);
            if (!p) return null; const b = p.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; }""")
        offset = page.locator("iframe:visible").last.bounding_box() or {"x": 0, "y": 0}
        if box:
            box = [box[0] + round(offset["x"]), box[1] + round(offset["y"]), box[2], box[3]]
        page.screenshot(path=str(picture))
        self.close_query_tabs()
        return {"rows": rows, "total": int(total.group(1).replace(",", "")) if total else None, "error": failed.group(0) if failed else "", "box": box}


@contextmanager
def session(ws, store: SecretStore, headless=True):
    """Sign in and select the database; yields a PgAdmin ready for query(). Closes the browser on exit."""
    cfg = settings(ws, store)
    with browser.launch(headless) as new_page:
        _context, page = new_page()
        yield PgAdmin(page, cfg).login().select_database()


def shot(session, ws, store: SecretStore, case, n, sql, caption=None, headless=True):
    """Run `sql` on PgAdmin and attach the picture to the already recorded step `n` of `case` as supplemental evidence (the same place `oqa live shot` puts one).
    The picture is the whole PgAdmin screen with the result panel framed and the caption under it."""
    import shutil
    import uuid

    from . import evidence, events, live
    session.require("EXECUTE")
    attempt = live._attempt(session, case)
    if n not in live._recorded(session, case, attempt):
        raise GateError(f"Step {n} of {case} is not recorded yet: record it with oqa live step {case} --n {n} first, then add the database picture.")
    sql = check_sql(sql)
    folder = session.dir / "evidence" / re.sub(r"[^\w.-]+", "_", case) / f"a{attempt}"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:8]
    raw, annotated = folder / f"s{n}_supp_{suffix}_raw.png", folder / f"s{n}_supp_{suffix}_annotated.png"
    with globals()["session"](ws, store, headless) as pg:
        result = pg.query(sql, raw)
    label = caption or ("PgAdmin: " + (result["error"] or f"{result['total']} row(s)") + " - " + " ".join(sql.split())[:90])
    evidence.annotate(raw, [tuple(result["box"] or (0, 0, 1600, 900))], label, annotated, ok=not result["error"])
    error = evidence.check(raw, annotated)
    if error:
        raise GateError(error)
    events.append(session.dir, "step_evidence", case=case, attempt=attempt, n=n, source="pgadmin", sql=sql,
                  images=[{"raw": str(raw.relative_to(session.dir)), "annotated": str(annotated.relative_to(session.dir))}])
    return result, annotated
