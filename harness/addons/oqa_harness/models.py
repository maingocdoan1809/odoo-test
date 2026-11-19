from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class Site(models.Model):
    _name = "oqa.harness.site"
    _description = "Harness Site"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    active = fields.Boolean(default=True)
    _unique_code = models.Constraint("UNIQUE(code)", "Site code must be unique.")


class Item(models.Model):
    _name = "oqa.harness.item"
    _description = "Harness Catalog Item"
    _rec_names_search = ["name", "code"]

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    site_id = fields.Many2one("oqa.harness.site", required=True, ondelete="restrict")
    cost = fields.Integer(required=True, default=1)
    active = fields.Boolean(default=True)
    _unique_code = models.Constraint("UNIQUE(code)", "Item code must be unique.")
    _positive_cost = models.Constraint("CHECK(cost > 0)", "Item cost must be positive.")

    @api.depends("name", "code")
    def _compute_display_name(self):
        for item in self:
            item.display_name = f"[{item.code}] {item.name}" if item.code else item.name


class Request(models.Model):
    _name = "oqa.harness.request"
    _description = "Harness Request"

    name = fields.Char(required=True)
    site_id = fields.Many2one("oqa.harness.site", required=True, ondelete="restrict")
    state = fields.Selection(
        [("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved")],
        required=True, default="draft",
    )
    line_ids = fields.One2many("oqa.harness.line", "request_id")
    total = fields.Integer(compute="_compute_total", store=True)

    @api.depends("line_ids.subtotal")
    def _compute_total(self):
        for request in self:
            request.total = sum(request.line_ids.mapped("subtotal"))

    @api.constrains("site_id", "line_ids")
    def _check_lines(self):
        for request in self:
            if any(line.item_id.site_id != request.site_id for line in request.line_ids):
                raise ValidationError("All items must belong to the request site.")

    def action_submit(self):
        for request in self:
            if request.state != "draft" or not request.line_ids:
                raise UserError("Only a draft request with at least one line can be submitted.")
        self.write({"state": "submitted"})

    def action_approve(self):
        if not self.env.user.has_group("oqa_harness.group_manager"):
            raise UserError("Only a harness manager can approve.")
        if any(request.state != "submitted" for request in self):
            raise UserError("Only submitted requests can be approved.")
        self.write({"state": "approved"})

    def action_reset(self):
        if any(request.state != "submitted" for request in self):
            raise UserError("Only submitted requests can be reset.")
        self.write({"state": "draft"})


class Line(models.Model):
    _name = "oqa.harness.line"
    _description = "Harness Request Line"

    request_id = fields.Many2one("oqa.harness.request", required=True, ondelete="cascade")
    item_id = fields.Many2one("oqa.harness.item", required=True, ondelete="restrict")
    quantity = fields.Integer(required=True, default=1)
    subtotal = fields.Integer(compute="_compute_subtotal", store=True)
    _one_item = models.Constraint("UNIQUE(request_id, item_id)", "An item can only appear once per request.")
    _positive_quantity = models.Constraint("CHECK(quantity > 0)", "Quantity must be positive.")

    @api.depends("quantity", "item_id.cost")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.item_id.cost

    @api.constrains("item_id", "request_id")
    def _check_site(self):
        self.mapped("request_id")._check_lines()
