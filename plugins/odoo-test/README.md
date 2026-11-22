# odoo-test

Test an Odoo system from a testcase workbook: plan, run every case in a browser with red-framed screenshots, repair, and write the Excel evidence.

Install: `/plugin marketplace add <folder that holds this plugin>` then `/plugin install odoo-test@odoo-test-local`, or try it once with `claude --plugin-dir <this folder>`.
Needs Python 3.9+. The first run sets up its own environment (`oqa doctor --fix`). Hooks start with the plugin: nothing is written into settings.json.
