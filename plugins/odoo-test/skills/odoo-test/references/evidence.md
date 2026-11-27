# Evidence for every UI action

Read this while expanding the testcase analysis and before executing a case. Evidence must let a reviewer follow what was clicked, what was entered, and what the application showed at each transition.

## Plan before execution

Keep the workbook's intent, but expand compound steps into meaningful actions with consecutive `n` values. Give each step an observable `expected` and an `evidence` description specifying the visible state and the exact region to frame. Do not guess a CSS selector before inspecting the real page. An evidence description is a capture requirement, not a claim that the capture already exists.

For creating a record, plan: New button before clicking → opened blank form → each filled field or coherent field group with values readable → Save button on the completed form before clicking → persisted values/status after saving. If the testcase checks each field individually, make each field a separate step. Capture dependent dropdown selections and dialogs while visible. If opening a form or clicking Save also navigates, capture its control before the click as supporting evidence of that step, then capture the resulting state with `live step`.

Apply the same reasoning to other cases:

| Operation | Evidence checkpoints |
| --- | --- |
| Edit | Original value, opened edit form, changed field, Save control, persisted changed value |
| Delete | Case-owned record, Delete control, confirmation dialog, confirmed disappearance in the relevant list |
| Search/filter/sort/pagination | Control and entered criteria, selected suggestion/filter/order/page, resulting rows and relevant count/order/pager |
| Validation/negative case | Invalid or empty fields, submit control, exact error and affected field; expected rejection passes |
| Workflow/permissions | Initial status/account, action control or its absence in its parent, confirmation dialog, resulting status or access message |
| Import/export | Selected file/options, mapping/preview, execution control, result/error/download confirmation |
| Visual/absence check | Each checked region framed separately; use its smallest visible parent when the expected element is absent |

Do not satisfy this by taking two copies of the final screen or adding a fixed number of pictures. Each picture must establish a distinct action, value, region, or result relevant to the assertion.

## Capture and review

1. Inspect the current UI and choose a locator matching exactly one visible element. For fields, frame the label/value or containing field widget; for a click, frame its button; for results, frame the changed row, message, or status. Avoid framing the whole screen when a specific target exists.
2. Capture a control before clicking it when it will disappear or the page will change. Use `oqa live shot CASE --n N --target LOCATOR --caption "Save on the completed form"`. The caption must describe the actual checkpoint in the workspace language. MCP workers use `evidence_before` with `n`, `target`, and `caption`.
3. Perform the action, wait for an observable condition, and record `live step` with the post-action target and the observed result. A before picture alone does not prove that save/delete/submit succeeded. For compound steps in older approved plans, repeat `live shot` after every intermediate action, before continuing. All shots are kept in chronological order, followed by the result image.
4. Inspect raw and annotated images before closing the case: verify field values are readable, frames touch the correct controls, no dialog hides the assertion, and adjacent images with the same screen show distinct relevant regions/states. Recapture incorrect or missing evidence while that state is still available. Never reconstruct an earlier screen and present it as the original action.

The dashboard gallery and Excel evidence sheet retain all pictures. Supplemental pictures attached after a recorded step are appended and do not replace the original evidence. Older sessions without an `evidence` field remain readable; new analyses must fill the field and split actions before approval. For a step with an evidence plan, PASS requires a visible post-action target; the tool refuses to substitute a before picture for its result. Extra captures within an old compound step are live evidence; automatic replay verification follows the saved step boundaries. Plan separate steps when those intermediate states must also be reproduced on replay.

## Database evidence (PgAdmin)

Keep a database verification as one planned step. Set `database` in the analysis to the exact expected rows, using SQL result column names or aliases and text values or JSON `null`. For example, `"database": [{"phone":"0901234567","active":"true"}]` requires one row with that phone and status. Use `[]` when the expected result is zero rows. Derive these expectations from the approved testcase or design before running the query.

Run the query immediately after the Odoo action has saved and before the next action changes the record. Filter by the current case's fixture key and select the fields whose values must be proved. For the example above, replace `FIXTURE_KEY` with the name generated for the case.

```sh
oqa pgadmin shot CASE --n N --sql "SELECT phone, active FROM res_partner WHERE name = 'FIXTURE_KEY'"
```

- Compare multiple related records by selecting their identifying values and using `ORDER BY` for deterministic row order. For example, select the parent code, line product, and quantity in a joined query. To assert a count returned by SQL, select `COUNT(*) AS line_count` and expect `[{"line_count":"2"}]`.
- Use unique aliases. Text compares exactly, including leading zeros. Use `{"phone":null}` for SQL NULL and `{"phone":""}` for an empty string. You can also select `phone IS NULL AS phone_is_null` and expect `"phone_is_null":"true"`. For dates and decimals, specify the expected display format or use an explicit SQL expression such as `to_char`.
- `oqa pgadmin shot` opens a Query Tool on the selected database, runs one read-only statement, and captures the query and framed result. It reports PASS when the row count and every specified cell match, FAIL for a mismatch, and UNVERIFIED when a query error or unreadable grid prevents comparison. Keep the query narrow enough for the grid to show all requested rows and columns.
- Use `oqa live step` with the PgAdmin verdict and an Odoo locator for the current record or relevant view. The step records the expected and observed values alongside the screenshots. A query error, missing or ambiguous result columns, or an incomplete grid needs a corrected query or a diagnosis before closing the case as BLOCKED. UI replay verifies the Odoo outcome. The live run performs the database verification.
- For an older approved database step without `database`, pass the expected rows via `--expect '[{"phone":"0901234567","active":"true"}]'`. A supplied expectation on a step with an approved `database` must match that plan. For a separate capture, omit CASE and use `--out FILE.png`. An image added after the step has been recorded is supplemental evidence.
- `oqa connect` verifies access to the workspace's PgAdmin and selects the configured database. Use `oqa pgadmin check` to test the connection on its own. If the configuration or saved password is missing, ask the user to fill in PgAdmin through `oqa init`.

## What a PASS must prove

- Wait for the state, then judge it: after Save, Discard or any onchange the form is still re-rendering. Wait for an observable condition (the Save button hidden, the value shown) before the assertion. Replay does this too, with a bounded settle before each check.
- Assert the specific value the expectation names: the count `1-1 / 1`, the date, the field content. A label that is always on the page (`Archived` tag on any record, a column title) does not prove the outcome. When the expected result names numbers, dates or quoted values, `oqa live step` refuses a PASS whose `--shows`, `--actual` and assertion code contain none of them.
- The picture shows the expectation and the right target: frame the value or message that proves it, after the action, not the control that was clicked.
- Do not require a literal message code. ERR_xxx exists in the design document; the UI shows the message text in its language. Match the text and meaning.
- Corrected conclusions and replaced pictures stay on record (case history, supplemental evidence); never overwrite old evidence to make a case look clean.
