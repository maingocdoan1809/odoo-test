from oqa import browser, live, replay_browser, replays
from test_dashboard import Up
from test_evidence_replay import _saved_script, approved


def test_closing_a_visible_replay_stops_the_batch_and_keeps_live_result(session, ws, monkeypatch):
    approved(session, ws)
    _saved_script(session)
    _saved_script(session, "ORD-002")
    opens = []

    def cancel(session, workspace, initial, blocks, timings, video_dir):
        opens.append(blocks)
        return {"status": "CANCELLED", "reason": "window closed", "failed_step": 1, "timing": {}}

    monkeypatch.setattr(replay_browser, "run", cancel)
    before = session.state()
    rows = live.replay_all(session, headed=True)
    assert rows == [{"case": "ORD-001", "replay": "CANCELLED", "reason": "window closed"}]
    assert len(opens) == 1 and replays.latest(session, "ORD-001")["failed_step"] == 1
    assert replays.status(session, "ORD-002") == "NOT RUN"
    assert session.state() == before


def test_scripts_tab_expands_the_steps_of_the_saved_script(session, ws):
    approved(session, ws)
    item = _saved_script(session)
    server = Up((session.ws, session.id))
    try:
        status, data = server.call(f"/api/script?view=steps&ws=demo&id={session.id}&case=ORD-001")
        assert status == 200 and len(data["steps"]) == len(item["steps"]) and not data["problems"]
        with browser.launch() as new:
            _, page = new()
            page.goto(f"{server.base}/?t={server.app.token}")
            page.get_by_role("tab", name="Scripts").click()
            button = page.get_by_role("button", name="Các bước script ORD-001")
            button.click()
            panel = page.locator(".script-steps")
            panel.get_by_text(item["steps"][0]["action"], exact=True).first.wait_for()
            assert panel.locator("li").count() == len(item["steps"])
            panel.locator("summary").first.click()
            assert "await page.goto(" in panel.locator("code").first.inner_text()
            assert button.get_attribute("aria-expanded") == "true"
            button.click()
            assert not panel.count()
    finally:
        server.app.quit = True
