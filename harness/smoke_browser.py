"""Exercise one visual case using a real Odoo server and playwright-cli."""
import os
import io
import csv
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oqa import analysis, claims, connect, fixtures, intake, live, report, script_bundle, sitemap, worker_mcp, workspace
import cli_agents
from oqa.browser import launch
from oqa.odoo_ui import Odoo
from oqa.secrets import MemoryStore
from oqa.state import Session
from oqa.store import write_json


def check_imports(page, base_url, action, run_prefix, site_name):
    """Run the four CSV fixture scenarios through the native Odoo import wizard."""
    odoo = Odoo(page, base_url)
    directory = Path(__file__).with_name("fixtures")
    checks = (
        ("items-valid", ""),
        ("items-required", "missing required|field.*name"),
        ("items-missing-site", "no matching record|could not find|cannot be found"),
        ("items-duplicate", "must be unique|duplicate key"),
    )
    for index, (name, expected_error) in enumerate(checks):
        prefix = f"{run_prefix}-{index}"
        page.goto(f"{base_url}/odoo/action-{action}")
        page.locator(".o_cp_action_menus .dropdown-toggle").click()
        page.locator(".o_import_menu").click()
        payload = io.StringIO()
        with (directory / f"{name}.csv").open(encoding="utf-8", newline="") as source:
            csv.writer(payload).writerows([
                [value.format(run=prefix, site=site_name) for value in row] for row in csv.reader(source)
            ])
        with page.expect_file_chooser() as chooser:
            page.get_by_role("button", name="Upload Data File", exact=True).click()
        chooser.value.set_files({
            "name": name + ".csv", "mimeType": "text/csv", "buffer": payload.getvalue().encode("utf-8"),
        })
        page.get_by_role("button", name="Import", exact=True).click()
        domain = [["code", "in", [prefix + "-A", prefix + "-B"]]]
        if expected_error:
            errors = page.locator(".o_import_data_content").get_by_text(re.compile(expected_error, re.I))
            errors.first.wait_for()
            assert odoo.records("oqa.harness.item", domain, ["code"]) == []
        else:
            page.locator(".o_import_action").wait_for(state="hidden")
            rows = odoo.records("oqa.harness.item", domain, ["code", "site_id", "cost"])
            assert sorted(row["cost"] for row in rows) == [7, 11]
            assert {row["site_id"][1] for row in rows} == {site_name}
        print(f"Import wizard {name}: passed", flush=True)


def smoke(root):
    mapper = os.environ.get("OQA_SMOKE_MAPPER")
    case = "SMOKE-01"
    workbook = root / "browser-smoke.xlsx"
    source = Workbook()
    source.active.append(["ID", "Title", "Role", "Steps", "Expected Result", "Status"])
    source.active.append([case, "Check the Items list", "Manager",
                          "1. Open Items and inspect the Cost column."
                          + ("\n2. Create a Site and its Item, then verify the new item in the list."
                             "\n3. Upload a run-specific CSV to the native import wizard." if mapper else ""),
                          "Cost column is visible" + (" and the prepared Item appears in the list." if mapper else "."),
                          None])
    source.save(workbook)
    original_bytes = workbook.read_bytes()
    store = MemoryStore()
    base_url = os.environ.get("OQA_SMOKE_URL", "http://127.0.0.1:8079")
    action = os.environ["OQA_SMOKE_ITEM_ACTION"]
    ws = workspace.save({
        "name": "browser-smoke", "url": base_url, "db": "oqa_harness",
        "profiles": [{"name": "Manager", "login": "oqa-smoke-manager"}],
    }, store, {"Manager": os.environ["OQA_SMOKE_PASSWORD"]})
    cases = intake.select(intake.build(workbook), case)
    session = Session.create(ws["name"], workbook, cases, channel="path")
    packet = analysis.template(cases, ws, session)
    steps = [
        {"n": 1, "action": f"Open the Items list at {base_url}/odoo/action-{action}", "expected": "Cost column appears"},
    ]
    if mapper:
        store.set(workspace.secret_ref(ws["name"], "Mapper"), os.environ["OQA_SMOKE_PASSWORD"])
        with launch() as new_page:
            _, page = new_page()
            connect.login(page, ws, {"name": "Mapper", "login": mapper}, store)
            data = sitemap.build(Odoo(page, base_url).rpc, profile="Mapper")
            sitemap.save(ws["name"], data)
        steps.append({"n": 2, "action": "Prepare a Site and a related Item", "expected": "The created item appears"})
        steps.append({"n": 3, "action": "Upload the CSV", "expected": "The wizard previews Widget A and Widget B"})
        packet["cases"][0]["refs"] = [{
            "text": "items-valid.csv", "kind": "file", "status": "ok",
            "path": str(Path(__file__).with_name("fixtures") / "items-valid.csv"),
        }]
    packet["cases"][0].update(profile="Manager", verdict="ready", steps=steps)
    write_json(session.dir / "analysis.json", packet)
    session.analysis_submitted()
    connect.connect(session, ws, store)
    write_json(session.dir / "strategy.json", {"order": [case], "excluded": []})
    session.strategy_ready("harness-smoke")
    session.confirm("harness-smoke", "harness-smoke")
    if os.environ.get("OQA_SMOKE_DISPATCH") == "1":
        outcome = cli_agents.run(session, "haiku", "sonnet", 150)
        print(outcome, flush=True)
        if outcome["results"].get(case, {}).get("status") != "PASS":
            for log in session.dir.glob("logs/host-*/worker.log"):
                print(log.read_text(encoding="utf-8"))
            raise AssertionError("Claude worker did not complete the case.")
        print("Claude worker produced a live result and evidence.", flush=True)
        return
    assert claims.claim(session, "smoke", case)[0] == case
    live.start(session, case)
    locator = "getByRole('columnheader', { name: 'Cost' })"
    code = f"await page.goto('{base_url}/odoo/action-{action}');\nawait page.{locator}.waitFor();"
    live._run(live.browser_case(session, case), "run-code", "async page => {\n" + code + "\n}")
    live.step(session, case, 1, "PASS", "Cost column is visible", locator, code, "Cost")
    if mapper:
        prefix = fixtures.run_key(session, case, "csv")
        site_code = fixtures.run_key(session, case, "site")
        item_code = fixtures.run_key(session, case, "item")
        fixtures.ui_create(session, case, "oqa.harness.site", "code",
                       {"name": site_code, "code": site_code})
        fixtures.ui_create(session, case, "oqa.harness.item", "code", {
            "name": item_code, "code": item_code, "site_id": {"$fixture": site_code}, "cost": 7,
        })
        code = "\n".join([f"await page.goto('/odoo/action-{action}');",
                          f"await page.getByRole('searchbox').fill('{item_code}');",
                          f"await page.getByRole('link', {{name: 'Search Code for: {item_code}', exact: true}}).click();"])
        locator = f"locator('td[name=code]').filter({{ hasText: /^{item_code}$/ }})"
        code += f"\nawait page.{locator}.waitFor();"
        live.act(session, case, "run-code", [], code, record=True)
        live.step(session, case, 2, "PASS", f"Item {item_code} is visible", locator, "", item_code)
        enter_import = [
            f"await page.goto('{base_url}/odoo/action-{action}');",
            "await page.locator('.o_cp_action_menus .dropdown-toggle').click();",
            "await page.locator('.o_import_menu').click();",
        ]
        browser_id = live.browser_case(session, case)
        live.act(session, case, "run-code", [], "\n".join(enter_import), record=True)
        live.run_actions(browser_id, [
            "await page.getByRole('button', {name: 'Upload Data File', exact: true}).click();",
        ])
        worker_mcp.call(session, case, "smoke", "upload_csv", {
            "name": "items-valid.csv", "values": {"run": prefix + "-preview", "site": site_code},
        })
        live.run_actions(browser_id, ["await page.locator('.o_import_data_content').waitFor();"])
        live.step(session, case, 3, "PASS", "The import preview contains the expected CSV rows",
                  ".o_import_data_content", "", "Widget A")
    live.end(session, case, "PASS", "")
    if mapper:
        with launch() as new_page:
            _, page = new_page()
            connect.login(page, ws, ws["profiles"][0], store)
            check_imports(page, base_url, action, prefix, site_code)
    for _ in range(2):
        _, status, reason = live.verify_replay(session, case, headed=False)
        assert status == "PASS", reason
    result = report.build(session, ws)
    assert Path(result["main"]).is_file() and workbook.read_bytes() == original_bytes
    bundle_bytes = script_bundle.build(session)
    modules = os.environ.get("OQA_SMOKE_NODE_MODULES")
    if modules:
        bundle = root / "bundle"
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as archive:
            archive.extractall(bundle)
        for package in ("playwright", "playwright-core"):
            shutil.copytree(Path(modules) / package, bundle / "node_modules" / package)
        subprocess.run(["node", "replay.mjs", case], cwd=bundle, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, env={
            **os.environ, "OQA_LOGIN": "oqa-smoke-manager", "OQA_PASSWORD": os.environ["OQA_SMOKE_PASSWORD"],
            "OQA_DB": ws["db"],
        })
    destination = os.environ.get("OQA_SMOKE_OUT")
    if destination:
        output = Path(destination).resolve()
        output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result["main"], output / "smoke-result.xlsx")
        (output / "oqa-scripts.zip").write_bytes(bundle_bytes)
    print("Live evidence, two replays and Excel copy passed.")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="oqa-browser-smoke-", ignore_cleanup_errors=True) as folder:
        os.environ["ODOO_TEST_HOME"] = folder
        smoke(Path(folder))
