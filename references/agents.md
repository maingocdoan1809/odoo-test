# Running EXECUTE: one worker per case, each in its own browser

One coordinator (Sonnet 5.5, you) and one worker (Sonnet 5.5) per case of the strategy, all started in the SAME turn with the Agent tool, so no case waits. A worker has its own playwright-cli browser (`oqa live start`), so workers never move each other's page. There is no validator and no exploring pass: the analysis already judged the case.

## Coordinator
- `oqa status` for the cases; one Agent call per case (at most 6 at a time), `run_in_background: true`.
- Cases that depend on each other (`depends_on`) go to ONE worker, in order. Data every case needs first (an import file, a second company) is made by the first worker and reported with `oqa note`.
- Reads the reports, tells the user PASS / FAIL / BLOCKED per case in the user's language, then `oqa report`. Never drives a case by hand, never records a step for a worker.
- Gives a hint when a worker comes back stuck: `oqa ask`.

## Worker prompt (copy it, fill the case id and workspace)
> You test case <ID> of Odoo session <SESSION>, workspace <WS>. Run every oqa command as `python3 <skill>/oqa.py <command> --ws <WS>`. Read <skill>/SKILL.md section "EXECUTE" and follow it: `oqa live start <ID>`, then work in YOUR browser with `playwright-cli -s=oqa-<ID> …` (read the snapshot before every action, never guess a selector), record every planned step once with `oqa live step`, close with `oqa live end`. Fix your own mistakes in the browser; do not retry a real failure. Never confirm the strategy, never edit files under ~/.odoo-test, never read cookies or passwords, never run a cron or server action unless the case asks for it. Test data carries the workspace prefix. Report in Vietnamese: PASS/FAIL/BLOCKED, the reason, the evidence path, the test data left behind.

## Rules that save time
- Read the language of the screen first: labels, menus and cron names are in that language (Japanese on astragate), not in English.
- Make data in small actions and look at the snapshot after each: a dropdown that lists only some records, a form that saves without an error, are Odoo behaviours to read, not to retry blindly.
- A click that leaves the page: `oqa live shot` BEFORE it, so the picture still shows the element.
- A step is recorded once. Correct things in the browser first, then record what the page finally showed.
