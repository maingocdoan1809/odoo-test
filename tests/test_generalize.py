"""Regenerating a saved script generically: kept only when fresh replays pass, otherwise the original comes back."""
import pytest

from oqa import events, generalize, live
from oqa.state import GateError
from test_hooks import executing

STEP = "// step {n}\n// frame: .o_form_view\n// shows: Saved\nawait page.goto('http://x/web');\nawait page.waitForURL(/action-90\\/308/);\n"
ORIGINAL = "\n".join(STEP.format(n=n) for n in (1, 2, 3))
GENERIC = "\n".join(
    "// step {n}\n// frame: .o_form_view\n// shows: Saved\nawait page.goto('http://x/web');\n$OQA_CAPTURE(rec)\nawait page.waitForURL(new RegExp('action-90/' + $OQA_ID(rec)));\n".format(n=n)
    for n in (1, 2, 3))


@pytest.fixture
def saved(session, tmp_path):
    executing(session, tmp_path)
    path = live.script_file(session, "ORD-001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ORIGINAL, encoding="utf-8")
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "PASS", "attempts": 1}
    return path


def answers(*texts):
    queue, prompts = list(texts), []

    def ask(executable, model, prompt):
        prompts.append(prompt)
        return queue.pop(0)
    return ask, prompts


def test_a_generic_script_that_replays_twice_replaces_the_original(session, saved, monkeypatch):
    runs = []
    monkeypatch.setattr(live, "replay", lambda *a, **k: runs.append(k) or {"replay": "PASS"})
    ask, prompts = answers("```javascript\n" + GENERIC + "```")
    result = generalize.regenerate(session, "ORD-001", ask=ask)
    text = saved.read_text(encoding="utf-8")
    assert result["status"] == "PASS" and result["attempts"] == 1 and len(runs) == 2 and all(r["headed"] is False for r in runs)
    assert "$OQA_" not in text and "sessionStorage.setItem('oqa:var:' + name" in text and "```" not in text
    assert saved.with_name(saved.stem + ".original.js").read_text(encoding="utf-8") == ORIGINAL
    assert "never write the id of a record" in prompts[0].lower() and "AUTO_TEST_" in prompts[0] and "action-90\\/308" in prompts[0]
    assert [e["status"] for e in events.read(session.dir) if e["kind"] == "script_generalized"] == ["RUNNING", "PASS"]


def test_a_candidate_with_a_delay_or_an_id_is_refused_before_any_browser_is_used(session, saved, monkeypatch):
    monkeypatch.setattr(live, "replay", lambda *a, **k: {"replay": "PASS"})
    bad = GENERIC.replace("await page.goto('http://x/web');", "await page.waitForTimeout(3000);", 1)
    ask, prompts = answers(bad, ORIGINAL, GENERIC)
    result = generalize.regenerate(session, "ORD-001", ask=ask)
    assert result["attempts"] == 3
    assert "fixed delay" in prompts[1] and "record id in an address" in prompts[2]       # each refusal goes back to the model


def test_when_no_candidate_replays_the_original_script_comes_back(session, saved, monkeypatch):
    def failing(*a, **k):
        raise GateError("Step 2 in X.spec.js: locator.click: Timeout 30000ms exceeded.")
    monkeypatch.setattr(live, "replay", failing)
    ask, prompts = answers(GENERIC, GENERIC)
    with pytest.raises(GateError, match="original is restored"):
        generalize.regenerate(session, "ORD-001", attempts=2, ask=ask)
    assert saved.read_text(encoding="utf-8") == ORIGINAL
    assert "replay 1 of 2 failed" in prompts[1] and "Timeout 30000ms" in prompts[1]
    assert events.read(session.dir)[-1]["status"] == "FAIL"


def test_a_case_that_did_not_pass_cannot_be_rewritten(session, saved):
    with session.update() as state:
        state["results"]["ORD-001"] = {"status": "FAIL", "attempts": 1}
    ask, _ = answers("x")
    with pytest.raises(GateError, match="no PASS result"):
        generalize.regenerate(session, "ORD-001", ask=ask)


def test_a_second_rewrite_of_the_same_case_is_refused_while_one_runs(session, saved):
    events.append(session.dir, "script_generalized", case="ORD-001", status="RUNNING", attempt=1, note="attempt 1 of 3")
    ask, _ = answers("x")
    with pytest.raises(GateError, match="already running"):
        generalize.regenerate(session, "ORD-001", ask=ask)


def test_a_rewrite_that_stopped_reporting_is_stale_and_can_start_again(session, saved, monkeypatch):
    events.append(session.dir, "script_generalized", case="ORD-001", status="RUNNING", attempt=1, note="attempt 1 of 3")
    later = generalize.time.time() + generalize.STALE_AFTER + 60
    with monkeypatch.context() as clock:
        clock.setattr(generalize.time, "time", lambda: later)
        assert generalize.latest(session, "ORD-001")["status"] == "STALE" and not generalize.running(session, "ORD-001")
        monkeypatch.setattr(live, "replay", lambda *a, **k: {"replay": "PASS"})
        ask, _ = answers(GENERIC)
        assert generalize.regenerate(session, "ORD-001", ask=ask)["status"] == "PASS"


def test_an_unexpected_error_still_restores_the_original(session, saved, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("the browser crashed")
    monkeypatch.setattr(live, "replay", broken)
    ask, _ = answers(GENERIC, GENERIC, GENERIC)
    with pytest.raises(GateError, match="original is restored.*browser crashed"):
        generalize.regenerate(session, "ORD-001", ask=ask)
    assert saved.read_text(encoding="utf-8") == ORIGINAL
    assert events.read(session.dir)[-1]["status"] == "FAIL"


def test_a_case_without_a_saved_script_cannot_be_rewritten(session, tmp_path):
    executing(session, tmp_path)
    ask, _ = answers("x")
    with pytest.raises(GateError, match="no saved script"):
        generalize.regenerate(session, "ORD-001", ask=ask)


def test_without_verification_only_the_static_checks_stand_and_it_says_so(session, saved, monkeypatch):
    monkeypatch.setattr(live, "replay", lambda *a, **k: pytest.fail("nothing may be replayed"))
    ask, _ = answers(GENERIC)
    result = generalize.regenerate(session, "ORD-001", replays=0, ask=ask)
    assert result["status"] == "UNVERIFIED" and "sessionStorage" in saved.read_text(encoding="utf-8")
    assert events.read(session.dir)[-1]["status"] == "UNVERIFIED"


def test_a_rewrite_that_repeats_an_upload_or_a_save_is_refused(session, saved, monkeypatch):
    monkeypatch.setattr(live, "replay", lambda *a, **k: {"replay": "PASS"})
    original = ORIGINAL.replace("await page.goto('http://x/web');", "await page.getByRole('button', {name: 'Upload Data File'}).click(); chooser.setFiles(x);", 1)
    saved.write_text(original, encoding="utf-8")
    twice = GENERIC.replace("await page.goto('http://x/web');", "chooser.setFiles(x);")        # every step uploads: more than the original does
    ask, prompts = answers(twice, GENERIC)
    generalize.regenerate(session, "ORD-001", ask=ask)
    assert "repeats an action that changes data" in prompts[1] and "NEVER repeat" in prompts[0]


def test_node_modules_do_not_exist_in_the_browser_sandbox(session, saved, monkeypatch):
    monkeypatch.setattr(live, "replay", lambda *a, **k: {"replay": "PASS"})
    bad = GENERIC.replace("await page.goto('http://x/web');", "const fs = require('fs'); await page.goto('http://x/web');", 1)
    ask, prompts = answers(bad, GENERIC)
    generalize.regenerate(session, "ORD-001", ask=ask)
    assert "require/import does not exist" in prompts[1] and "createReadStream" in prompts[0]
