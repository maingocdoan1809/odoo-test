"""The dashboard's Knowledge tab and the summary panel of a finished run, in a real browser."""
from queue import Queue
from threading import Thread

import pytest

from oqa import browser, claims, dashboard_server, knowledge, knowledge_flow
from test_evidence_replay import approved


def serve(session):
    app = dashboard_server.Dashboard((session.ws, session.id), timeout=30)
    urls = Queue()
    Thread(target=lambda: app.serve(urls.put, open_browser=False, forever=True), daemon=True).start()
    return urls.get(timeout=5)


def test_the_user_manages_knowledge_on_its_tab_and_reviews_the_summary_of_a_run(session, ws):
    pytest.importorskip("playwright")
    approved(session, ws)
    with session.update() as state:
        state["phase"] = "REPORT"
        state["results"] = {c["id"]: {"status": "PASS", "attempts": 1} for c in session.cases()["cases"]}
    claims.add_note(session, "worker", "A search must be applied from the suggestion list", "ORD-001")
    knowledge_flow.digest(session)
    knowledge_flow.draft(session, [
        {"screen": "res.partner", "kind": "quirk", "fact": "Tìm kiếm phải chọn từ danh sách gợi ý", "scope": "shared", "case": "ORD-001"},
        {"screen": "res.partner", "kind": "rule", "fact": "Contact cần Name để lưu", "condition": "khi lưu"},
        {"screen": "res.partner", "kind": "fix", "fact": "Mục này sẽ bị bỏ"}])
    url = serve(session)
    with browser.launch() as new_page:
        _, page = new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(url)

        # the summary of the run: edit one item, drop one, choose the scope of each, save
        panel = page.locator("section.attn-panel", has_text="Tổng kết knowledge")
        panel.wait_for()
        assert panel.locator(".kcard").count() == 3
        panel.locator('[data-kr="fact"]').nth(1).fill("Contact cần Name để lưu được")
        panel.locator('[data-kr="scope"]').nth(1).select_option("shared")
        panel.locator('[data-kr="keep"]').nth(2).uncheck()
        assert "Lưu 2 mục" in panel.locator("[data-kr-save]").inner_text()
        panel.locator("[data-kr-save]").click()
        page.locator("section.attn-panel", has_text="Tổng kết knowledge").wait_for(state="detached")
        assert {(i["scope"], i["fact"]) for i in knowledge.everything(ws["name"])} == {("shared", "Tìm kiếm phải chọn từ danh sách gợi ý"), ("shared", "Contact cần Name để lưu được")}
        assert knowledge_flow.pending(session) == ""

        # the Knowledge tab of the workspace: both scopes, add, edit, move, delete
        page.locator("#tab-knowledge").click()
        page.locator("#knowledgeRows tr").first.wait_for()
        assert page.locator("#knowledgeRows tr").count() == 2 and "Chung mọi workspace" in page.locator("#knowledgeRows").inner_text()
        page.locator("#knowledgeAdd").click()
        form = page.locator('[data-kbox="new"]')
        form.locator('[data-kf="screen"]').fill("sale.order")
        form.locator('[data-kf="fact"]').fill("Đơn hàng cần khách hàng")
        form.locator("[data-k-create]").click()
        page.locator("#knowledgeRows", has_text="Đơn hàng cần khách hàng").wait_for()
        row = page.locator("#knowledgeRows tr", has_text="Đơn hàng cần khách hàng")
        assert "Riêng workspace" in row.inner_text()
        row.locator("[data-k-move]").click()
        page.locator("#knowledgeRows tr", has_text="Đơn hàng cần khách hàng").filter(has_text="Chung mọi workspace").wait_for()
        page.locator("#knowledgeRows tr", has_text="Đơn hàng cần khách hàng").locator("[data-k-edit]").click()
        edit = page.locator("tr.kedit")
        edit.locator('[data-kf="fact"]').fill("Đơn hàng cần khách hàng và ngày")
        edit.locator("[data-k-save]").click()
        page.locator("#knowledgeRows", has_text="Đơn hàng cần khách hàng và ngày").wait_for()
        page.locator("#knowledgeQ").fill("khách hàng")
        assert page.locator("#knowledgeRows tr").count() == 1
        page.locator("#knowledgeRows tr").locator("[data-k-del]").click()
        page.locator("#knowledgeRows", has_text="Không có mục nào khớp").wait_for()
        assert "Đơn hàng cần khách hàng" not in " ".join(i["fact"] for i in knowledge.everything(ws["name"]))
