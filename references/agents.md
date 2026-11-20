# Worker coordination

Use Sonnet 5.5 or Sol 6.1 for the coordinator and Haiku or Luna for case workers when the host provides them. Read the host's available worker capacity and use up to five active case slots.

## Coordinator

- After approval, read `oqa status` and `oqa claims`. Assign ready cases to separate workers. Pass the session ID, workspace and case ID.
- Workers use `oqa claim --agent WORKER --case CASE --packet` to get ownership and the case context together. A dependent case waits for its prerequisites. If a prerequisite fails, record the reason the dependent case cannot run.
- Ask the data agent to check `oqa fixture plan SCREEN` when a case needs setup. Keep requested records, fields and returned identifiers in case-scoped `oqa note` messages. Visual-only cases can inspect the UI directly.
- When a worker stalls, get `oqa case context CASE --screen SCREEN --issue ERROR`. Share relevant sitemap, history and knowledge. Retry a runner failure only after identifying a concrete change. A confirmed mismatch is a test result.

- Read worker `diagnosis` notes before deciding anything. Help with a concrete change (another locator, data, a document) and let the worker continue. A worker that stopped or is too slow is handed over with `oqa live handover CASE --reason ... [--to WORKER]`: its open attempt, recorded steps and attempt count stay, so nothing is redone and no recovery budget is used. `oqa live recover` is for an attempt that must be thrown away; it uses the one recovery.
- A conclusion you reopen is kept in the case history with what replaced it. Classify every non-PASS: application defect (FAIL, with evidence), automation issue or missing precondition (BLOCKED/PENDING, with the diagnosis).
- Replay results are separate (Replay PASS/FAIL/NOT RUN): after a run use `oqa live replay all --fast --headless` and treat a Replay FAIL as a script problem to fix, never by weakening an assertion.

## Case worker

1. Claim the case and read `oqa case context CASE`. Use the session and workspace flags on every command. Start with `oqa live start CASE` and use the browser name returned by that command.
2. Inspect the real page before choosing actions. Prepare a case-owned record for state changes, edits or deletes. Run `oqa fixture apply SCREEN --case CASE --key FIELD --values FILE --out SETUP.js` with a JSON object of field values and a workspace-prefixed unique key. Put the generated setup in the verified code for the appropriate step. A relation can refer to an earlier fixture with `{"$fixture":"PREFIX_name"}`.
3. Follow [evidence requirements](evidence.md) and each step's `evidence` plan. Capture every meaningful UI action and intermediate state, including opened forms, entered values, controls before clicking and results after clicking. Use repeated `live shot --caption TEXT` (MCP `evidence_before`) for supporting captures in a step; then `live step` for its observed result. Inspect the pictures and frame the exact unique target. Similar final-screen copies do not establish different actions. Add `--verified-code` and `--shows` only for the final path so the tool can assemble the script. Avoid persistent record IDs, secrets and fixed waits in saved code; the replay runner supplies configurable viewing pauses.
4. Verify before concluding. Operate the right control: a link inside a cell is not the empty area of the cell; confirm in the snapshot which element you used. Compare messages by text in the UI language against the documented message (`message_codes` in the case context); a code such as ERR_015 is not UI text. After Save or Discard wait for the state you check (onchange included) and assert the specific value, not a label that is always on the page. The picture must show that value and the target.
5. When stuck, do not close the case because a selector was not found or time passed: `oqa live diagnose CASE --category automation --detail "what you saw" --tried "locators and results"` (or `--category precondition --need "..."`), try what the coordinator answers, and only then close BLOCKED/PENDING with `--category`. A FAIL names the verified control: `--control`.
6. Create records through the screen: `oqa fixture key`, `oqa fixture ui ledger|license_user|contract ...`; use existing records only as `{"$ref": ...}` (read only). Relative dates use `{"$date": N}`. Never write through the ORM or edit the DOM to produce a UI outcome.
7. Close with `oqa live end CASE --status STATUS`. Report the result, reason and any remaining fixture data. The coordinator generates the workbook after the queue drains.
