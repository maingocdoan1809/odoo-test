"""The whole host-agent flow through the real command line: what a sub-agent of Claude Code or Copilot CLI runs, in the order it runs it.
oqa --plan gives the brief, the agent works the case (a fake playwright-cli stands in for the browser), --settle replays the script, a generic script is written by an agent and
submitted with --candidate, the dashboard's requests reach the coordinator through `oqa wait`. No model and no CLI is started by oqa anywhere in it."""
import re

import pytest

from odoofake import approve, fake, live  # noqa: F401  (fixtures and helpers)
from oqa import cli, events, fixtures, live as livemod
from test_live import SCRIPT, pw  # noqa: F401


def oqa(session, *argv, capsys):
    code = cli.main([*argv, "--ws", session.ws, "--session", session.id])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_case_goes_through_plan_agent_settle_and_a_generic_rewrite(live, pw, tmp_path, capsys, monkeypatch):
    session, ws, store = live
    approve(session, ws, store)
    import shutil
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: pytest.fail("oqa looked for the Claude CLI") if name == "claude" else which(name, *a, **k))

    code, text, _ = oqa(session, "dispatch", "--plan", "--limit", "1", capsys=capsys)
    agent = re.search(r"worker (host-\S+) · case T-1", text).group(1)
    assert code == 0 and f"oqa live start T-1" in text and "oqa tool T-1 NAME" in text and "--allowedTools" not in text

    code, text, _ = oqa(session, "tool", "T-1", "context", "--agent", agent, capsys=capsys)           # a tool with no `oqa live` command, as the agent calls it
    assert code == 0 and '"id": "T-1"' in text
    code, _, err = oqa(session, "tool", "T-1", "context", "--agent", "someone-else", capsys=capsys)
    assert code == 2 and "does not hold" in err

    livemod.start(session, "T-1")                                                                 # the agent works the case
    for n in (1, 2, 3, 4):
        livemod.step(session, "T-1", n, "PASS", f"step {n} showed what was expected", target=".o_form_view")
    mine = tmp_path / "case.js"
    mine.write_text(SCRIPT, encoding="utf-8")
    livemod.end(session, "T-1", "PASS", "", script=str(mine))

    code, text, _ = oqa(session, "dispatch", "--settle", "T-1", capsys=capsys)
    assert code == 0 and "PASS and its saved script replays" in text

    code, text, _ = oqa(session, "live", "regenerate", "T-1", "--brief", capsys=capsys)
    candidate = tmp_path / "generic.js"
    assert code == 0 and "oqa live regenerate T-1 --candidate" in text and "never write the id of a record" in text.lower()
    candidate.write_text(SCRIPT.replace("ZZ_AUTOTEST_order", fixtures.run_key(session, "T-1", "order")), encoding="utf-8")  # what the sub-agent wrote: a name that is this run's own
    code, text, _ = oqa(session, "live", "regenerate", "T-1", "--candidate", str(candidate), capsys=capsys)
    assert code == 0 and "generic script PASS" in text
    assert [e["status"] for e in events.read(session.dir) if e["kind"] == "script_generalized"] == ["RUNNING", "PASS"]

    code, _, err = oqa(session, "dispatch", capsys=capsys)                                          # there is no way to make oqa start workers itself
    assert code == 2 and "starts no agent of its own" in err


def test_a_rewrite_the_agent_gets_wrong_comes_back_as_a_retry_with_the_reason(live, pw, tmp_path, capsys):
    session, ws, store = live
    approve(session, ws, store)
    livemod.start(session, "T-1")
    for n in (1, 2, 3, 4):
        livemod.step(session, "T-1", n, "PASS", f"step {n} showed what was expected", target=".o_form_view")
    mine = tmp_path / "case.js"
    mine.write_text(SCRIPT, encoding="utf-8")
    livemod.end(session, "T-1", "PASS", "", script=str(mine))
    oqa(session, "live", "regenerate", "T-1", "--brief", capsys=capsys)
    candidate = tmp_path / "bad.js"
    candidate.write_text(SCRIPT.replace("await page.goto('http://x/web');", "await page.waitForTimeout(3000);", 1), encoding="utf-8")
    code, text, _ = oqa(session, "live", "regenerate", "T-1", "--candidate", str(candidate), capsys=capsys)
    assert code == 0 and text.startswith("RETRY T-1") and "fixed delay" in text and "oqa live regenerate T-1 --candidate" in text
    assert livemod.script_file(session, "T-1").read_text(encoding="utf-8") == SCRIPT              # the saved script is untouched until a candidate passes
    code, text, _ = oqa(session, "live", "regenerate", "T-1", "--cancel", capsys=capsys)
    assert code == 0 and "original" in text


def test_what_the_user_presses_on_the_dashboard_reaches_the_coordinator_through_wait(live, pw, capsys):
    session, ws, store = live
    approve(session, ws, store)
    events.append(session.dir, "dispatch_requested", case="T-1", request="dispatch", by="dashboard")
    events.append(session.dir, "regenerate_requested", case="all", by="dashboard")
    code, text, _ = oqa(session, "wait", "--timeout", "3", capsys=capsys)
    assert code == 0 and "oqa dispatch --plan" in text and "oqa live regenerate all --brief" in text and "sub-agent" in text
