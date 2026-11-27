"""Regressions for the whole-codebase review of 2026-11-26: each test fails on the code before its fix."""
import json
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from oqa import events, knowledge, live, pgadmin, refs, sitemap, store, textfix, worker_mcp
from oqa.state import GateError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import guard  # noqa: E402


@pytest.mark.parametrize("sql", ["select '--'; delete from res_partner", "select id into tmp_x from res_partner"])
def test_evidence_sql_that_writes_is_refused(sql):
    with pytest.raises(GateError):
        pgadmin.check_sql(sql)


def test_a_line_comment_is_never_split_into_live_sql():
    sql = pgadmin.check_sql("select 1 -- where 1=1; delete from res_partner")
    assert pgadmin.format_sql(sql) == sql
    assert pgadmin.format_sql("select a from t where b='x' order by a") == "select a\nfrom t\nwhere b='x'\norder by a"


@pytest.mark.parametrize("tool,command", [
    ("Bash", 'python "C:/Users/x/odoo-test/oqa.py" confirm'),
    ("Bash", "python 'C:\\a\\oqa.py' stop --ws a"),
    ("Bash", "python C:/a/oqa.py --json confirm"),
    ("Bash", "python oqa.py --ws demo --session 1 resume"),
    ("PowerShell", "python oqa.py confirm"),
    ("PowerShell", '& python "C:/x y/oqa.py" select --all'),
])
def test_confirming_for_the_user_is_blocked_however_oqa_is_written(tool, command):
    assert guard.verdict({"tool_name": tool, "tool_input": {"command": command}})


@pytest.mark.parametrize("command", ["python oqa.py live start TC01", "python oqa.py wait", "python oqa.py --json status"])
def test_ordinary_commands_pass_the_guard(command):
    assert guard.verdict({"tool_name": "Bash", "tool_input": {"command": command}}) is None


@pytest.mark.parametrize("text,fixed", [
    ("Bấm nút “CÓ”", "Bấm nút “CÓ”"),
    ("Trạng thái “ĐÃ”", "Trạng thái “ĐÃ”"),
    ("CAFÉ’s", "CAFÉ’s"),
    ("tÄƒng lÃªn", "tăng lên"),
    ("BÆ°á»›c 1", "Bước 1"),
])
def test_text_repair_leaves_correct_text_alone(text, fixed):
    assert textfix.repair_text(text) == fixed


def test_a_write_waits_for_a_reader_instead_of_failing(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    real, calls = store.os.replace, []

    def busy_twice(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(store.os, "replace", busy_twice)
    store.write_json(target, {"a": 1})
    assert store.read_json(target) == {"a": 1} and len(calls) == 3


def test_concurrent_event_writers_lose_no_line(tmp_path):
    def writer(name):
        for i in range(150):
            events.append(tmp_path, "phase", phase="EXECUTE", text=f"{name}-{i}")

    threads = [threading.Thread(target=writer, args=(f"w{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    raw = (tmp_path / "events.jsonl").read_bytes()
    assert b"\r\n" not in raw
    assert len(events.read(tmp_path)) == 600


def test_the_note_tool_can_reach_the_claims():
    assert worker_mcp.claims.add_note


def test_a_fact_with_the_source_separator_is_refused():
    with pytest.raises(knowledge.KnowledgeError):
        knowledge._clean("sale.order", "navigation", "Open Sales -> Orders -> New")


def test_docx_tables_give_their_text_not_their_markup(tmp_path):
    doc = tmp_path / "bd.docx"
    body = ('<w:document><w:body><w:tbl><w:tblPr/><w:tr><w:tc><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:tab/><w:t xml:space="preserve"> world</w:t></w:r></w:p>'
            '</w:tc></w:tr></w:tbl></w:body></w:document>')
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("word/document.xml", body)
    assert refs._ooxml_text(doc, "", r"word/document\.xml") == ["Hello world"]


@pytest.mark.parametrize("expected,observed", [("1000", "1,000.00"), ("1.500.000", "1500000.00"), ("2026-01-01", "01/01/2026"), ("3", "3.0")])
def test_numbers_and_dates_compare_by_value(expected, observed):
    assert live.number_forms(expected) & live.number_forms(observed)


@pytest.mark.parametrize("expected,observed", [
    ("50", "50,000"), ("1234", "1.234"), ("123", "1.2.3"),
    ("2026-03-04", "03/04/2026"), ("2026-04-03", "03/04/2026"),
])
def test_numbers_and_dates_do_not_guess_ambiguous_values(expected, observed):
    assert live.number_forms(expected).isdisjoint(live.number_forms(observed))


@pytest.mark.skipif(not shutil.which("node"), reason="Node executes the generated replay assertion")
def test_replay_assertion_rejects_a_grouped_number_that_could_mean_the_expected_decimal():
    script = """
global.getComputedStyle = () => ({visibility: 'visible', display: 'block'});
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
const check = new AsyncFunction('page', CHECK);
const page = text => ({ locator: () => ({evaluate: (fn, args) =>
    fn({innerText: text, getClientRects: () => [1], matches: () => false, querySelectorAll: () => []}, args)}) });
(async () => {
    await check(page('Total 50.00'));
    try { await check(page('Total 50,000')); }
    catch (error) { if (error.message.includes('Expected values missing')) return; throw error; }
    throw new Error('The grouped number incorrectly passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    script = script.replace("CHECK", json.dumps(live.outcome_check("Total", ["50"], 5)))
    done = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr


def test_the_outcome_check_retries_until_its_timeout():
    line = live.outcome_check("Total", ["1000"], 4000)
    assert line.startswith("await (async () =>") and "deadline" in line and "setTimeout(resolve, 250)" in line


def test_a_ui_fixture_is_kept_whole_and_runs_before_the_step_opens_its_screen():
    fixture = "await page.goto('/odoo/action-site/new');\nawait page.locator('.o_field_widget[name=name] input').fill('AUTO_TEST_site');\nawait page.locator('.o_form_button_save').click();"
    log = [{"kind": "fixture_prepared", "case": "C", "attempt": 1, "key": "AUTO_TEST_site", "code": fixture}]
    verified = "await page.goto('/odoo/action-item');\nawait page.locator('.o_list_button_add').click();\nawait page.locator('.o_form_button_save').click();"
    code, _ = live._step_actions(log, "C", 1, 1, verified)
    assert code[:3] == fixture.splitlines()
    assert code[3] == "await page.goto('/odoo/action-item');"


def test_a_fixture_already_in_the_verified_code_is_not_added_again():
    fixture = "await page.goto('/odoo/action-site/new');\nawait page.locator('.o_form_button_save').click();"
    log = [{"kind": "fixture_prepared", "case": "C", "attempt": 1, "key": "k", "code": fixture}]
    code, _ = live._step_actions(log, "C", 1, 1, fixture.replace(";", "") + "\nawait page.goto('/odoo/action-item');")
    assert len(code) == 3


def test_a_slow_browser_call_is_a_refusal_not_a_crash(monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

    monkeypatch.setattr(live, "_cli", lambda: "playwright-cli")
    monkeypatch.setattr(live.subprocess, "run", slow)
    with pytest.raises(GateError, match="did not answer"):
        live._run("case", "run-code", "x", timeout=1)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows refuses to rename a folder a process works in")
def test_publishing_over_a_folder_in_use_refuses_and_keeps_it_whole(tmp_path):
    from oqa import plugin
    folder = tmp_path / "odoo-test"
    folder.mkdir()
    (folder / "oqa.py").write_text("x", encoding="utf-8")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(folder))
    try:
        time.sleep(0.5)
        with pytest.raises(GateError, match="in use"):
            plugin.move_aside(folder)
        assert (folder / "oqa.py").is_file()
    finally:
        holder.kill()
        holder.wait()
    aside = plugin.move_aside(folder)
    plugin.put_back(aside, folder)
    assert (folder / "oqa.py").is_file()


def test_unreadable_actions_are_listed_not_fatal():
    def rpc(model, method, args, kwargs=None):
        if model == "ir.ui.menu":
            return [{"id": 1, "name": "Sales", "parent_id": False, "action": "ir.actions.act_window,5", "sequence": 1}]
        if model.startswith("ir.actions"):
            raise AssertionError(f"{model}.read: not allowed")
        return []

    data = sitemap._build(rpc)
    assert any("not readable" in item["why"] for item in data["unresolved"])
