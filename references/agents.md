# Worker coordination

Use Sonnet 5.5 or Sol 6.1 for the coordinator and Haiku or Luna for case workers when the host provides them. Read the host's available worker capacity and use up to five active case slots.

## Coordinator

- After approval, read `oqa status` and `oqa claims`. Assign ready cases to separate workers. Pass the session ID, workspace and case ID.
- Workers use `oqa claim --agent WORKER --case CASE --packet` to get ownership and the case context together. A dependent case waits for its prerequisites. If a prerequisite fails, record the reason the dependent case cannot run.
- Ask the data agent to check `oqa fixture plan SCREEN` when a case needs setup. Keep requested records, fields and returned identifiers in case-scoped `oqa note` messages. Visual-only cases can inspect the UI directly.
- When a worker stalls, get `oqa case context CASE --screen SCREEN --issue ERROR`. Share relevant sitemap, history and knowledge. Retry a runner failure only after identifying a concrete change. A confirmed mismatch is a test result.

## Case worker

1. Claim the case and read `oqa case context CASE`. Use the session and workspace flags on every command. Start with `oqa live start CASE` and use the browser name returned by that command.
2. Inspect the real page before choosing actions. Prepare a case-owned record for state changes, edits or deletes. Run `oqa fixture apply SCREEN --case CASE --key FIELD --values FILE --out SETUP.js` with a JSON object of field values and a workspace-prefixed unique key. Put the generated setup in the verified code for the appropriate step. A relation can refer to an earlier fixture with `{"$fixture":"PREFIX_name"}`.
3. Check the actual result and record every planned step. Capture evidence before a navigation if it removes the target. Add `--verified-code` and `--shows` only for the final path so the tool can assemble the script. Avoid persistent record IDs, secrets and fixed waits.
4. Close with `oqa live end CASE --status STATUS`. Report the result, reason and any remaining fixture data. The coordinator generates the workbook after the queue drains.
