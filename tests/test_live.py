"""EXECUTE with playwright-cli: a fake `playwright-cli` stands in for the browser; oqa must frame the element itself and refuse a PASS that has no framed element."""
import os
import stat
import sys

import pytest

pytest.importorskip("playwright")

from odoofake import approve, fake, live  # noqa: E402,F401  (fixtures and helpers)
from oqa import events, live as livemod  # noqa: E402
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
    assert 'new URL("/odoo/action-92/new", page.url()).href' in commands[0][2]


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
    livemod.end(session, "T-1", "FAIL", "Không có nút New trên màn hình đơn hàng")
    statuses = [e["status"] for e in events.read(session.dir) if e["kind"] == "step"]
    assert statuses == ["PASS", "FAIL", "SKIPPED", "SKIPPED"]
    assert session.state()["results"]["T-1"]["status"] == "FAIL"


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
    session, ws, store = live
    approve(session, ws, store)
    script = tmp_path / "case.js"
    script.write_text(SCRIPT, encoding="utf-8")
    livemod.keep_script(session, "T-1", str(script))
    before = session.state()
    history = events.read(session.dir)
    sessions = set(session.dir.parent.iterdir())
    calls = []
    monkeypatch.setattr(livemod, "open_browser", lambda *a, **kw: calls.append(kw) or "Admin")
    monkeypatch.setattr(livemod, "run_actions", lambda browser, code: calls.append(list(code)))
    monkeypatch.setattr(livemod.uuid, "uuid4", lambda: type("ReplayStamp", (), {"hex": "0123456789"})())
    result = livemod.replay(session, "T-1")
    assert result["session"] == session.id and result["status"] == "COMPLETED"
    assert calls[0]["headed"] is True and calls[0]["browser_id"].startswith("replay-")
    assert calls[1][:2] == ["await page.goto('http://x/web');",
                            "await page.getByRole('button', { name: 'Save' }).click();"]
    assert "ZZ_AUTOTEST_r0123456789_order" in str(calls[1:])
    assert len(calls) == 5
    assert session.state() == before and events.read(session.dir) == history
    assert set(session.dir.parent.iterdir()) == sessions
