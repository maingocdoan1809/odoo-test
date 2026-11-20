"""Export replay blocks and a standalone Playwright runner."""
import io
import json
import zipfile
from pathlib import Path

from . import live, workspace
from .state import GateError
from .store import read_json


def build(session):
    strategy = read_json(session.dir / "strategy.json", {}) or {}
    analysis = read_json(session.dir / "analysis.json", {}) or {}
    planned = {case["id"]: case for case in analysis.get("cases", [])}
    config = workspace.load(session.ws)
    manifest = {"baseUrl": config["url"], "prefix": config.get("test_prefix"), "cases": {}}
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for case in strategy.get("order", []):
            source = live.script_file(session, case)
            if not source.is_file() or case not in planned:
                continue
            text = source.read_text(encoding="utf-8")
            blocks, issues = live.parse_script(text, planned[case]["steps"])
            if issues:
                raise GateError(f"Cannot export {case}: " + "; ".join(issues))
            manifest["cases"][case] = {"profile": planned[case].get("profile"), "steps": blocks}
            archive.writestr(f"scripts/{source.name}", text)
        if not manifest["cases"]:
            raise GateError("This session has no saved scripts.")
        archive.writestr("cases.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.write(Path(__file__).with_name("web") / "replay.mjs", "replay.mjs")
        archive.writestr("package.json", json.dumps({"private": True, "dependencies": {"playwright": "^1.51.0"}}))
        archive.writestr("README.txt", """Install dependencies with npm install, then npx playwright install chromium.
Set OQA_BASE_URL for a different Odoo URL. Set OQA_DB if the server needs a database.
Supply OQA_LOGIN and OQA_PASSWORD for the testcase profile through the environment.
Alternatively set OQA_STORAGE_STATE to the path of an existing signed-in browser state.
For a case that changes roles, OQA_PROFILES is an environment JSON object keyed by profile.
Each entry accepts login/password or storageState. Supply values at runtime.
Run node replay.mjs CASE. Evidence goes into a new folder under evidence/.
The browser is visible by default; set OQA_HEADLESS=1 for unattended runs.
OQA_ACTION_DELAY_MS sets the pause after each action (default 700).
OQA_STEP_DELAY_MS sets the pause after each checked step (default 1500).
Both accept whole milliseconds from 0 to 60000; 0 disables the pause.
OQA_FAST=1 is fast mode: both pauses are 0 whatever the two values above say.
These viewing pauses do not change application assertion timeouts.
The archive may contain test fixture values. Keep it within your test team.
""")
    return content.getvalue()
