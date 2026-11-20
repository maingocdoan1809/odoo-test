"""'Not AI slop' as things that can be measured: contrast, no horizontal scrolling at phone / tablet / desktop widths, nothing decorative, nothing remote,
every control reachable and named, no console errors."""
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from conftest import filled  # noqa: E402
from oqa import analysis, browser, dashboard_server, workspace  # noqa: E402
from oqa.state import Session  # noqa: E402
from oqa.store import write_json  # noqa: E402

HTML = (Path(__file__).resolve().parents[1] / "oqa" / "web" / "dashboard.html").read_text(encoding="utf-8")
INIT = (Path(__file__).resolve().parents[1] / "oqa" / "web" / "init.html").read_text(encoding="utf-8")


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return sum(w * (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4) for w, c in zip((0.2126, 0.7152, 0.0722), channels))


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def palette(page_html):
    light = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", page_html.split("@media (prefers-color-scheme: dark)")[0]))
    dark_block = page_html.split("@media (prefers-color-scheme: dark)")[1].split("}")[0]
    return light, {**light, **dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", dark_block))}


@pytest.mark.parametrize("page_html", [HTML, INIT], ids=["dashboard", "init form"])
def test_text_and_status_colours_are_readable_in_light_and_dark(page_html):
    for theme in palette(page_html):
        for ink, ground, minimum in (("ink", "surface", 7), ("ink", "bg", 7), ("muted", "surface", 4.5), ("muted", "bg", 4.5)):
            if ink in theme and ground in theme:
                assert contrast(theme[ink], theme[ground]) >= minimum, (ink, ground, theme[ink], theme[ground])
    if page_html is HTML:
        for theme in palette(HTML):
            for status in ("pass", "fail", "pending", "blocked", "running", "repairing"):
                assert contrast(theme[status], theme["surface"]) >= 4.5, (status, theme[status])
            assert contrast(theme["accent-ink"], theme["accent"]) >= 3.0      # the brand coral with a white label, as the design specifies (about 3.3:1: fine for bold UI text, not for paragraphs)


@pytest.mark.parametrize("page_html", [HTML, INIT], ids=["dashboard", "init form"])
def test_nothing_decorative_and_nothing_remote(page_html):
    page_html = page_html.replace('<link rel="icon" href="data:,">', "")
    assert not re.search(r"gradient\(|backdrop-filter|text-shadow|@import|<link[^>]+href=|<script[^>]+src=|(?:src|href)=[\"']https?://|url\(\s*[\"']?https?://", page_html), "decorative or remote resource"
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", page_html.replace("↗", "").replace("←", "").replace("→", "").replace("✕", "")), "emoji"
    assert page_html.count("box-shadow") <= 3 and "border-radius:50%" in page_html or "border-radius" in page_html


@pytest.fixture
def served(session, tmp_path):
    config = workspace.load(session.ws)
    data = filled(session.cases(), config)
    write_json(session.dir / "analysis.json", data)
    session.analysis_submitted()
    app = dashboard_server.Dashboard((session.ws, session.id), timeout=60)
    import queue
    import threading
    urls = queue.Queue()
    threading.Thread(target=lambda: app.serve(urls.put, open_browser=False, forever=True), daemon=True).start()
    return urls.get(timeout=5)


@pytest.mark.parametrize("width", [320, 375, 800, 1100, 1280, 1440, 1920])
def test_no_horizontal_scroll_every_control_is_named_and_the_console_is_clean(served, width):
    with browser.launch(True) as new_page:
        _, page = new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(served)
        page.wait_for_selector("tr.case")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "the page scrolls sideways"
        unnamed = page.evaluate("""() => [...document.querySelectorAll('button, select, textarea, input')].filter(e => !(e.innerText || e.getAttribute('aria-label') || e.title || e.placeholder || '').trim()).map(e => e.outerHTML.slice(0, 80))""")
        assert not unnamed, unnamed
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement !== document.body"), "nothing can be focused with the keyboard"
        assert not errors, errors


def test_long_case_content_stays_readable_and_table_scroll_survives_redraw(served):
    """A realistic long list must scroll inside its panel, with all columns reachable."""
    with browser.launch(True) as new_page:
        _, page = new_page()
        page.goto(served)
        page.wait_for_selector("tr.case")
        data = page.evaluate("S.view")
        import copy
        original = data["cases"][0]
        data["cases"] = [dict(copy.deepcopy(original), id=f"ORD-{i:03}") for i in range(40)]
        data["cases"][0].update(
            title="Kiểm tra toàn bộ nội dung testcase dài với nhiều điều kiện và kết quả mong đợi. " * 6,
            profile="Profile_" + "Administrator" * 8,
        )
        data["total"] = len(data["cases"])
        page.route("**/api/session?**", lambda route: route.fulfill(json=data))
        page.evaluate("v => { S.view = v; S.sig = JSON.stringify(v); S.caseId = v.cases[0].id; renderMain(); }", data)
        for width in (320, 375, 800, 1280, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert page.eval_on_selector("#main", "e => e.scrollWidth <= e.clientWidth + 1"), width
            assert page.eval_on_selector("tr.case .title", "e => e.scrollHeight <= e.clientHeight + 1"), "title is clipped"
            assert page.eval_on_selector("tr.case td:nth-child(3)", "e => e.scrollWidth <= e.clientWidth + 1"), "profile is clipped"
            assert page.eval_on_selector(".case-table-scroll", "e => e.scrollHeight > e.clientHeight"), "long list cannot scroll"
            panel = page.locator(".case-list").bounding_box()
            detail = page.locator("#detail").bounding_box()
            if width < 1600:
                assert detail["y"] >= panel["y"] + panel["height"] - 1, "detail squeezes the table"
            else:
                assert detail["x"] >= panel["x"] + panel["width"], "wide layout wastes available space"

        page.set_viewport_size({"width": 375, "height": 667})
        assert page.eval_on_selector(".case-table-scroll", "e => e.scrollWidth > e.clientWidth")
        page.eval_on_selector(".case-table-scroll", "e => { e.scrollLeft = e.scrollWidth; e.scrollTop = 160; }")
        before = page.eval_on_selector(".case-table-scroll", "e => [e.scrollLeft, e.scrollTop]")
        page.evaluate("renderMain()")
        assert page.eval_on_selector(".case-table-scroll", "e => [e.scrollLeft, e.scrollTop]") == before
        assert page.eval_on_selector(".case-table-scroll", """e => {
            const panel = e.getBoundingClientRect(), last = e.querySelector('th:last-child').getBoundingClientRect();
            return last.left >= panel.left && last.right <= panel.right + 1 && Math.abs(last.top - panel.top) <= 1;
        }"""), "the result column or sticky header is unreachable"
        for collapsed in (True, False):
            page.evaluate("value => toggleRail(value)", collapsed)
            assert page.eval_on_selector("#main", "e => e.clientHeight > 200 && e.scrollHeight > e.clientHeight")
            assert page.eval_on_selector("#main", "e => e.getBoundingClientRect().bottom <= innerHeight + 1")


def test_the_page_does_not_move_while_it_is_being_read(session, served):
    """A live page must not pull itself from under the reader: no redraw when nothing changed, scroll kept when something did, and nothing redrawn while text is selected."""
    with browser.launch(True) as new_page:
        _, page = new_page()
        page.set_viewport_size({"width": 1000, "height": 420})
        page.goto(served)
        page.wait_for_selector("tr.case")
        page.evaluate("document.querySelector('tr.case').dataset.mark = 'x'; document.getElementById('main').scrollTop = 150")
        assert page.evaluate("document.getElementById('main').scrollTop") == 150, "the page is too short to scroll: the test proves nothing"
        page.wait_for_timeout(4000)                                                  # two or more polls with identical data
        assert page.evaluate("document.querySelector('tr.case').dataset.mark") == "x", "the page was redrawn although nothing changed"
        assert page.evaluate("document.getElementById('main').scrollTop") == 150

        page.evaluate("const r = document.createRange(); r.selectNodeContents(document.querySelector('tr.case .title')); const s = getSelection(); s.removeAllRanges(); s.addRange(r)")
        session.ask("Which customer?")                                              # the data changes while the person has text selected
        page.wait_for_timeout(3500)
        assert page.evaluate("document.querySelector('tr.case').dataset.mark") == "x", "the page was redrawn under a selection"
        page.evaluate("getSelection().removeAllRanges()")
        page.wait_for_selector(".attn-panel", timeout=5000)                         # once they let go, the change appears
        assert page.evaluate("document.getElementById('main').scrollTop") == 150, "the scroll position was lost by the redraw"


VIETNAMESE = "ồấẫậẳằễệổỗộờởỡợừửữựỳỵỷỹặẵẩầ"          # stacked diacritics: the letters a font without full Vietnamese support draws from another font


def test_replay_speed_controls_persist_and_submit_the_selected_delays(served):
    with browser.launch(True) as new_page:
        _, page = new_page()
        page.goto(served)
        page.wait_for_selector("tr.case")
        data = page.evaluate("S.view")
        data["cases"][0].update(status="PASS", can_replay=True)
        page.route("**/api/session?**", lambda route: route.fulfill(json=data))
        page.evaluate("v => { S.view = v; S.caseId = v.cases[0].id; renderMain(); }", data)
        action = page.get_by_label("Nghỉ sau thao tác (ms)", exact=True)
        step = page.get_by_label("Nghỉ sau bước (ms)", exact=True)
        action.fill("1200")
        step.fill("2400")
        step.press("Tab")
        page.evaluate("renderMain()")
        assert action.input_value() == "1200" and step.input_value() == "2400"
        assert page.evaluate("JSON.parse(localStorage.getItem('oqaReplayTiming')).step_delay_ms") == 2400
        for width in (320, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.route("**/api/replay?**", lambda route: route.fulfill(json={"ok": True, "message": "Replay started"}))
        with page.expect_request("**/api/replay?**") as request:
            page.get_by_role("button", name="Chạy lại testcase", exact=True).click()
        assert request.value.post_data_json["action_delay_ms"] == 1200
        assert request.value.post_data_json["step_delay_ms"] == 2400


def test_every_font_stack_really_draws_vietnamese(served):
    """Vietnamese needs a font that has its stacked-diacritic letters. Ask the browser which fonts it used for each stack: one font must draw (nearly) all of them."""
    with browser.launch(True) as new_page:
        context, page = new_page()
        page.goto(served)
        page.wait_for_selector("tr.case")
        page.evaluate("""(text) => { for (const name of ['display', 'sans', 'mono']) { const e = document.createElement('div'); e.id = 'vi-' + name; e.textContent = text;
            e.style.cssText = 'font-family:var(--' + name + ');font-size:24px;position:fixed;left:0;top:0'; document.body.append(e); } }""", VIETNAMESE)
        page.evaluate("async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); }")
        client = context.new_cdp_session(page)
        client.send("DOM.enable")
        client.send("CSS.enable")
        root = client.send("DOM.getDocument")["root"]["nodeId"]
        for name in ("display", "sans", "mono"):
            node = client.send("DOM.querySelector", {"nodeId": root, "selector": f"#vi-{name}"})["nodeId"]
            fonts = client.send("CSS.getPlatformFontsForNode", {"nodeId": node})["fonts"]
            total = sum(f["glyphCount"] for f in fonts)
            best = max(fonts, key=lambda f: f["glyphCount"])
            assert total and best["glyphCount"] / total >= 0.95, f"{name}: {best['familyName']} draws only {best['glyphCount']} of {total} Vietnamese letters; fonts used: {fonts}"
            assert best["familyName"] != "Georgia", f"{name} falls on Georgia, which lacks several Vietnamese letters"


def test_the_ai_repair_notes_are_shown_under_the_case_analysis(session, tmp_path):
    import queue
    import threading

    from oqa import events
    from test_flow import executing
    executing(session, tmp_path)
    session.begin_attempt("ORD-001")
    events.append(session.dir, "attempt_finished", case="ORD-001", attempt=1, status="FAIL", reason="TimeoutError: waiting for locator", failed_step=1, script_sha="a")
    session.repair("ORD-001", "Save is inside the gear menu in Odoo 19", "the probe lists no Save button", "open the gear menu first", script_sha="b", timeout_s=20)
    app = dashboard_server.Dashboard((session.ws, session.id), timeout=60)
    urls = queue.Queue()
    threading.Thread(target=lambda: app.serve(urls.put, open_browser=False, forever=True), daemon=True).start()
    with browser.launch(True) as new_page:
        _, page = new_page()
        page.goto(urls.get(timeout=5))
        page.wait_for_selector("tr.case")
        page.click("tr.case[data-id='ORD-001']")
        page.wait_for_selector(".ai-notes .note")
        text = page.inner_text(".ai-notes .note")
        assert "sau lần 1" in text.lower() and "Save is inside the gear menu" in text and "the probe lists no Save button" in text and "20 giây" in text
        assert "đã sửa 1 lần" in page.inner_text("#detail summary")
