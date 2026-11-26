"""CONNECT: sign in once per profile, keep the signed-in state for the run, and record what this Odoo does after login (the landing page, the version).
Odoo is not like other apps here: the page after login differs by version (/web, /odoo), a database selector can come first, and two-factor
stops a script. Each of those is reported as a sentence that says what to do, not as a timeout."""
import json
from pathlib import Path
from urllib.parse import urljoin

from . import browser, knowledge
from .secrets import SecretStore
from .state import GateError, Session
from .workspace import secret_ref

SETTLED = "() => document.querySelector('.o_web_client') || document.querySelector('.alert-danger') || document.querySelector('input[name=totp_token]')"
VERSION_JS = ("() => fetch('/web/webclient/version_info', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
              "body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {}})}).then(r => r.json()).then(j => (j.result || {}).server_version || '').catch(() => '')")


SLOW_MS = 90000          # the first load after an Odoo restart builds its asset bundles, which can take a minute


class LoginFailed(Exception):
    pass


LOGIN_FORM = "input[name=login], input#login"
ARRIVED = ".o_web_client, input[name=login], input#login, input[name=totp_token], .o_database_list, a[href*='/web/database/selector']"


def reach(page, ws, profile, store: SecretStore, target):
    """Open `target` signed in as `profile`, whatever this system does first. The address the server sends us to is NOT known when goto returns (the redirect to the
    sign-in page may still be under way), so wait until the page is one of: the web client (signed in), a sign-in form, a two-factor box or a database selector;
    only then decide. A stored session that has expired lands on the form and signs in again; one that is valid never meets the form."""
    page.goto(target, wait_until="domcontentloaded")
    page.wait_for_selector(ARRIVED, state="attached", timeout=SLOW_MS)
    if page.locator(LOGIN_FORM).count() or "/web/login" in page.url or "/web/database/selector" in page.url:
        return login(page, ws, profile, store)
    return {"landing_url": page.url, "title": page.title(), "version": page.evaluate(VERSION_JS) or ""}


def login(page, ws, profile, store: SecretStore):
    """Sign `profile` in on `page`. Returns {landing_url, title, version}; raises LoginFailed with the reason."""
    password = store.get(secret_ref(ws["name"], profile["name"], ws.get("env")))
    if password is None:
        raise LoginFailed(f"no password is saved for profile {profile['name']}: run oqa init and enter it")
    page.goto(urljoin(ws["url"], "/web/login") + (f"?db={ws['db']}" if ws.get("db") else ""), wait_until="domcontentloaded")
    page.wait_for_selector(ARRIVED, state="attached", timeout=SLOW_MS)
    if page.locator(".o_web_client").count():                # a valid session is already stored: the server sent us straight to the web client
        return {"landing_url": page.url, "title": page.title(), "version": page.evaluate(VERSION_JS) or ""}
    if "/web/database/selector" in page.url:                 # not `.o_database_list`: the real login page has a "Manage Databases" link with that class
        raise LoginFailed("Odoo asks which database to use: enter the database name in oqa init")
    if not page.locator(LOGIN_FORM).count():
        raise LoginFailed(f"the page {page.url} has no sign-in form this tool knows (single sign-on or a custom page?): sign in by hand once with `oqa connect --headed`")
    page.fill(LOGIN_FORM.split(",")[0].strip(), profile["login"])
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_function(SETTLED, timeout=SLOW_MS)
    if page.locator("input[name=totp_token]").count():
        raise LoginFailed(f"profile {profile['name']} asks for a two-factor code: only the user can enter it (use --headed), or turn it off for the test account")
    if page.locator(".alert-danger").count():
        raise LoginFailed(f"Odoo refused the sign-in: {page.locator('.alert-danger').first.inner_text().strip()[:160]}")
    page.wait_for_selector(".o_web_client", state="visible", timeout=SLOW_MS)
    page.wait_for_function("() => !location.pathname.includes('/web/login')", timeout=SLOW_MS)   # the redirect after sign-in may still be running: never record the login page as the landing page
    return {"landing_url": page.url, "title": page.title(), "version": page.evaluate(VERSION_JS) or ""}


def reuse_session(session: Session, context, page, ws, profile):
    """No password saved: the sign-in is kept in the sessions of this workspace (auth/<profile>.json). Take the newest one that still opens the web client."""
    candidates = sorted(session.dir.parent.glob(f"*/auth/{profile['name']}.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.parent.parent == session.dir:
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        context.clear_cookies()
        context.add_cookies(state.get("cookies", []))
        page.goto(urljoin(ws["url"], "/web"), wait_until="domcontentloaded")
        page.wait_for_selector(ARRIVED, state="attached", timeout=SLOW_MS)
        if page.locator(".o_web_client").count():
            page.wait_for_selector(".o_web_client", state="visible", timeout=SLOW_MS)
            return {"landing_url": page.url, "title": page.title(), "version": page.evaluate(VERSION_JS) or ""}
    raise LoginFailed(f"no password is saved for profile {profile['name']} and no earlier session of this workspace is still signed in: run oqa init and enter it")


def check_pgadmin(ws, store: SecretStore, headless=True):
    """When the workspace has PgAdmin, sign in and select the database now, so a wrong address, password, server or database is found before the run starts and not in
    the middle of a case. Returns "ok", or "not configured" when the workspace has no PgAdmin. Raises GateError saying what to fix."""
    if not ws.get("pgadmin_url"):
        return "not configured"
    from . import pgadmin

    try:
        with pgadmin.session(ws, store, headless):
            return "ok"
    except GateError as error:
        raise GateError(f"PgAdmin does not connect: {error}") from error
    except Exception as error:
        raise GateError(f"PgAdmin does not connect ({str(error).splitlines()[0][:160]}): check the address, server and database in oqa init, and that this computer can reach {ws['pgadmin_url']}.") from error


def connect(session: Session, ws, store: SecretStore, headless=True):
    """Sign every profile in, store each signed-in state under the session (auth/, never read by the assistant), then move CONNECT -> CONFIRM."""
    session.require("CONNECT")
    auth = session.dir / "auth"
    auth.mkdir(exist_ok=True)
    info, failures = {}, []
    with browser.launch(headless) as new_page:
        for profile in ws["profiles"]:
            context, page = new_page()
            try:
                info[profile["name"]] = login(page, ws, profile, store) if store.has(secret_ref(ws["name"], profile["name"], ws.get("env"))) else reuse_session(session, context, page, ws, profile)
                context.storage_state(path=str(auth / f"{profile['name']}.json"))
            except Exception as error:
                picture = session.dir / "logs" / f"connect_{profile['name']}.png"
                picture.parent.mkdir(exist_ok=True)
                try:
                    page.screenshot(path=str(picture))
                except Exception:
                    picture = None
                failures.append(f"{profile['name']}: {str(error).splitlines()[0][:200]}" + (f" (picture: {picture})" if picture else ""))
    if failures:
        raise GateError("Could not sign in:\n  " + "\n  ".join(failures) + "\nFix the cause (oqa init for a wrong login or database), then run oqa connect again.")
    for name, item in info.items():
        path = "/" + item["landing_url"].split("//", 1)[-1].partition("/")[2]
        knowledge.learn(ws["name"], "login", "login", f"After signing in as {name} the page is {path.split('?')[0]}" + (f" (Odoo {item['version']})" if item["version"] else ""))
    pgadmin_state = check_pgadmin(ws, store, headless)
    session.connected({"profiles": info, "version": next(iter(info.values()))["version"], "pgadmin": pgadmin_state, "url": ws["url"]})
    return info


def storage_path(session: Session, profile):
    path = session.dir / "auth" / f"{profile}.json"
    return str(path) if path.exists() else None
