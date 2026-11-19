"""`oqa doctor`: is this computer ready to run the skill, and `--fix` installs what is missing (pip packages and the Playwright browser)."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from . import VERSION, globalhooks
from .secrets import SecretStoreUnavailable, default_store

MIN_PYTHON = (3, 9)
PACKAGES = {"openpyxl": "openpyxl", "playwright": "playwright", "PIL": "pillow"}


def _browser_installed():
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        candidates = [Path(env)]
    elif sys.platform == "win32":
        candidates = [Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"]
    elif sys.platform == "darwin":
        candidates = [Path.home() / "Library" / "Caches" / "ms-playwright"]
    else:
        candidates = [Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"]
    return any(b.is_dir() and any(p.name.startswith("chromium") for p in b.iterdir()) for b in candidates)


def check():
    missing = [pip for module, pip in PACKAGES.items() if importlib.util.find_spec(module) is None]
    try:
        default_store()
        secrets_ok, secrets_note = True, ""
    except SecretStoreUnavailable as error:
        secrets_ok, secrets_note = False, str(error)
    python_ok = sys.version_info[:2] >= MIN_PYTHON
    browser = "playwright" not in missing and _browser_installed()
    return {"oqa": VERSION, "python": sys.version.split()[0], "python_ok": python_ok, "missing_packages": missing, "browser_installed": browser,
            "missing_hooks": globalhooks.missing(), "secret_store": secrets_ok, "secret_store_note": secrets_note, "ready": python_ok and not missing and browser and secrets_ok}


VENV = Path.home() / ".odoo-test" / "venv"  # fixed place for the packages; oqa.py runs everything with this python


def venv_python():
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def fix(report, log):
    """Create the venv at ~/.odoo-test/venv when it is missing, install the missing packages and the browser into it. `log(line)` receives progress; returns the new report."""
    steps = []
    if not venv_python().exists():
        steps.append([sys.executable, "-m", "venv", str(VENV)])
    python = str(venv_python())
    if report["missing_packages"] or not venv_python().exists():
        steps.append([python, "-m", "pip", "install", *PACKAGES.values()])
    if "playwright" in report["missing_packages"] or not report["browser_installed"] or not venv_python().exists():
        steps.append([python, "-m", "playwright", "install", "chromium"])
    for step in steps:
        log("$ " + " ".join(step))
        subprocess.run(step, check=True)
    if steps and Path(sys.prefix).resolve() != VENV.resolve():
        log("Packages are in ~/.odoo-test/venv: run `oqa doctor` again to check them.")
    if report.get("missing_hooks"):                      # the two hooks that work before the skill is loaded: written once, with a backup
        try:
            path, added, backup = globalhooks.install()
            log(f"Added {', '.join(added)} to {path}" + (f" (backup: {backup})" if backup else "") + ": open a new Claude Code window for them to start.")
        except (OSError, ValueError) as error:
            log(f"Hooks not installed ({error}). Run `oqa hooks --install` after fixing that file.")
    return check()
