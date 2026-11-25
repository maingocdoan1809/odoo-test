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

When a step says to confirm on the database, check a record in PostgreSQL or capture a query result, keep it as one planned step and do not open PgAdmin yourself. Write the SQL from the step's wording (table and columns it names, the case's own `AUTO_TEST_` values). Record the step first (`oqa live step`, framing the saved record still on screen), then pass only the SQL:

`oqa pgadmin shot CASE --n N --sql "SELECT id, name FROM res_partner WHERE name = 'AUTO_TEST_x'"`

- It signs in to the PgAdmin of the workspace (address, login, server and database come from `oqa init`), opens a fresh Query Tool on the right database, runs the SQL and adds the real PgAdmin screen to step N as supplemental evidence, result panel framed. The output lists the rows it read, so you can judge PASS or FAIL from them.
- Only one reading statement is accepted (SELECT, WITH, SHOW, EXPLAIN). Anything that writes is refused.
- Query the rows the case itself created or changed (its `AUTO_TEST_` names), and select the columns the expectation names.
- `oqa pgadmin check` only signs in and selects the database (use it to see whether the setup works). Without CASE, `--out FILE.png` saves the picture on its own.
- If it says PgAdmin is not set up or a password is missing, tell the user to run `oqa init` and fill the PgAdmin section; never ask for the password in the chat.

## What a PASS must prove

- Wait for the state, then judge it: after Save, Discard or any onchange the form is still re-rendering. Wait for an observable condition (the Save button hidden, the value shown) before the assertion. Replay does this too, with a bounded settle before each check.
- Assert the specific value the expectation names: the count `1-1 / 1`, the date, the field content. A label that is always on the page (`Archived` tag on any record, a column title) does not prove the outcome. When the expected result names numbers, dates or quoted values, `oqa live step` refuses a PASS whose `--shows`, `--actual` and assertion code contain none of them.
- The picture shows the expectation and the right target: frame the value or message that proves it, after the action, not the control that was clicked.
- Do not require a literal message code. ERR_xxx exists in the design document; the UI shows the message text in its language. Match the text and meaning.
- Corrected conclusions and replaced pictures stay on record (case history, supplemental evidence); never overwrite old evidence to make a case look clean.
