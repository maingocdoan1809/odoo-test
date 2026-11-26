"""EXECUTE with playwright-cli: a fake `playwright-cli` stands in for the browser; oqa must frame the element itself and refuse a PASS that has no framed element."""
import os
import stat
import sys

import pytest

pytest.importorskip("playwright")

from odoofake import approve, fake, live  # noqa: E402,F401  (fixtures and helpers)
from oqa import events, live as livemod, replays  # noqa: E402
from oqa.state import GateError  # noqa: E402

FAKE = '''#!{python}
import sys, os, pathlib
args = [a for a in sys.argv[1:] if not a.startswith("-s=")]
raw = "--raw" in args
args = [a for a in args if a != "--raw"]
cmd = args[0]
if cmd == "run-code" and "includes(" in args[1]:
    print("### Error\\nExpected text not found" if "NOT-ON-PAGE" in args[1] else "### Result")
elif cmd == "run-code" and "boundingBox" not in args[1]:
    print("### Result")
elif cmd in ("eval", "run-code"):
    target = args[2] if cmd == "eval" and len(args) > 2 else (args[1] if cmd == "run-code" else "")
    if "missing" in target:
        print("### Error\\nError: strict mode violation / not found")
        sys.exit(0)
    print('"{{\\\\"x\\\\":100,\\\\"y\\\\":200,\\\\"w\\\\":300,\\\\"h\\\\":40,\\\\"vw\\\\":1440,\\\\"vh\\\\":900}}"')
elif cmd == "screenshot":
    from PIL import Image
    name = [a for a in args if a.startswith("--filename=")][0].split("=", 1)[1]
    folder = pathlib.Path(".playwright-cli"); folder.mkdir(exist_ok=True)
    Image.new("RGB", (1440, 900), "white").save(folder / name)
elif cmd in ("goto", "open", "state-load", "close"):
    print("### Page\\n- Page URL: http://x/web")
'''


SCRIPT = "".join(f"// step {n}\n// frame: .o_form_view\n// shows: ZZ_AUTOTEST_order\nawait page.goto('http://x/web');\nawait page.getByRole('button', {{ name: 'Save' }}).click();\n" for n in (1, 2, 3, 4))


def test_replay_resolves_a_relative_odoo_url(monkeypatch):
    commands = []
    monkeypatch.setattr(livemod, "_run", lambda *args: commands.append(args) or "")
    livemod.run_actions("case", ["await page.goto('/odoo/action-92/new');"])
    assert 'page.evaluate(path => new URL(path, location.href).href, "/odoo/action-92/new")' in commands[0][2]


def test_live_rejects_replay_text_not_present_in_the_page(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    with session.update() as state:
        state["channel"] = "path"
    with pytest.raises(GateError, match="verbatim"):
        livemod.step(session, "T-1", 1, "PASS", "Three records were visible",
                     target="body", verified_code="await page.goto('http://x/web');",
                     shows="NOT-ON-PAGE")
    assert not any(event["kind"] == "step" for event in events.read(session.dir))


@pytest.fixture
def pw(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "playwright-cli"
    exe.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    if os.name == "nt":
        run = livemod.subprocess.run

        def run_fake(args, **kwargs):
            if isinstance(args, list) and args[0] == str(exe):
                args = [sys.executable, *args]
            return run(args, **kwargs)

        monkeypatch.setattr(livemod, "_cli", lambda: str(exe))
        monkeypatch.setattr(livemod.subprocess, "run", run_fake)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ODOO_TEST_HOME", str(tmp_path / "home"))
    return exe


def test_a_case_is_run_live_with_a_frame_drawn_by_oqa(live, pw, tmp_path):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    with pytest.raises(GateError, match="no element is framed|No element is framed"):
        livemod.step(session, "T-1", 1, "PASS", "the list showed three orders", target="#missing")
    with pytest.raises(GateError, match="in order"):
        livemod.step(session, "T-1", 2, "PASS", "the list showed three orders", target=".o_list_view")
    with pytest.raises(GateError, match="a few words"):
        livemod.step(session, "T-1", 1, "PASS", "ok", target=".o_list_view")
    livemod.shot(session, "T-1", 1, "getByRole('cell', { name: 'LS1' })")
    livemod.step(session, "T-1", 1, "PASS", "the order list showed three rows", target="#missing")        # the element left the page: the picture taken before is used
    with pytest.raises(GateError, match="already recorded"):
        livemod.step(session, "T-1", 1, "PASS", "the order list showed three rows", target=".o_list_view")
    with pytest.raises(GateError, match="PASS needs every step"):
        livemod.end(session, "T-1", "PASS", "")
    done = [e for e in events.read(session.dir) if e["kind"] == "step"]
    assert done[0]["status"] == "PASS" and (session.dir / done[0]["images"][0]["annotated"]).exists()
    for n in (2, 3, 4):
        livemod.step(session, "T-1", n, "PASS", f"step {n} showed what was expected", target="e15")
    with pytest.raises(GateError, match="keeps the script"):
        livemod.end(session, "T-1", "PASS", "")                                           # a PASS leaves the case's script behind: no script, no PASS
    mine = tmp_path / "case.js"
    mine.write_text("// step 1\n// shows: x\nawait page.goto('http://x/web');\n", encoding="utf-8")
    with pytest.raises(GateError, match="cannot be replayed"):
        livemod.end(session, "T-1", "PASS", "", script=str(mine))                         # no frame, no text to check, missing steps: not a script that can be replayed
    mine.write_text(SCRIPT.replace("await page.goto('http://x/web');", "await page.getByRole('menuitem', { name: 'A' }).click();", 1), encoding="utf-8")
    with pytest.raises(GateError, match="must start with"):
        livemod.end(session, "T-1", "PASS", "", script=str(mine))                         # a script that does not start by going somewhere depends on where the browser was
    mine.write_text(SCRIPT, encoding="utf-8")
    livemod.end(session, "T-1", "PASS", "", script=str(mine))
    assert livemod.script_file(session, "T-1").read_text(encoding="utf-8") == mine.read_text(encoding="utf-8")
    assert session.state()["results"]["T-1"]["status"] == "PASS"
    assert [e["status"] for e in events.read(session.dir) if e["kind"] == "step"] == ["PASS"] * 4
    from oqa import view
    prior = view.case_records(session)[0]["attempts"][0]["steps"][0]["images"]
    livemod.shot(session, "T-1", 1, None, from_pre=True)
    images = view.case_records(session)[0]["attempts"][0]["steps"][0]["images"]
    assert len(images) == len(prior) + 1 and all(item in images for item in prior)
    assert any("_supp_" in item["annotated"] for item in images)
    assert session.state()["results"]["T-1"]["status"] == "PASS"


def test_a_failed_step_closes_the_case_and_marks_the_rest_as_not_run(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    livemod.step(session, "T-1", 1, "PASS", "the order list showed three rows", target=".o_list_view")
    livemod.step(session, "T-1", 2, "FAIL", "the New button was not on the page", target="#missing")    # no element: the whole screen is framed for a FAIL
    with pytest.raises(GateError, match="already failed"):
        livemod.step(session, "T-1", 3, "PASS", "the form showed the customer", target=".o_form_view")
    with pytest.raises(GateError, match="Say why"):
        livemod.end(session, "T-1", "FAIL", "")
    with pytest.raises(GateError, match="right control"):
        livemod.end(session, "T-1", "FAIL", "Không có nút New trên màn hình đơn hàng")          # a functional FAIL says which control was operated and how it was verified
    with pytest.raises(GateError, match="script or a missing precondition"):
        livemod.end(session, "T-1", "FAIL", "selector broke", category="automation", control="the New button by role")
    livemod.end(session, "T-1", "FAIL", "Không có nút New trên màn hình đơn hàng",
                control="getByRole button New, checked in the snapshot of the order list toolbar")
    assert session.state()["results"]["T-1"]["category"] == "application"
    statuses = [e["status"] for e in events.read(session.dir) if e["kind"] == "step"]
    assert statuses == ["PASS", "FAIL", "SKIPPED", "SKIPPED"]
    assert session.state()["results"]["T-1"]["status"] == "FAIL"


def test_a_worker_that_stopped_after_passing_steps_is_replaced_by_a_handover_that_keeps_them(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    assert livemod.passed_steps(session, "T-1") == []
    livemod.step(session, "T-1", 1, "PASS", "the order list showed three rows", target=".o_list_view")
    assert livemod.passed_steps(session, "T-1") == [1]
    livemod.handover(session, "T-1", "the worker timed out after step 1, continue from step 2")
    resumed = livemod.start(session, "T-1")[0]
    assert resumed["resumed"] and resumed["next_step"] == 2
    with pytest.raises(GateError, match=r"Steps recorded in this attempt: \[1\]"):
        livemod.step(session, "T-1", 4, "PASS", "skipped ahead", target=".o_form_view")


def test_a_fail_names_what_it_was_judged_against(live, pw):
    from oqa.store import read_json, write_json

    session, ws, store = live
    approve(session, ws, store)
    document = read_json(session.dir / "analysis.json")
    document["cases"][0]["steps"][1]["oracle"] = {"source": "bd", "ref": "BD-orders.txt 4.2", "quote": "The New button is on the order list"}
    write_json(session.dir / "analysis.json", document)
    livemod.start(session, "T-1")
    livemod.step(session, "T-1", 1, "PASS", "the order list showed three rows", target=".o_list_view")
    livemod.step(session, "T-1", 2, "FAIL", "the New button was not on the page", target="#missing")
    livemod.end(session, "T-1", "FAIL", "Không có nút New trên màn hình đơn hàng", control="getByRole button New, checked in the snapshot of the order list toolbar")
    result = session.state()["results"]["T-1"]
    assert result["basis"] == "BD: BD-orders.txt 4.2" and "[BD: BD-orders.txt 4.2]" in result["reason"]


def test_a_passed_case_is_replayed_in_a_new_session_and_stops_at_the_first_step_that_does_not_show(live, pw, tmp_path):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    for n in (1, 2, 3, 4):
        livemod.step(session, "T-1", n, "PASS", f"step {n} showed what was expected", target=".o_form_view")
    mine = tmp_path / "case.js"
    mine.write_text(SCRIPT, encoding="utf-8")
    livemod.end(session, "T-1", "PASS", "", script=str(mine))
    again, status, why = livemod.verify_replay(session, "T-1", headed=False)
    assert status == "PASS" and not why and again.id != session.id
    assert again.state()["replay_of"] == session.id and again.state()["results"]["T-1"]["status"] == "PASS"
    assert [e["status"] for e in events.read(again.dir) if e["kind"] == "step"] == ["PASS"] * 4
    mine.write_text(SCRIPT.replace("ZZ_AUTOTEST_order", "NOT-ON-PAGE", 1), encoding="utf-8")
    livemod.script_file(session, "T-1").write_text(mine.read_text(encoding="utf-8"), encoding="utf-8")
    broken, status, why = livemod.verify_replay(session, "T-1", headed=False)
    assert status == "FAIL" and "step 1" in why
    assert [e["status"] for e in events.read(broken.dir) if e["kind"] == "step"] == ["FAIL", "SKIPPED", "SKIPPED", "SKIPPED"]


def test_recording_rejects_uncommitted_odoo_search():
    script = """// step 1
// frame: td
// shows: ITEM
await page.goto('http://x/odoo/items');
await page.getByRole('searchbox', {name: 'Search...'}).fill('ITEM');
await page.getByRole('cell', {name: 'ITEM'}).click();
"""
    steps = [{"n": 1}]
    assert "search helper" in livemod.parse_script(script, steps, check_search=True)[1][0]
    assert not livemod.parse_script(script, steps)[1]
    committed = script.replace("await page.getByRole('cell'", (
        "await page.getByRole('link', {name: 'Search Code for: ITEM', exact: true}).click();\n"
        "await page.getByRole('cell'"
    ))
    assert not livemod.parse_script(committed, steps, check_search=True)[1]


def test_dashboard_style_replay_runs_saved_actions_in_headed_browser(live, tmp_path, monkeypatch):
    from oqa import replay_browser

    session, ws, store = live
    approve(session, ws, store)
    script = tmp_path / "case.js"
    script.write_text(SCRIPT, encoding="utf-8")
    livemod.keep_script(session, "T-1", str(script))
    before = session.state()
    history = events.read(session.dir)
    sessions = set(session.dir.parent.iterdir())
    calls = []

    def run(session, workspace, initial, blocks, timings, video_dir):
        calls.extend(blocks)
        assert video_dir is None
        return {"status": "PASS", "timing": {"total_s": 1}}

    monkeypatch.setattr(replay_browser, "run", run)
    monkeypatch.setattr(livemod.uuid, "uuid4", lambda: type("ReplayStamp", (), {"hex": "0123456789"})())
    result = livemod.replay(session, "T-1", action_delay_ms=0, step_delay_ms=0)
    assert result["session"] == session.id and result["status"] == "COMPLETED"
    assert replays.latest(session, "T-1")["headed"] is True
    assert calls[0]["label"].startswith("Bước 1/") and " — " in calls[0]["label"]
    assert calls[0]["code"][:2] == ["await page.goto('http://x/web');",
                            "await page.getByRole('button', { name: 'Save' }).click();"]
    assert "AUTO_TEST_create-an-order_r0123456789_order" in str(calls)
    assert all(len(block["check"]) == 2 for block in calls)
    assert session.state() == before and events.read(session.dir) == history
    assert replays.status(session, "T-1") == "PASS"
    assert set(session.dir.parent.iterdir()) == sessions


def test_blocked_and_pending_need_a_concrete_diagnosis_with_the_right_category(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    with pytest.raises(GateError, match="concrete diagnosis"):
        livemod.end(session, "T-1", "BLOCKED", "selector not found")
    with pytest.raises(GateError, match="category is automation"):
        livemod.diagnose(session, "T-1", "application", "the button is missing from the toolbar of the order list")
    with pytest.raises(GateError, match="already tried"):
        livemod.diagnose(session, "T-1", "automation", "the Save button locator matches two buttons in the dialog", tried="")
    livemod.diagnose(session, "T-1", "automation", "the Save button locator matches two buttons in the dialog",
                     tried="role button Save, then .o_form_button_save, then the dialog footer")
    from oqa import claims
    assert "DIAGNOSIS [automation]" in claims.notes(session)[-1]["text"]
    with pytest.raises(GateError, match="diagnosis says automation"):
        livemod.end(session, "T-1", "PENDING", "needs data", category="precondition")
    livemod.end(session, "T-1", "BLOCKED", "locator is ambiguous in the dialog")
    assert session.state()["results"]["T-1"] == {"status": "BLOCKED", "attempts": 1, "reason": "locator is ambiguous in the dialog", "category": "automation"}


def test_a_reopened_conclusion_stays_in_the_history(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    livemod.diagnose(session, "T-1", "precondition", "the case needs an archived ledger that does not exist yet", need="an archived ledger record")
    livemod.end(session, "T-1", "PENDING", "waiting for data")
    session.hint("T-1", "create the archived ledger first")
    history = session.state()["history"]["T-1"]
    assert history[0]["status"] == "PENDING" and history[0]["category"] == "precondition" and "archived ledger" in history[0]["superseded_by"]
    from oqa import view
    assert view.case_records(session)[0]["history"] == history


def test_handover_keeps_the_open_attempt_its_steps_and_the_recovery_budget(live, pw):
    from oqa import claims
    session, ws, store = live
    approve(session, ws, store)
    with session.update() as state:
        state["channel"] = "path"
    assert claims.claim(session, "worker-1", "T-1")[0] == "T-1"
    livemod.start(session, "T-1")
    livemod.step(session, "T-1", 1, "PASS", "the order list showed three rows", target=".o_list_view")
    attempts = dict(session.state()["attempts"])
    with pytest.raises(GateError, match="Say why"):
        livemod.handover(session, "T-1", "stuck")
    result, text = livemod.handover(session, "T-1", "worker-1 stalled after step 1, continue with step 2")
    assert result["steps_recorded"] == [1] and "kept" in text
    assert claims.claim(session, "worker-2", "T-1")[0] == "T-1"                       # a handed-over case is free for the next worker
    info, text = livemod.start(session, "T-1")
    assert info["resumed"] and info["next_step"] == 2 and "Do not redo" in text
    assert session.state()["attempts"] == attempts                                     # no attempt, no recovery budget spent
    livemod.step(session, "T-1", 2, "PASS", "the order form showed the customer", target=".o_list_view")
    assert [e["n"] for e in events.read(session.dir) if e["kind"] == "step"] == [1, 2]


def test_a_pass_checks_each_value_in_the_result_target():
    from playwright.sync_api import Error

    from oqa.browser import launch

    assert livemod.specific_values("Pager shows 1-1 / 1; date 2026/01/01; see ERR_092") == ["1-1", "1", "2026/01/01"]
    values = livemod.specific_values("Hiển thị số lượng 5 và tổng tiền 100")
    command = livemod.outcome_check("Orders", values, 1000, "#result")
    execute = """source => new (Object.getPrototypeOf(async function() {}).constructor)('page', source)({
        locator: selector => ({evaluate: (fn, arg) => fn(document.querySelector(selector), arg)})
    })"""
    with launch(headless=True) as new_page:
        _, page = new_page()
        page.set_content("<h1>Orders 5 100</h1><section id='result'>Orders 1000</section>")
        with pytest.raises(Error, match="5, 100"):
            page.evaluate(execute, command)
        page.set_content("<section id='result'>Orders 100 <input value='5'></section>")
        page.evaluate(execute, command)
        page.set_content("<section id='result'>Orders 100 <input value='5' hidden></section>")
        with pytest.raises(Error, match="missing"):
            page.evaluate(execute, command)


def test_the_saved_step_never_drops_recorded_helper_actions(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    goto = "await page.goto('http://x/web');"
    capture = livemod.capture_id_code("record")
    for code in (goto, capture):
        livemod.act(session, "T-1", "run-code", [], code, record=True)
    with pytest.raises(GateError, match="omitted or reordered"):
        livemod.step(session, "T-1", 1, "PASS", "The order is visible", "body", goto, "Orders")
    livemod.step(session, "T-1", 1, "PASS", "The order is visible", "body", shows="Orders")
    recorded = next(e for e in events.read(session.dir) if e["kind"] == "step")
    assert recorded["verified_script"][-2:] == [goto, capture]
    assert "// check-target: yes" in recorded["verified_script"]


def test_literal_check_compares_text_after_normalising_whitespace():
    code = livemod.literal_check("1-1 / 1", 3000)
    assert "replace(/[\\s\\u00a0\\u202f]+/g" in code and "includes(norm(text))" in code
def test_evidence_caption_uses_its_language_without_changing_the_observation(live, pw, monkeypatch):
    from oqa import evidence, workspace

    session, ws, store = live
    ws = workspace.save({**ws, "evidence_language": "ja", "replay_caption_language": "en"}, store)
    approve(session, ws, store)
    livemod.start(session, "T-1")
    labels = []
    annotate = evidence.annotate

    def capture(raw, boxes, label, out, ok=True):
        labels.append(label)
        return annotate(raw, boxes, label, out, ok=ok)

    monkeypatch.setattr(evidence, "annotate", capture)
    observation = "Danh sách hiển thị ba dòng."
    livemod.step(session, "T-1", 1, "PASS", observation, target=".o_list_view")
    step = next(e for e in events.read(session.dir) if e["kind"] == "step")
    assert labels == ["手順 1/4 · 合格"]
    assert step["actual"] == observation and step["images"][0]["caption"] == labels[0]


def test_an_action_that_types_a_fixed_record_name_is_refused(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    for fixed in ("await page.getByRole('textbox').fill('ZZ_AUTOTEST_T09_S');", "await page.getByRole('textbox').fill('AUTO_TEST_site');"):
        with pytest.raises(GateError, match="fixed record name"):
            livemod.act(session, "T-1", "run-code", [], fixed, record=True)
    with pytest.raises(GateError, match="fixed record name"):
        livemod.act(session, "T-1", "fill", ["e5", "ZZ_AUTOTEST_site"])
    from oqa import record_names

    own = record_names.fixture_prefix(session, "T-1", ws["test_prefix"]) + "site"
    assert record_names.foreign(session, "T-1", f"await page.getByRole('textbox').fill('{own}');", ws["test_prefix"]) == []


def test_a_record_id_lives_in_a_run_variable_not_in_the_script(live, pw):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    for bad in (r"await page.waitForURL(/action-90\/308/);", "await page.goto('http://x/odoo/action-91/564');", "await page.goto('http://x/odoo/oqa.harness.request/12');",
                "await page.goto('http://x/web#id=12&action=3');"):
        with pytest.raises(GateError, match="capture_id"):
            livemod.act(session, "T-1", "run-code", [], bad, record=True)
    assert not livemod.RECORD_ID_URL.search("await page.goto('http://x/odoo/action-91');")
    assert not livemod.RECORD_ID_URL.search("await page.getByRole('cell', {name: 'REQ 308'}).click();")
    capture = livemod.capture_id_code("rec")
    assert "sessionStorage.setItem('oqa:var:' + name" in capture and capture.endswith('"rec");') and "308" not in capture
    used = livemod.expand_vars("await page.waitForURL(new RegExp('action-90/' + $OQA_ID(rec)));")
    assert "$OQA_ID" not in used and "sessionStorage.getItem('oqa:var:' + k)" in used and not livemod.RECORD_ID_URL.search(used)
    with pytest.raises(GateError, match="variable name"):
        livemod.capture_id_code("bad name")
