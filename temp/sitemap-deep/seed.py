"""Run inside Odoo shell on the disposable sitemap benchmark database."""

import json

if env.cr.dbname != "oqa_sitemap_deep":
    raise RuntimeError("Expected the isolated sitemap sandbox.")

request_model = "oqa.harness.request"
request = env[request_model].search([("name", "=", "ZZ_AUTOTEST_H10_REQ")], limit=1)
if not request or request.state != "submitted":
    raise RuntimeError("Expected the submitted fixture request.")

field_model = env["ir.model.fields"]
model = env["ir.model"]._get(request_model)
references = {
    "x_ab_carrier_reference": "CAR-7129",
    "x_ab_customer_reference": "CUS-8431",
}
for name in references:
    if not field_model.search_count([("model_id", "=", model.id), ("name", "=", name)]):
        field_model.create({"model_id": model.id, "name": name, "field_description": "Reference",
                            "ttype": "char", "state": "manual"})
request.write(references)


def menu(name, parent, sequence, action=None):
    menu_model = env["ir.ui.menu"]
    vals = {"name": name, "parent_id": parent.id, "sequence": sequence}
    if action:
        vals["action"] = "ir.actions.act_window," + str(action.id)
    existing = menu_model.search([("parent_id", "=", parent.id), ("name", "=", name)], limit=1)
    if existing:
        existing.write(vals)
        return existing
    return menu_model.create(vals)


def view(key, kind, xml):
    values = {"name": "oqa.ab." + key, "model": request_model, "type": kind, "arch": xml}
    views = env["ir.ui.view"]
    existing = views.search([("name", "=", values["name"])], limit=1)
    if existing:
        existing.write(values)
        return existing
    return views.create(values)


def action(label, key, parent, list_fields, form=None):
    layout = view(key + ".list", "list", "<list>" + "".join(list_fields) + "</list>")
    actions = env["ir.actions.act_window"]
    values = {"name": label, "res_model": request_model, "view_mode": "list,form",
              "view_id": layout.id, "domain": repr([("id", "=", request.id)]),
              "context": repr({"oqa_ab_surface": key})}
    existing = actions.search([("context", "ilike", "'%s'" % key)], limit=1)
    if existing:
        existing.write(values)
    result = existing or actions.create(values)
    if form:
        links = env["ir.actions.act_window.view"]
        if not links.search_count([("act_window_id", "=", result.id), ("view_mode", "=", "form")]):
            links.create({"act_window_id": result.id, "sequence": 20, "view_mode": "form", "view_id": form.id})
        if not links.search_count([("act_window_id", "=", result.id), ("view_mode", "=", "list")]):
            links.create({"act_window_id": result.id, "sequence": 10, "view_mode": "list", "view_id": layout.id})
    menu(label, parent, 10, result)
    return result.id


root = env.ref("oqa_harness.root_menu")
ops = menu("Operations", root, 40)
finance = menu("Finance", root, 50)
support = menu("Customer Service", root, 60)
dispatch = menu("Dispatch", ops, 10)
warehouse = menu("Warehouse", ops, 20)
billing = menu("Billing", finance, 10)
orders = menu("Orders", support, 10)
dispatch_reviews = menu("Reviews", dispatch, 10)
billing_reviews = menu("Reviews", billing, 10)
warehouse_reviews = menu("Reviews", warehouse, 10)
customer_archive = menu("Archive", orders, 10)
billing_archive = menu("Archive", billing, 20)

details = view("transport.form", "form", """<form><sheet>
  <group><field name="name" readonly="1"/><field name="state" readonly="1"/></group>
  <group>
    <group string="Customer"><field name="x_ab_customer_reference" readonly="1"/></group>
    <group string="Transport"><field name="x_ab_carrier_reference" readonly="1"/></group>
  </group>
</sheet></form>""")

destinations = {
    "dispatch_queue": action("Queue", "dispatch_queue", dispatch_reviews,
                             ['<field name="name"/>', '<field name="site_id" string="Loading Site"/>', '<field name="state"/>']),
    "billing_queue": action("Queue", "billing_queue", billing_reviews,
                            ['<field name="name"/>', '<field name="total" string="Invoice Total"/>']),
    "warehouse_queue": action("Queue", "warehouse_queue", warehouse_reviews,
                              ['<field name="name"/>', '<field name="site_id" string="Storage Site"/>']),
    "customer_records": action("Records", "customer_records", customer_archive,
                               ['<field name="name"/>', '<field name="site_id" string="Service Site"/>']),
    "billing_records": action("Records", "billing_records", billing_archive,
                              ['<field name="name"/>', '<field name="total" string="Archived Amount"/>']),
    "transport_details": action("Details", "transport_details", dispatch,
                                ['<field name="name"/>', '<field name="state"/>'], details),
}
env.cr.commit()
print("OQA_AB_DESTINATIONS=" + json.dumps(destinations))
