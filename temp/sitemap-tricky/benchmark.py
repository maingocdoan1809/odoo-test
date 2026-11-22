"""Check first choices and recovery when the OQA sitemap points to a stale screen."""

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
from openpyxl.styles import Alignment, Font

HERE = Path(__file__).resolve().parent
DEEP = HERE.parent / "sitemap-deep"
sys.path.insert(0, str(DEEP))
import benchmark as base
from oqa import browser, connect, mcp_server, paths, secrets, sitemap, workspace
from oqa.store import write_json

ORACLE = json.loads((HERE / "oracle.json").read_text(encoding="utf-8"))
ARMS = ("fresh", "stale", "off")
URL = "http://127.0.0.1:8082"
DB = "oqa_sitemap_tricky"
TASK = (
    "Có nhiều màn hình cùng tên Queue. Tìm màn hình đang có cột Storage Site và xác nhận cột trên danh sách. "
    "Có hai bản ghi cùng tên ZZ_AUTOTEST_H10_REQ. Mở bản ghi đang Submitted, bỏ qua bản Draft. "
    "Trong form có hai ô cùng nhãn Reference. Dùng read_reference để đọc mã của đơn vị vận chuyển "
    "rồi trả về đường dẫn màn hình, trạng thái bản ghi và giá trị đã đọc."
)


def scope():
    os.environ["ODOO_TEST_HOME"] = str(HERE / "runtime")


def setup():
    config = workspace.load(base.ACCOUNT_WORKSPACE)
    config.update(url=URL, db=DB)
    scope()
    maps = {"fresh": HERE / "fresh-sitemap.json", "stale": DEEP / "admin-sitemap.json"}
    for arm in ARMS:
        name = "tricky_" + arm
        write_json(paths.workspace_dir(name) / "workspace.json", {**config, "name": name})
        if arm in maps:
            data = json.loads(maps[arm].read_text(encoding="utf-8"))
            sitemap.save(name, data)
            hit = mcp_server.call(name, "sitemap_find", {"text": "Storage Site"})
            view = next(re.search(r"view:\S+", line)[0] for line in hit.splitlines() if "field 'Storage Site'" in line)
            route = mcp_server.call(name, "sitemap_path", {"target": view})
            action = ORACLE["correct_screen" if arm == "fresh" else "stale_screen"]
            if f"/odoo/action-{action}" not in route:
                raise RuntimeError(f"Unexpected Storage Site route for {arm}.")
            print(f"{arm}: /odoo/action-{action}", flush=True)


class Trial(base.BrowserTrial):
    def __init__(self, folder, page, config):
        self.folder, self.page, self.config = folder, page, config
        self.spec = {"field": "x_ab_carrier_reference", "expected": "CAR-7129"}
        self.flags = {"queue_visits": []}
        self.started = time.monotonic()
        self.write()

    def snapshot(self):
        page = self.page
        path = urlsplit(page.url).path
        match = re.fullmatch(r"/odoo/action-(\d+)(?:/(\d+))?", path)
        if match and int(match[1]) in ORACLE["candidates"]:
            action = int(match[1])
            expected_screen = action == ORACLE["correct_screen"]
            if match[2] is None:
                visits = self.flags["queue_visits"]
                if not visits or visits[-1]["action"] != action:
                    headings = page.get_by_role("columnheader").filter(visible=True).all_inner_texts()
                    visits.append({"action": action, "headings": headings})
                    page.screenshot(path=str(self.folder / f"queue-{len(visits)}.png"))
                if "first_screen_correct" not in self.flags:
                    self.flags.update(first_screen=action, first_screen_correct=expected_screen)
                if expected_screen and page.get_by_role("columnheader", name="Storage Site").is_visible():
                    self.flags["screen_verified"] = True
            elif expected_screen:
                record = int(match[2])
                correct = record == ORACLE["correct_record"]
                form = page.locator(".o_form_view")
                if form.is_visible():
                    self.flags["form_reached"] = True
                    self.flags["current_record"] = record
                    if "first_record_correct" not in self.flags:
                        self.flags.update(first_record=record, first_record_correct=correct)
                        form.screenshot(path=str(self.folder / "first-record.png"))
                    if correct and form.get_by_text("Submitted", exact=True).is_visible():
                        self.flags["submitted_verified"] = True
        self.write()
        menus = "\n".join(menu.aria_snapshot() for menu in page.locator(".o-dropdown--menu").all()
                          if menu.is_visible())
        return f"URL: {path}\n{menus}\n" + page.locator("body").aria_snapshot()[:6800]

    def tool(self, name, args):
        if name in base.MAP_TOOLS:
            return mcp_server.call(self.config["name"], name, args)
        if name == "route":
            match = re.fullmatch(r"/odoo/action-(\d+)", args.get("path", ""))
            if not match or int(match[1]) not in ORACLE["candidates"]:
                raise ValueError("Choose a Queue action path on this test system.")
        if name == "click" and args.get("role") == "row" and base.RECORD in args.get("name", ""):
            label = args["name"]
            if not re.fullmatch(re.escape(base.RECORD) + r"[\w\s.\[\]-]*", label):
                raise ValueError("Use the visible test record and its state.")
            row = self.page.get_by_role("row", name=label)
            slot = args.get("occurrence")
            (row.nth(slot - 1) if slot and slot > 0 else row).click()
            self.page.wait_for_timeout(650)
            return self.snapshot()
        if name == "read_reference":
            if not self.page.locator(".o_form_view").is_visible():
                raise ValueError("Open a Queue record form first.")
            result = super().tool(name, args)
            if self.flags.get("verified") and self.flags.get("current_record") != ORACLE["correct_record"]:
                self.flags["verified"] = False
                self.write()
            return result
        return super().tool(name, args)


def serve(folder, arm):
    scope()
    config = workspace.load("tricky_" + arm)
    if config["url"] != URL or config["db"] != DB:
        raise ValueError("Expected the isolated benchmark sandbox.")
    with browser.launch() as new_page:
        _, page = new_page()
        page.set_default_timeout(5000)
        profile = next(p for p in config["profiles"] if p["name"] == "Manager")
        connect.login(page, {**config, "name": base.ACCOUNT_WORKSPACE}, profile, secrets.default_store())
        trial = Trial(folder, page, config)
        mcp_server.serve(tools=base.UI_TOOLS + (mcp_server.TOOLS if arm != "off" else []), tool_call=trial.call)


def run_trial(root, repeat, arm, *, case_context=None, label=None):
    folder = root / f"r{repeat}-{label or arm}"
    folder.mkdir()
    config = folder / "mcp.json"
    write_json(config, {"mcpServers": {"bench": {"command": sys.executable, "args": [
        str(Path(__file__).resolve()), "serve", str(folder), arm]}}})
    prompt = (
        "Thực hiện nguyên case Odoo chỉ đọc bằng các công cụ được cấp. Bạn là Manager. Không có lịch sử case. "
        "Có thể dùng route nếu biết URL, hoặc inspect và click để khám phá. Vai trò row/cell dùng được với occurrence. "
        "Hãy quan sát giao diện thật để kiểm chứng từng bước. Không thay đổi dữ liệu.\n\n" + TASK
    )
    if case_context is not None:
        prompt += "\n\nGợi ý từ template phân tích của case:\n" + json.dumps(case_context, ensure_ascii=False)
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
    observed_path = folder / "observed.json"
    observed = json.loads(observed_path.read_text(encoding="utf-8")) if observed_path.exists() else {}
    trace = folder / "tools.jsonl"
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    result = {"repeat": repeat, "arm": label or arm, "exit": status,
              "wall_s": round(time.monotonic() - started, 2),
              "sitemap_calls": sum(c["tool"] in base.MAP_TOOLS for c in calls),
              "navigation": sum(c["tool"] in {"click", "route"} for c in calls),
              "evidence": folder.name, **observed}
    result["complete"] = all(result.get(key) for key in ("screen_verified", "submitted_verified", "verified"))
    write_json(folder / "result.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = "Testcases"
    sheet.append(["ID", "Title", "Role", "Steps", "Expected Result"])
    sheet.append(["TRICKY01", "Màn hình đổi vị trí, trùng bản ghi và nhãn field", "Manager", TASK,
                  "Danh sách thật có Storage Site. Chọn bản ghi Submitted và đọc mã vận chuyển CAR-7129."])
    for column, width in zip("ABCDE", (16, 48, 18, 96, 74)):
        sheet.column_dimensions[column].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for cell in sheet[2]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[2].height = 80
    book.save(HERE / "sitemap-tricky.xlsx")


def run(repeats):
    workbook()
    root = HERE / time.strftime("run-%Y%m%d-%H%M%S")
    root.mkdir()
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for repeat in range(1, repeats + 1):
            order = ARMS[repeat - 1:] + ARMS[:repeat - 1]
            jobs = [pool.submit(run_trial, root, repeat, arm) for arm in order]
            results.extend(job.result() for job in jobs)
            write_json(root / "summary.json", results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["setup", "run", "serve"])
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("arm", nargs="?", choices=ARMS)
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    if args.mode == "setup":
        setup()
    elif args.mode == "run":
        run(args.repeats)
    else:
        serve(args.folder, args.arm)
