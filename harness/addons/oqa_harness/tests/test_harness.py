from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase
from psycopg2 import IntegrityError


class TestHarness(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.site, cls.other_site = cls.env["oqa.harness.site"].create([
            {"name": "Primary", "code": "ORM-A"},
            {"name": "Other", "code": "ORM-B"},
        ])
        cls.item = cls.env["oqa.harness.item"].create({
            "name": "Widget", "code": "ORM-W", "site_id": cls.site.id, "cost": 7,
        })
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.operator, cls.manager = users.create([
            {"name": role, "login": f"oqa-orm-{role}", "group_ids": [
                Command.set([cls.env.ref("base.group_user").id,
                             cls.env.ref(f"oqa_harness.group_{role}").id]),
            ]}
            for role in ("operator", "manager")
        ])

    def test_workflow_and_totals(self):
        request = self.env["oqa.harness.request"].with_user(self.operator).create({
            "name": "Workflow", "site_id": self.site.id,
        })
        with self.assertRaises(UserError):
            request.action_submit()
        request.write({"line_ids": [Command.create({"item_id": self.item.id, "quantity": 3})]})
        self.assertEqual(request.total, 21)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            request.site_id = self.other_site
        self.assertEqual(request.site_id, self.site)
        request.action_submit()
        with self.assertRaises(UserError):
            request.action_approve()
        request.action_reset()
        self.item.cost = 10
        self.assertEqual(request.total, 30)
        request.action_submit()
        request.with_user(self.manager).action_approve()
        self.assertEqual(request.state, "approved")

    def test_import_success_and_invalid_batches(self):
        items = self.env["oqa.harness.item"].with_user(self.operator)
        columns = ["name", "code", "site_id", "cost"]
        valid = items.load(columns, [
            ["One", "IMPORT-ONE", "Primary", "7"],
            ["Two", "IMPORT-TWO", "Primary", "11"],
        ])
        self.assertEqual(len(valid["ids"]), 2, valid)
        self.assertEqual(items.browse(valid["ids"]).mapped("site_id"), self.site)
        for valid_code, invalid in (
            ("REQUIRED-OK", ["", "REQUIRED-BAD", "Primary", "7"]),
            ("RELATION-OK", ["Bad site", "RELATION-BAD", "Missing", "7"]),
            ("DUPLICATE", ["Duplicate", "DUPLICATE", "Primary", "7"]),
        ):
            result = items.load(columns, [["Good", valid_code, "Primary", "7"], invalid])
            self.assertTrue(any(msg["type"] == "error" for msg in result["messages"]), result)
            self.assertFalse(items.search_count([("code", "in", [valid_code, invalid[1]])]))

    def test_line_constraints_and_total(self):
        other = self.env["oqa.harness.item"].create({
            "name": "Widget", "code": "ORM-OTHER", "site_id": self.other_site.id, "cost": 11,
        })
        second = self.env["oqa.harness.item"].create({
            "name": "Widget", "code": "ORM-SECOND", "site_id": self.site.id, "cost": 11,
        })
        request = self.env["oqa.harness.request"].create({
            "name": "Datapool", "site_id": self.site.id,
            "line_ids": [
                Command.create({"item_id": self.item.id, "quantity": 3}),
                Command.create({"item_id": second.id, "quantity": 2}),
            ],
        })
        first, last = request.line_ids
        self.assertEqual(request.total, 43)
        first.quantity = 4
        self.assertEqual(request.total, 50)
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            first.quantity = -1
            first.flush_recordset()
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            request.write({"line_ids": [Command.create({"item_id": second.id})]})
            request.line_ids.flush_recordset()
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            first.item_id = other
        self.assertEqual(first.item_id, self.item)
        self.assertEqual(request.total, 50)
        first.unlink()
        self.assertEqual(request.line_ids, last)
        self.assertEqual(request.total, 22)
        self.assertTrue(self.item.exists())

    def test_archive_and_restrict_delete(self):
        sites = self.env["oqa.harness.site"]
        self.site.active = False
        self.assertEqual(sites.search([("id", "in", (self.site | self.other_site).ids)]), self.other_site)
        self.assertIn(self.site, sites.with_context(active_test=False).search([]))
        self.site.active = True
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.site.unlink()
        self.assertTrue(self.site.exists())
        self.assertEqual(self.item.site_id, self.site)
        self.other_site.unlink()
        self.assertFalse(self.other_site.exists())
