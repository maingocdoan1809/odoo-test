"""The dashboard tab: the server knows whether one is open, and the hooks open one when none is."""
import subprocess

from oqa import autostart, dashboard_server


def test_a_tab_is_seen_only_while_the_page_keeps_asking(monkeypatch):
    assert not autostart.viewer_seen()
    autostart.mark_viewer()
    assert autostart.viewer_seen()
    now = autostart.time.time()
    monkeypatch.setattr(autostart.time, "time", lambda: now + autostart.VIEWER_FRESH + 5)
    assert not autostart.viewer_seen()


def test_the_page_polling_marks_the_viewer(session):
    app = dashboard_server.Dashboard((session.ws, session.id), timeout=60)
    assert not autostart.viewer_seen()
    app.handle("GET", "/api/sessions", {}, {})
    assert autostart.viewer_seen()


def test_a_tab_is_opened_once_when_none_is_open(monkeypatch):
    monkeypatch.delenv("OQA_NO_OPEN", raising=False)
    opened = []
    monkeypatch.setattr(autostart, "dashboard_running", lambda: "http://127.0.0.1:5/?t=x")
    monkeypatch.setattr(autostart, "open_url", lambda url: opened.append(url) or True)
    assert autostart.ensure_viewer("w", "s1") is True
    assert opened == ["http://127.0.0.1:5/?t=x#w/s1"]
    assert autostart.ensure_viewer("w", "s1") is False                # the cooldown: a tab just opened needs a moment
    assert len(opened) == 1


def test_no_tab_is_opened_when_one_is_showing_or_no_dashboard_runs(monkeypatch):
    monkeypatch.delenv("OQA_NO_OPEN", raising=False)
    opened = []
    monkeypatch.setattr(autostart, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(autostart, "dashboard_running", lambda: None)
    assert autostart.ensure_viewer("w", "s1") is False
    monkeypatch.setattr(autostart, "dashboard_running", lambda: "http://127.0.0.1:5/?t=x")
    autostart.mark_viewer()
    assert autostart.ensure_viewer("w", "s1") is False and opened == []


def test_on_wsl_the_windows_side_opens_the_page(monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "_wsl", lambda: True)
    monkeypatch.setattr("shutil.which", lambda exe: "/mnt/c/windows/system32/cmd.exe" if exe == "cmd.exe" else None)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    assert autostart.open_url("http://127.0.0.1:5/?t=x#w/s1") is True
    assert calls == [["cmd.exe", "/c", "start", "", "http://127.0.0.1:5/?t=x#w/s1"]]
