from oqa import browser, connect
from support.fake_odoo import start


def test_login_uses_origin_when_workspace_url_is_a_backend_route(ws, store):
    server, origin = start()
    try:
        config = dict(ws, url=origin + "/system")
        with browser.launch() as new_page:
            _, page = new_page()
            result = connect.login(page, config, config["profiles"][0], store)
            assert result["landing_url"] == origin + "/odoo"
            assert page.locator(".o_web_client").is_visible()
    finally:
        server.shutdown()
