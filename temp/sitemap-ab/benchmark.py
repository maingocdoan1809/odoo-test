"""Paired, read-only browser benchmark for the odoo-test sitemap MCP tools."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from oqa import browser, connect, mcp_server, secrets, sitemap, workspace
from oqa.worker_mcp import schema, strings

WORKSPACE = "oqa_harness_20"
TITLE = "ZZ_AUTOTEST_H10_REQ"
MODEL = "haiku"
CASES = {
    "NAV01": (
        "Mở danh sách Sites, mở một dòng và đọc Code/Active. Sang Items, mở một dòng, "
        "đọc Site/Cost. Sang Requests và mở request " + TITLE + "; xác nhận Total = 10 và có bảng Item, Quantity, Subtotal.",
        ("sites_list", "site_form", "items_list", "item_form", "requests_list", "manager_request"),
    ),
    "NAV02": (
        "Mở Items và xác nhận danh sách có Code và Cost. Vào màn hình Import của Items, "
        "xác nhận có Upload Data File. Quay về Items, sang danh sách Requests, vào màn hình Import của Requests, "
        "xác nhận nút Upload Data File và breadcrumb Requests. Cuối cùng hủy để về danh sách Requests.",
        ("items_list", "items_import", "items_list", "requests_list", "requests_import", "requests_list"),
    ),
    "NAV03": (
        "Từ Manager, mở danh sách Requests rồi mở " + TITLE + ". Kiểm tra Submitted, Approve, "
        "Reset to Draft và Total = 10. Đổi sang Operator, đi lại từ danh sách Requests vào đúng request đó. "
        "Kiểm tra vẫn Submitted, Total = 10 và có Reset to Draft, không có Approve. "
        "Sau đó sang Sites và mở một dòng để đối chiếu form có Code và Active.",
        ("requests_list", "manager_request", "operator_list", "operator_request", "sites_list", "site_form"),
    ),
}

BROWSER_TOOLS = [
    schema("inspect", "Read the visible browser and URL."),
    schema("click", "Click one visible UI target by accessibility role and name. Exact name by default; set occurrence to choose a same-name target.",
           (*strings("role", "name"), ("occurrence", "integer", {})), ("role", "name")),
    schema("route", "Open a relative Odoo Harness screen URL such as /odoo/action-<id> when known.", strings("path"), ("path",)),
    schema("profile", "Reopen the initial screen with the other named test account.", strings("name"), ("name",)),
]
MAP_NAMES = {tool["name"] for tool in mcp_server.TOOLS}
MENU_NAMES = {"Sites", "Items", "Requests", "Import records"}
BUTTON_NAMES = {"Actions menu", "Cancel"}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Trial:
    def __init__(self, folder, case, new_page, config):
        self.folder, self.case, self.new_page, self.config = folder, case, new_page, config
        self.checkpoints = CASES[case][1]
        self.done = 0
        self.started = time.monotonic()
        self.profile = "Manager"
        self.page = self.login("Manager")
        self.started = time.monotonic()
        self.allowed_actions = {
            key.split(":", 1)[1]
            for key, node in sitemap.load(WORKSPACE)["nodes"].items()
            if key.startswith("window:") and str(node.get("model", "")).startswith("oqa.harness.")
        }

    def login(self, name):
        if name not in {"Manager", "Operator"}:
            raise ValueError("Use Manager or Operator.")
        _, page = self.new_page()
        page.set_default_timeout(5500)
        profile = next(item for item in self.config["profiles"] if item["name"] == name)
        connect.login(page, self.config, profile, secrets.default_store())
        return page

    def check(self, name):
        page = self.page
        view = page.locator(".o_list_view")
        form = page.locator(".o_form_view")
        active = page.get_by_role("radio", name="Submitted", exact=True)
        if name.endswith("_import"):
            label = "Items" if name == "items_import" else "Requests"
            return (page.get_by_role("button", name="Upload Data File", exact=True).is_visible()
                    and page.get_by_role("link", name=label, exact=True).is_visible())
        if name.endswith("_list"):
            columns = {"sites_list": ("code", "active"), "items_list": ("code", "cost"),
                       "requests_list": ("total", "state"), "operator_list": ("total", "state")}
            return ((name != "operator_list" or self.profile == "Operator")
                    and all(view.locator(f'th[data-name="{field}"]').is_visible() for field in columns[name]))
        if name in {"item_form", "site_form"}:
            fields = ("site_id", "cost") if name == "item_form" else ("code", "active")
            return all(form.locator(f'.o_field_widget[name="{field}"]').is_visible() for field in fields)
        approver = name == "manager_request"
        return (
            self.profile == ("Manager" if approver else "Operator")
            and form.get_by_role("textbox", name="Name", exact=True).is_visible()
            and form.get_by_role("textbox", name="Name", exact=True).input_value() == TITLE
            and active.is_visible() and active.get_attribute("aria-checked") == "true"
            and form.get_by_role("button", name="Reset to Draft", exact=True).is_visible()
            and form.get_by_role("button", name="Approve", exact=True).is_visible() == approver
            and form.locator('.o_field_widget[name="total"]').inner_text().strip() == "10"
            and all(form.get_by_role("columnheader", name=label).is_visible() for label in ("Item", "Quantity", "Subtotal"))
        )

    def observe(self):
        if self.done < len(self.checkpoints) and self.check(self.checkpoints[self.done]):
            checkpoint = self.checkpoints[self.done]
            self.done += 1
            self.page.screenshot(path=str(self.folder / f"{self.done:02}-{checkpoint}.png"))
            with (self.folder / "checkpoints.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"name": checkpoint, "elapsed_s": round(time.monotonic() - self.started, 2),
                                      "url": urlsplit(self.page.url).path, "profile": self.profile}) + "\n")
        visible = self.page.locator("body").aria_snapshot(timeout=5500)
        menus = [menu.aria_snapshot(timeout=1500) for menu in self.page.locator(".o-dropdown--menu").all() if menu.is_visible()]
        overlay = "\n".join(menus)
        return (f"Profile: {self.profile}\nURL: {urlsplit(self.page.url).path}\nVerified checkpoints: "
                f"{self.done}/{len(self.checkpoints)}\n{overlay}\n{visible[:6800]}")

    def tool(self, name, args):
        if name in MAP_NAMES:
            return mcp_server.call(WORKSPACE, name, args)
        if name == "profile":
            if args["name"] != self.profile:
                self.page = self.login(args["name"])
                self.profile = args["name"]
        elif name == "route":
            path = args["path"]
            match = re.fullmatch(r"/odoo/action-(\d+)(?:/\d+)?", path)
            if not match or match[1] not in self.allowed_actions:
                raise ValueError("Choose an OQA Harness action path.")
            self.page.goto(self.config["url"] + path, wait_until="domcontentloaded")
        elif name == "click":
            role, label = args["role"], args["name"]
            if role == "menuitem" and label.strip().endswith("Import records"):
                label = "Import records"
            if not ((role in {"menuitem", "link"} and label in MENU_NAMES)
                    or (role == "button" and label in BUTTON_NAMES)
                    or (role in {"cell", "row"} and label.startswith("ZZ_AUTOTEST_"))):
                raise ValueError("Choose a read-only menu, breadcrumb, wizard control or synthetic record.")
            occurrence = args.get("occurrence")
            target = (self.page.get_by_text(label, exact=True) if label == "Import records"
                      else self.page.get_by_role(role, name=label, exact=role != "row"))
            (target.nth(occurrence - 1) if occurrence is not None else target).click()
        elif name != "inspect":
            raise ValueError("Unknown tool.")
        if name != "inspect":
            self.page.wait_for_timeout(550)
        return self.observe()

    def call(self, name, args):
        started = time.monotonic()
        error = ""
        try:
            result = self.tool(name, args)
        except Exception as exc:
            error = str(exc).splitlines()[0][:500]
            result = ""
        with (self.folder / "tools.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"tool": name, "args": args, "elapsed_s": round(started - self.started, 2),
                                  "result": result[:7500] if name in MAP_NAMES else result.split("\n", 3)[:3],
                                  "error": error}, ensure_ascii=False) + "\n")
        if error:
            raise sitemap.SitemapError(error)
        return result


def serve(folder, case, arm):
    config = workspace.load(WORKSPACE)
    if config["url"] != "http://127.0.0.1:8079" or config["db"] != "oqa_harness":
        raise ValueError("Benchmark requires the local oqa_harness database.")
    with browser.launch() as new_page:
        trial = Trial(folder, case, new_page, config)
        tools = BROWSER_TOOLS + (mcp_server.TOOLS if arm == "on" else [])
        mcp_server.serve(tools=tools, tool_call=trial.call)


def run_trial(folder, case, arm):
    folder.mkdir()
    config_path = folder / "mcp.json"
    write_json(config_path, {"mcpServers": {"bench": {"command": sys.executable, "args": [
        str(Path(__file__).resolve()), "serve", str(folder), case, arm]}}})
    prompt = (
        "Thực hiện testcase đọc trên Odoo Harness bằng các công cụ được cung cấp. "
        "Chỉ điều hướng, mở hoặc đọc dữ liệu có sẵn và hủy wizard. Không lưu, sửa, xóa, upload hay duyệt bản ghi. "
        "Thực hiện tuần tự. Gọi inspect sau khi điều hướng nếu giao diện còn đang tải. "
        "Hoàn thành khi số Verified checkpoints đủ. Khi không thể làm tiếp, tóm tắt điều đã thấy. "
        "Ngữ cảnh chung: workspace OQA Harness, bắt đầu bằng Manager, không có ghi chú case cũ.\n\n"
        + CASES[case][0]
    )
    command = [shutil.which("claude"), "--print", "--model", MODEL, "--no-session-persistence",
               "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
               "--mcp-config", str(config_path), "--tools", "", "--allowedTools", "mcp__bench",
               "--permission-mode", "dontAsk"]
    started = time.monotonic()
    with (folder / "worker.log").open("w", encoding="utf-8") as out:
        try:
            run = subprocess.run(command, input=prompt, encoding="utf-8", stdout=out, stderr=subprocess.STDOUT,
                                 cwd=folder, timeout=200)
            exit_code = run.returncode
        except subprocess.TimeoutExpired:
            exit_code = "timeout"
    def lines(file):
        path = folder / file
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    checks, tools = lines("checkpoints.jsonl"), lines("tools.jsonl")
    result = {"case": case, "sitemap": arm, "exit_code": exit_code, "verified": len(checks),
              "total": len(CASES[case][1]), "first_checkpoint_s": checks[0]["elapsed_s"] if checks else None,
              "last_checkpoint_s": checks[-1]["elapsed_s"] if checks else None,
              "wall_s": round(time.monotonic() - started, 2),
              "navigation_actions": sum(t["tool"] in {"click", "route", "profile"} for t in tools),
              "ui_inspects": sum(t["tool"] == "inspect" for t in tools),
              "sitemap_calls": sum(t["tool"] in MAP_NAMES for t in tools),
              "tool_errors": sum(bool(t["error"]) for t in tools)}
    write_json(folder / "result.json", result)
    print(json.dumps(result), flush=True)
    return result


def workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = "Testcases"
    sheet.append(["ID", "Title", "Role", "Steps", "Expected Result"])
    titles = ("Đối chiếu ba màn hình", "So sánh hai wizard Import", "Đối chiếu quyền theo profile")
    for (case, (description, checkpoints)), title in zip(CASES.items(), titles):
        sheet.append([case, title, "Manager, Operator" if case == "NAV03" else "Manager", description,
                      "Xác minh tuần tự " + str(len(checkpoints)) + " chặng trên giao diện. " + description])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in zip("ABCDE", (14, 36, 26, 82, 82)):
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        sheet.row_dimensions[row[0].row].height = 84
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "D2"
    book.save(HERE / "sitemap-ab.xlsx")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run", "serve"])
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("case", nargs="?", choices=CASES)
    parser.add_argument("arm", nargs="?", choices=["on", "off"])
    parser.add_argument("--reverse", action="store_true", help="Reverse which arm is submitted first per case.")
    args = parser.parse_args()
    if args.mode == "serve":
        return serve(args.folder, args.case, args.arm)
    workbook()
    destination = HERE / time.strftime("run-%Y%m%d-%H%M%S")
    destination.mkdir()
    tasks = [(case, arm) for i, case in enumerate(CASES) if args.case in (None, case)
             for arm in (("off", "on") if (i % 2 == 0) != args.reverse else ("on", "off"))
             if args.arm in (None, arm)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(run_trial, destination / f"{case}-{arm}", case, arm) for case, arm in tasks]
        write_json(destination / "summary.json", [job.result() for job in jobs])


if __name__ == "__main__":
    main()
