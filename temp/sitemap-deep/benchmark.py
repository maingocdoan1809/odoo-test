"""Measure first-choice correctness with and without the real odoo-test sitemap."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.styles import Alignment

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from oqa import browser, connect, mcp_server, paths, secrets, sitemap, workspace
from oqa.store import write_json
from oqa.worker_mcp import schema, strings

WORKSPACE = "sitemap_deep"
ACCOUNT_WORKSPACE = "oqa_harness_20"
DESTINATIONS = json.loads((HERE / "destinations.json").read_text(encoding="utf-8"))
RECORD = "ZZ_AUTOTEST_H10_REQ"
REFERENCE_FIELDS = ("x_ab_customer_reference", "x_ab_carrier_reference")
MAP_TOOLS = {tool["name"] for tool in mcp_server.TOOLS}
MENU_LABELS = {"Operations", "Finance", "Customer Service", "Dispatch", "Warehouse",
               "Billing", "Orders", "Reviews", "Archive", "Queue", "Records", "Details"}
CASES = {
    "DEEP01": {
        "title": "Chọn đúng Queue trong ba menu cùng tên",
        "task": "Mở danh sách Queue dành cho giao vận theo nhánh Operations → Dispatch → Reviews. "
                "Kiểm tra danh sách có cột Loading Site và có đúng bản ghi " + RECORD + ".",
        "destination": "dispatch_queue", "header": "Loading Site",
        "distractors": ("dispatch_queue", "billing_queue", "warehouse_queue"),
    },
    "DEEP02": {
        "title": "Vào kho hồ sơ dịch vụ, qua bốn cấp menu",
        "task": "Mở Records trong kho hồ sơ của bộ phận chăm sóc khách hàng: Customer Service → Orders → Archive. "
                "Xác nhận cột Service Site và có đúng bản ghi " + RECORD + ". Có một Records khác thuộc Finance.",
        "destination": "customer_records", "header": "Service Site",
        "distractors": ("customer_records", "billing_records"),
    },
    "DEEP03": {
        "title": "Chọn đúng field Reference trong form",
        "task": "Mở Operations → Dispatch → Details và mở request " + RECORD + ". "
                "Form có hai field cùng hiển thị nhãn Reference. Hãy đọc Reference của đơn vị vận chuyển. "
                "Dùng read_reference để nộp lựa chọn field, rồi xác nhận giá trị hiển thị.",
        "destination": "transport_details", "distractors": ("transport_details",),
        "field": "x_ab_carrier_reference", "expected": "CAR-7129",
    },
    "DEEP04": {
        "title": "Phân biệt các Queue chỉ bằng cột đặc thù",
        "task": "Có nhiều màn hình cùng tên Queue. Hãy mở Queue có cột Storage Site, "
                "rồi xác nhận cột đó và bản ghi " + RECORD + ".",
        "destination": "warehouse_queue", "header": "Storage Site",
        "distractors": ("dispatch_queue", "billing_queue", "warehouse_queue"),
    },
}
UI_TOOLS = [
    schema("inspect", "Read the current UI, open menus and current URL."),
    schema("click", "Click a visible menu or record by role and displayed name.",
           (*strings("role", "name"), ("occurrence", "integer", {})), ("role", "name")),
    schema("route", "Open a known relative benchmark Odoo action URL, /odoo/action-<id>.", strings("path"), ("path",)),
    schema("read_reference", "Read one visible Reference field, by technical name or 1-based occurrence in form order.",
           (*strings("technical_name"), ("occurrence", "integer", {}))),
]


def setup():
    config = workspace.load(ACCOUNT_WORKSPACE)
    config.update(name=WORKSPACE, url="http://127.0.0.1:8081", db="oqa_sitemap_deep")
    data = json.loads((HERE / "admin-sitemap.json").read_text(encoding="utf-8"))
    scope()
    write_json(paths.workspace_dir(WORKSPACE) / "workspace.json", config)
    sitemap.save(WORKSPACE, data)
    intended = {f"window:{aid}" for aid in DESTINATIONS.values()}
    missing = intended.difference(data["nodes"])
    if missing:
        raise RuntimeError(f"Missing benchmark actions in Manager sitemap: {sorted(missing)}")
    print(sitemap.header(data, "en"), flush=True)


def scope():
    os.environ["ODOO_TEST_HOME"] = str(HERE / "runtime")


class BrowserTrial:
    def __init__(self, folder, case, page, config):
        self.folder, self.spec, self.page, self.config = folder, CASES[case], page, config
        self.flags = {}
        self.started = time.monotonic()
        self.write()

    def write(self):
        write_json(self.folder / "observed.json", self.flags)

    def snapshot(self):
        ui = self.page
        match = re.search(r"/action-(\d+)(?:/(\d+))?", urlsplit(ui.url).path)
        if match:
            destination = int(match[1])
            candidates = {DESTINATIONS[name] for name in self.spec["distractors"]}
            if destination in candidates and "first_screen" not in self.flags:
                self.flags["first_screen"] = destination
                self.flags["first_screen_correct"] = destination == DESTINATIONS[self.spec["destination"]]
                ui.screenshot(path=str(self.folder / "first-screen.png"))
            if destination == DESTINATIONS[self.spec["destination"]]:
                if "header" in self.spec:
                    header = ui.get_by_role("columnheader", name=self.spec["header"])
                    row = ui.get_by_role("cell", name=RECORD, exact=True)
                    if header.is_visible() and row.is_visible() and not self.flags.get("verified"):
                        self.flags["verified"] = True
                        ui.screenshot(path=str(self.folder / "verified.png"))
                elif match[2] and ui.locator(".o_form_view").is_visible():
                    self.flags["form_reached"] = True
        self.write()
        overlays = [menu.aria_snapshot() for menu in ui.locator(".o-dropdown--menu").all() if menu.is_visible()]
        return (f"URL: {urlsplit(ui.url).path}\n"
                + "\n".join(overlays) + "\n" + ui.locator("body").aria_snapshot()[:6800])

    def tool(self, name, arg):
        ui = self.page
        if name in MAP_TOOLS:
            return mcp_server.call(WORKSPACE, name, arg)
        if name == "route":
            path = arg.get("path", "")
            match = re.fullmatch(r"/odoo/action-(\d+)", path)
            if not match or int(match[1]) not in DESTINATIONS.values():
                raise ValueError("Provide an available benchmark action URL.")
            ui.goto(self.config["url"] + path, wait_until="domcontentloaded")
            ui.locator(".o_list_view").wait_for()
        elif name == "click":
            role, label = arg["role"], arg["name"]
            if role in {"menuitem", "button", "link"} and label in MENU_LABELS:
                target = ui.get_by_role(role, name=label, exact=True)
            elif role in {"row", "cell"} and label == RECORD:
                target = ui.get_by_role(role, name=label, exact=role == "cell")
            else:
                raise ValueError("Choose a visible benchmark menu or the named test record.")
            slot = arg.get("occurrence")
            (target.nth(slot - 1) if slot is not None and slot > 0 else target).click()
        elif name == "read_reference":
            if "field" not in self.spec or not self.flags.get("form_reached"):
                raise ValueError("Open the requested Details form first.")
            selector = ",".join(f'.o_form_view .o_field_widget[name="{key}"]' for key in REFERENCE_FIELDS)
            fields = ui.locator(selector)
            if arg.get("technical_name") in REFERENCE_FIELDS:
                field = ui.locator(f'.o_form_view .o_field_widget[name="{arg["technical_name"]}"]')
            elif arg.get("occurrence") in (1, 2):
                field = fields.nth(arg["occurrence"] - 1)
            else:
                raise ValueError("Select a technical name or occurrence 1 or 2.")
            field.wait_for(state="visible")
            key, value = field.get_attribute("name"), field.inner_text().strip()
            correct = key == self.spec["field"]
            if "first_field" not in self.flags:
                self.flags.update(first_field=key, first_field_correct=correct)
                field.screenshot(path=str(self.folder / "first-field.png"))
            if correct and value == self.spec["expected"]:
                self.flags["verified"] = True
                field.screenshot(path=str(self.folder / "verified.png"))
            self.write()
            return f"Reference: {value}"
        elif name != "inspect":
            raise ValueError("Unknown tool.")
        if name != "inspect":
            ui.wait_for_timeout(650)
        return self.snapshot()

    def call(self, name, arg):
        when = round(time.monotonic() - self.started, 2)
        try:
            result, error = self.tool(name, arg), ""
        except Exception as exc:
            result, error = "", str(exc).splitlines()[0][:350]
        with (self.folder / "tools.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps({"t_s": when, "tool": name, "args": arg, "result": result[:8500], "error": error},
                                 ensure_ascii=False) + "\n")
        if error:
            raise sitemap.SitemapError(error)
        return result


def serve(folder, case, arm):
    scope()
    config = workspace.load(WORKSPACE)
    if config["db"] != "oqa_sitemap_deep" or config["url"] != "http://127.0.0.1:8081":
        raise ValueError("Use the isolated test database.")
    with browser.launch() as new_page:
        _, page = new_page()
        page.set_default_timeout(5000)
        profile = next(p for p in config["profiles"] if p["name"] == "Manager")
        connect.login(page, {**config, "name": ACCOUNT_WORKSPACE}, profile, secrets.default_store())
        trial = BrowserTrial(folder, case, page, config)
        tools = UI_TOOLS + (mcp_server.TOOLS if arm == "on" else [])
        mcp_server.serve(tools=tools, tool_call=trial.call)


def run_trial(root, repeat, case, arm):
    folder = root / f"r{repeat}-{case}-{arm}"
    folder.mkdir()
    config = folder / "mcp.json"
    write_json(config, {"mcpServers": {"bench": {"command": sys.executable, "args": [
        str(Path(__file__).resolve()), "serve", str(folder), case, arm]}}})
    prompt = (
        "Thực hiện testcase chỉ đọc bằng các MCP tool được cấp. Odoo Harness đang đăng nhập với vai trò Manager. "
        "Không có lịch sử case. Không lưu hay thay đổi dữ liệu. "
        "Dùng inspect và click để điều hướng. Nếu biết đường dẫn nội bộ từ công cụ, có thể dùng route. "
        "Thực hiện trên UI, đọc giá trị thấy được, sau đó trả lời ngắn gọn.\n\n" + CASES[case]["task"]
    )
    command = [shutil.which("claude"), "--print", "--model", "haiku", "--no-session-persistence",
               "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
               "--mcp-config", str(config), "--tools", "", "--allowedTools", "mcp__bench",
               "--permission-mode", "dontAsk"]
    started = time.monotonic()
    with (folder / "worker.log").open("w", encoding="utf-8") as out:
        try:
            status = subprocess.run(command, input=prompt, encoding="utf-8", stdout=out, stderr=subprocess.STDOUT,
                                    cwd=folder, timeout=180).returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
    evidence = folder / "observed.json"
    observed = json.loads(evidence.read_text(encoding="utf-8")) if evidence.exists() else {}
    trace = folder / "tools.jsonl"
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    result = {"repeat": repeat, "case": case, "arm": arm, "exit": status,
              "wall_s": round(time.monotonic() - started, 2),
              "navigation": sum(c["tool"] in {"click", "route"} for c in calls),
              "sitemap_calls": sum(c["tool"] in MAP_TOOLS for c in calls),
              "evidence": str(folder.relative_to(root)), **observed}
    write_json(folder / "result.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def run(repeats, only=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "Testcases"
    sheet.append(["ID", "Title", "Role", "Steps", "Expected Result"])
    for case, spec in CASES.items():
        expected = ("Danh sách đúng nhánh, cột " + spec["header"] + " và bản ghi " + RECORD
                    if "header" in spec else "Reference của đơn vị vận chuyển hiển thị " + spec["expected"])
        sheet.append([case, spec["title"], "Manager", spec["task"], expected])
    for col, width in zip("ABCDE", (13, 40, 20, 92, 70)):
        sheet.column_dimensions[col].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[row[0].row].height = 55
    sheet.freeze_panes = "D2"
    book.save(HERE / "sitemap-deep.xlsx")

    root = HERE / time.strftime("run-%Y%m%d-%H%M%S")
    root.mkdir()
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for repetition in range(1, repeats + 1):
            jobs = [pool.submit(run_trial, root, repetition, case, arm)
                    for index, case in enumerate(CASES) if only is None or case == only
                    for arm in (("on", "off") if (index + repetition) % 2 else ("off", "on"))]
            results.extend(job.result() for job in jobs)
            write_json(root / "summary.json", results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["setup", "run", "serve"])
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("case", nargs="?", choices=CASES)
    parser.add_argument("arm", nargs="?", choices=["on", "off"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--only", choices=CASES)
    args = parser.parse_args()
    if args.mode == "setup":
        setup()
    elif args.mode == "run":
        run(args.repeats, args.only)
    else:
        serve(args.folder, args.case, args.arm)
