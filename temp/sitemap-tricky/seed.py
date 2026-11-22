"""Prepare a stale-sitemap challenge inside the cloned Odoo database."""

import json
from pathlib import Path
import sys

if env.cr.dbname != "oqa_sitemap_tricky":
    raise RuntimeError("Expected the isolated tricky benchmark database.")

root = Path("C:/Users/maido/odoo-test")
ids = json.loads((root / "temp/sitemap-deep/destinations.json").read_text(encoding="utf-8"))
actions = env["ir.actions.act_window"]
old_storage = actions.browse(ids["warehouse_queue"])
live_storage = actions.browse(ids["billing_queue"])
form = env["ir.ui.view"].search([("name", "=", "oqa.ab.transport.form")], limit=1)
request = env["oqa.harness.request"].search([("name", "=", "ZZ_AUTOTEST_H10_REQ"), ("state", "=", "submitted")], limit=1)
if not form or not request:
    raise RuntimeError("Missing the existing benchmark form or submitted record.")

draft = env["oqa.harness.request"].search([
    ("name", "=", request.name), ("state", "=", "draft"), ("x_ab_carrier_reference", "=", "CAR-1290")
], limit=1)
if not draft:
    draft = env["oqa.harness.request"].create({
        "name": request.name, "state": "draft", "site_id": request.site_id.id,
        "x_ab_carrier_reference": "CAR-1290", "x_ab_customer_reference": "CUS-6158",
    })

old_storage.view_id.arch = '<list><field name="name"/><field name="site_id" string="Inventory Site"/></list>'
live_storage.view_id.arch = ('<list default_order="id desc"><field name="name"/>'
                              '<field name="site_id" string="Storage Site"/><field name="state"/></list>')
live_storage.domain = repr([("id", "in", [request.id, draft.id])])
links = env["ir.actions.act_window.view"]
for order, view in ((10, live_storage.view_id), (20, form)):
    if not links.search_count([("act_window_id", "=", live_storage.id), ("view_mode", "=", view.type)]):
        links.create({"act_window_id": live_storage.id, "sequence": order, "view_mode": view.type, "view_id": view.id})
standard = env.ref("oqa_harness.request_action")
for order, view in ((10, env.ref("oqa_harness.request_list")), (20, env.ref("oqa_harness.request_form"))):
    if not links.search_count([("act_window_id", "=", standard.id), ("view_mode", "=", view.type)]):
        links.create({"act_window_id": standard.id, "sequence": order, "view_mode": view.type, "view_id": view.id})
env.cr.commit()

sys.path.insert(0, str(root))
from oqa import sitemap


def read_metadata(model, method, args=None, kwargs=None):
    kwargs = dict(kwargs or {})
    records = env[model].sudo().with_context(**kwargs.pop("context", {}))
    if method == "read":
        return records.browse(args[0]).read(**kwargs)
    if method in {"search_read", "get_views"}:
        return getattr(records, method)(*(args or []), **kwargs)
    raise ValueError(f"Unexpected metadata operation: {method}")


out = root / "temp/sitemap-tricky"
(out / "fresh-sitemap.json").write_text(json.dumps(
    sitemap.build(read_metadata, "Metadata admin", "19.0"), ensure_ascii=False), encoding="utf-8")
(out / "oracle.json").write_text(json.dumps({
    "correct_screen": live_storage.id, "stale_screen": old_storage.id,
    "correct_record": request.id, "draft_record": draft.id,
    "candidates": [ids["dispatch_queue"], ids["billing_queue"], ids["warehouse_queue"]],
}), encoding="utf-8")
