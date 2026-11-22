"""Use the installed OQA analysis template to compare real runs with and without knowledge."""

import json
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tricky_benchmark", HERE / "benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)

from oqa import intake, paths, sitemap, workspace
from oqa.state import Session
from oqa.store import write_json

def prepare(output):
    benchmark.scope()
    config = workspace.load("tricky_fresh")
    fresh_map = sitemap.load("tricky_fresh")
    os.environ["ODOO_TEST_HOME"] = str(output / "retrieval-home")
    config["name"] = name = "knowledge_ab"
    write_json(paths.workspace_dir(name) / "workspace.json", config)
    sitemap.save(name, fresh_map)
    workbook = HERE / "sitemap-tricky.xlsx"
    session = Session.create(name, workbook, intake.build(workbook))
    installed = Path.home() / ".agents/skills/odoo-test/oqa.py"
    command = [sys.executable, "-B", str(installed)]

    def run(*args):
        return json.loads(subprocess.check_output(command + [*args, "--ws", name, "--json"], encoding="utf-8"))

    def context():
        return run("analyze", "--template", "--session", session.id)["cases"][0]["_context"]

    before = context()
    scope = ("--screen", "oqa.harness.request", "--kind", "rule", "--action", "đọc",
             "--condition", "mã đơn vị vận chuyển")
    stages = (
        ("Chỉ xác nhận mã vận chuyển trên bản ghi Submitted. Bản Draft trùng tên là bản đối chứng.",
         "Kiểm tra trạng thái Submitted trên form."),
        ("Reference của đơn vị vận chuyển là field x_ab_carrier_reference trong nhóm Transport. Reference trong Customer là mã khách.",
         "Đối chiếu giá trị bằng tên kỹ thuật của field trong nhóm Transport."),
    )
    learned = [run("knowledge", "learn", *scope, "--fact", fact, "--evidence", evidence,
                   "--ref", "TRICKY01 evidence")["result"] for fact, evidence in stages]
    assert learned == ["added", "updated"]
    after = context()
    assert before["sitemap"] == after["sitemap"] and not before.get("knowledge")
    assert len(after["knowledge"]) == 2 and any("x_ab_carrier_reference" in fact for fact in after["knowledge"])
    write_json(output / "analysis-context.json", {"on": after, "off": before})
    print("Stored then updated one scoped knowledge entry; sitemap excerpts match.", flush=True)
    return {"on": after, "off": before}


def main():
    folder = HERE / time.strftime("knowledge-ab-%Y%m%d-%H%M%S")
    folder.mkdir()
    contexts = prepare(folder)
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for repeat, arms in enumerate((("on", "off"), ("off", "on")), 1):
            jobs = [pool.submit(benchmark.run_trial, folder, repeat, "fresh",
                                case_context=contexts[arm], label=arm) for arm in arms]
            results.extend(job.result() for job in jobs)
    write_json(folder / "summary.json", results)


if __name__ == "__main__":
    main()
