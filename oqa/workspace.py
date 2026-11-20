"""workspace.json: the project's configuration (URL, profiles, reference folders). Never holds a password: a profile's secret lives in the
credential store under `secret_ref(workspace, profile)`."""
import re
from urllib.parse import urlparse

from . import SCHEMA_VERSION
from .paths import check_name, workspace_dir
from .secrets import SecretStore
from .store import SchemaError, read_json, write_json

LANGUAGES = ("vi", "ja", "en")
PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]{2,30}$")
MAIN_ENV = "main"       # the workspace's own url/db; `environments` lists the others (staging, production...) that run the same scripts


def secret_ref(ws, profile, env=None):
    """Where a profile's password is kept. The `main` environment keeps the original key, so passwords saved before environments existed still work."""
    return f"oqa:{ws}:{profile}" if not env or env == MAIN_ENV else f"oqa:{ws}:{profile}:{env}"


def blank(name=""):
    return {"schema_version": SCHEMA_VERSION, "name": name, "url": "", "db": "", "profiles": [], "environments": [], "reference_dirs": [], "language": "vi", "test_prefix": "ZZ_AUTOTEST_"}


def normalize(raw):
    """What the form sends -> a clean workspace dict (no secrets). Unknown keys are dropped."""
    raw = raw if isinstance(raw, dict) else {}

    def text(key, default=""):
        return str(raw.get(key, default) or "").strip()

    url = text("url")
    if url and not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "http://" + url
    profiles = []
    for p in raw.get("profiles") or []:
        if isinstance(p, dict) and (str(p.get("name", "")).strip() or str(p.get("login", "")).strip()):
            profiles.append({"name": str(p.get("name", "")).strip(), "login": str(p.get("login", "")).strip(), "note": str(p.get("note", "")).strip()})
    environments = []
    for e in raw.get("environments") or []:
        if isinstance(e, dict) and (str(e.get("name", "")).strip() or str(e.get("url", "")).strip()):
            env_url = str(e.get("url", "")).strip()
            if env_url and not re.match(r"^[a-z][a-z0-9+.-]*://", env_url, re.I):
                env_url = "http://" + env_url
            accounts = [{"profile": str(a.get("profile", "")).strip(), "login": str(a.get("login", "")).strip()}
                        for a in e.get("accounts") or [] if isinstance(a, dict) and (str(a.get("profile", "")).strip() or str(a.get("login", "")).strip())]
            environments.append({"name": str(e.get("name", "")).strip(), "url": env_url.rstrip("/"), "db": str(e.get("db", "")).strip(), "accounts": accounts})
    return {"schema_version": SCHEMA_VERSION, "name": text("name"), "url": url.rstrip("/"), "db": text("db"), "profiles": profiles, "environments": environments,
            "reference_dirs": [str(d).strip() for d in raw.get("reference_dirs") or [] if str(d).strip()],
            "language": text("language", "vi") or "vi", "test_prefix": text("test_prefix", "ZZ_AUTOTEST_") or "ZZ_AUTOTEST_"}


def validate(ws):
    problems = []
    try:
        check_name(ws.get("name", ""), "workspace name")
    except Exception as error:
        problems.append(str(error))
    parsed = urlparse(ws.get("url", ""))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        problems.append("URL: enter the address you open in the browser, for example http://localhost:8069")
    if not ws.get("profiles"):
        problems.append("Profiles: add at least one (for example Admin).")
    seen = set()
    for p in ws.get("profiles", []):
        if not p.get("name") or not p.get("login"):
            problems.append("Each profile needs a name and a login.")
        elif p["name"].lower() in seen:
            problems.append(f"Profile '{p['name']}' appears twice.")
        else:
            try:
                check_name(p["name"], "profile name")
            except Exception as error:
                problems.append(str(error))
        seen.add(str(p.get("name", "")).lower())
    seen_env = {MAIN_ENV}
    for e in ws.get("environments", []):
        label = e.get("name") or "?"
        try:
            check_name(e.get("name", ""), "environment name")
        except Exception as error:
            problems.append(str(error))
        if e.get("name", "").lower() in seen_env:
            problems.append(f"Environment '{label}' appears twice (the name '{MAIN_ENV}' is the workspace's own address).")
        seen_env.add(e.get("name", "").lower())
        env_url = urlparse(e.get("url", ""))
        if env_url.scheme not in ("http", "https") or not env_url.netloc:
            problems.append(f"Environment '{label}': enter the address you open in the browser.")
        known, used = {p.get("name") for p in ws.get("profiles", [])}, set()
        for a in e.get("accounts", []):
            if a.get("profile") not in known:
                problems.append(f"Environment '{label}': profile '{a.get('profile')}' is not one of the profiles above.")
            elif not a.get("login"):
                problems.append(f"Environment '{label}': profile '{a['profile']}' needs a login.")
            elif a["profile"] in used:
                problems.append(f"Environment '{label}': profile '{a['profile']}' appears twice.")
            used.add(a.get("profile"))
    if ws.get("language") not in LANGUAGES:
        problems.append(f"Language must be one of {', '.join(LANGUAGES)}.")
    if not PREFIX_RE.match(ws.get("test_prefix", "")):
        problems.append("Test-data prefix: 2 to 30 letters, digits, - or _.")
    if problems:
        raise SchemaError(problems, "workspace")
    return ws


def environments(ws):
    """Every place the scripts can run: the workspace's own address first (named `main`), then the others."""
    return [{"name": MAIN_ENV, "url": ws["url"], "db": ws.get("db", ""), "accounts": []}, *ws.get("environments", [])]


def for_env(ws, env=None):
    """The workspace as seen from one environment: the same dict with that environment's url and db, so code that reads ws["url"] needs no change.
    A profile is a role shared by all environments; its login (and password, see secret_ref) may differ per environment. A profile the environment gives no
    account for keeps the main login and so fails to sign in, naming the missing password, rather than silently using another system's account."""
    if not env or env == MAIN_ENV:
        return dict(ws, env=MAIN_ENV)
    for e in ws.get("environments", []):
        if e["name"].lower() == env.lower():
            logins = {a["profile"]: a["login"] for a in e.get("accounts", [])}
            return dict(ws, url=e["url"], db=e["db"], env=e["name"], profiles=[dict(p, login=logins.get(p["name"], p["login"])) for p in ws["profiles"]])
    raise SchemaError(f"environment '{env}' does not exist: choose one of {', '.join(e['name'] for e in environments(ws))}", "workspace")


def load(name):
    ws = read_json(workspace_dir(name) / "workspace.json")
    if ws is None:
        raise SchemaError(f"workspace '{name}' does not exist", "workspace")
    return validate(ws)


def public_view(ws, store: SecretStore):
    """Safe to show anywhere (the form, `oqa ws show`, the assistant): each profile says only whether a password is saved."""
    view = dict(ws)
    view["profiles"] = [dict(p, has_secret=store.has(secret_ref(ws["name"], p["name"]))) for p in ws.get("profiles", [])]
    view["environments"] = [dict(e, accounts=[dict(a, has_secret=store.has(secret_ref(ws["name"], a["profile"], e["name"]))) for a in e.get("accounts", [])])
                            for e in ws.get("environments", [])]
    return view


def save(raw, store: SecretStore, passwords=None):
    """Validate, store the passwords given ({profile name: password}; missing or empty = keep the saved one), then write workspace.json.
    A profile that was removed loses its stored password."""
    ws = validate(normalize(raw))
    passwords = passwords or {}
    previous = read_json(workspace_dir(ws["name"]) / "workspace.json") or {}
    for profile in ws["profiles"]:
        ref = secret_ref(ws["name"], profile["name"])
        if not passwords.get(profile["name"]) and not store.has(ref):
            raise SchemaError(f"Profile '{profile['name']}' needs a password.", "workspace")
    for env in ws["environments"]:
        for account in env["accounts"]:
            if not passwords.get(f"{account['profile']}@{env['name']}") and not store.has(secret_ref(ws["name"], account["profile"], env["name"])):
                raise SchemaError(f"Environment '{env['name']}': profile '{account['profile']}' needs a password.", "workspace")
    for profile in ws["profiles"]:
        if passwords.get(profile["name"]):
            store.set(secret_ref(ws["name"], profile["name"]), passwords[profile["name"]])
    for env in ws["environments"]:
        for account in env["accounts"]:
            given = passwords.get(f"{account['profile']}@{env['name']}")
            if given:
                store.set(secret_ref(ws["name"], account["profile"], env["name"]), given)
    keep = {(a["profile"], e["name"]) for e in ws["environments"] for a in e["accounts"]}
    for old in previous.get("profiles", []):
        if old.get("name") not in {p["name"] for p in ws["profiles"]}:
            store.delete(secret_ref(ws["name"], old["name"]))
    for old_env in previous.get("environments", []):          # a removed environment or account loses its stored password
        for account in old_env.get("accounts", []):
            if (account.get("profile"), old_env.get("name")) not in keep:
                store.delete(secret_ref(ws["name"], account["profile"], old_env["name"]))
    write_json(workspace_dir(ws["name"]) / "workspace.json", ws)
    return ws
