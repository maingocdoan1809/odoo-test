# Odoo, as a test target

Odoo is not a generic web app. Read this when a step surprises you.

## After login
- The landing page differs by version: `/web` (16-17), `/odoo` (18). `oqa connect` records the real one in knowledge (`kind:login`); do not assume.
- A database selector (`/web/database/selector`) comes first on multi-database servers: the workspace needs its `db`.
- Two-factor (`input[name=totp_token]`) stops a script. Ask the user (`oqa connect --headed`) or use a test account without it.
- The client is a single page: wait for `.o_web_client` and for the loading indicator (`.o_loading_indicator`, `.o_blockUI`) to go, never sleep. A replay waits (bounded, 3 s) for the page to stop changing before it checks a step. A slow screen: raise the case's `timeout_s`.

## Look it up, do not guess (read only)
Names on screen are labels; locators and checks need technical names. Read them from the saved sitemap (built once per workspace, `oqa prepare`) or from the case's own browser:

| You want | Command |
|---|---|
| which screen (menu, action, view) matches "invoice" | `oqa sitemap find invoice` |
| the technical name behind the label "Customer" on a form | `oqa sitemap fields <screen> --match customer` (`--required` for the required ones) |
| the buttons of a screen, and what each opens | `oqa sitemap buttons <screen> [--view list]` |
| the way from the menus to a screen | `oqa sitemap path <screen> [--from <screen>]` |
| the case, its analysis, feedback and what is known about its screens | `oqa case context <CASE>` |
| what is on the page right now (roles, labels, refs) | `oqa live act <CASE> --browser-command snapshot` |
| data a case needs | `oqa fixture plan\|ui\|ref\|key` (see fixtures.md): never write to Odoo another way |

Changing data is done through the screen, like a user.

## Where things are
| You want | Look for |
|---|---|
| an app / menu | apps toggle `.o_navbar_apps_menu`, sections `.o_menu_sections`, items in `.dropdown-menu` |
| a button | `button` by its text, inside `.o_control_panel`, `.o_form_view`, or a `.modal` |
| a form field | `.o_field_widget[name='<technical name>']` (use the technical name, labels change with language) |
| a many2one | type, then click the suggestion (`.o-autocomplete--dropdown-item` in 17+) |
| list rows / pager | `.o_data_row`, `.o_pager_value` / `.o_pager_limit` (80 rows per page by default) |
| a message | `.o_notification` (error: `.border-danger`), dialogs `.modal` |
| invalid / required fields | `.o_field_invalid`, `.o_required_modifier` |

When a selector does not fit this Odoo: take a `snapshot` (above), use what it shows, then `oqa knowledge learn --kind locator`.

## Troubleshooting

| Symptom | Usual cause | What to check |
|---|---|---|
| Save does nothing | a required field is empty (`.o_required_modifier`), or a validation toast was missed | read the notification; fill the field; save again |
| Button not found | it lives in the Action/Print dropdown, a statusbar, or appears only in a state (draft / confirmed) | `oqa sitemap buttons <screen>`, or a `snapshot`; check the record's state first |
| "Discard changes?" dialog | you navigated away from a dirty form | save or discard first |
| Click lands on nothing | an onchange re-rendered the form | wait for the page to settle, then act again |
| The list is empty | the default filter hides the records, or the data is not there | clear the filter, then create the data with `oqa fixture` |
| Pagination case on a small database | fewer than 80 records | create the missing records with `oqa fixture` (each named with the test prefix) |
| Menu missing for a profile | permissions: that is often the thing being tested | assert it absent, with a framed picture of the menu |
| Labels differ from the workbook | the user language is not the workbook's | match by technical name; note it in knowledge |
| Read-only after save | the record's state locks fields | check the state before editing |

## Data rules
- Existing records are read-only. Create only records whose name starts with the workspace's test prefix (`oqa fixture key` gives the name), through the UI.
- Creating data is a write: if the case does not need to create it, do not.
- Anything that sends mail, posts payments or calls an outside service is out of scope unless the user said so: ask.
