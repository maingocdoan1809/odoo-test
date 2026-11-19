---
name: odoo-test
description: Test an Odoo system from a testcase workbook (.xlsx). Reads the cases, judges whether they are clear and complete, plans, gets the user's approval, runs every case live in its own playwright-cli browser (one agent per case) with a red-framed screenshot of every step, keeps the script playwright-cli produced, writes an Excel evidence file per case. Use when the user asks to test Odoo, run testcases from a file, or invokes /odoo-test. Needs Python 3.9+.
hooks:
  PreToolUse:
    - matcher: "Bash|Write|Edit|MultiEdit|Read|Grep"
      hooks:
        - type: command
          command: "sh -c 'P=$(command -v python3 || command -v python); exec \"$P\" \"${CLAUDE_PLUGIN_ROOT}/hooks/guard.py\"'"
  Stop:
    - hooks:
        - type: command
          command: "sh -c 'P=$(command -v python3 || command -v python); exec \"$P\" \"${CLAUDE_PLUGIN_ROOT}/hooks/stop.py\"'"
---

# odoo-test

Everything goes through one command: `python "${CLAUDE_SKILL_DIR}/oqa.py" <command>` (written `oqa <command>` below). `oqa.py` is the file next to this SKILL.md: if the path before it was not filled in with a real folder, use the folder this file is in. Every command checks the session's phase and
refuses out of turn, naming what to run instead: **follow its `Next:` line, do not improvise the order.** Reply to the user in the workspace's language (`oqa ws show`).
Files and formats: [references/contract.md](references/contract.md). Odoo specifics: [references/odoo.md](references/odoo.md).

## The process

| Phase | You do | Command |
|---|---|---|
| setup | check the machine, then the workspace | `oqa doctor` (`--fix` if not READY, or if it says `global hooks: not installed`: that adds two entries to Claude Code's settings.json, keeps a backup, and is how a new user gets the always-on behaviour without doing anything by hand) · `oqa init --status`; if not configured run `oqa init` **in the background**: it serves the form and opens it in the user's browser by itself (do not ask them to open anything), then exits when they save, and prepares the environment on its own (`oqa prepare`: the sitemap and the ready lines of every screen, once; `oqa session new` starts it too when it was never done, `oqa prepare --status` says how it went) |
| INTAKE | read the workbook the user named (add-in: the open file; terminal: the file they gave) | `oqa session new --file <path> [--cases A,B,C]`: when the user names only some cases of a workbook, pass them (ids, `A..C` ranges or words of the title; `oqa session cases --file <path>` lists what is in it) and use their file as it is: **never ask for a new file holding just those cases**. Then at once `oqa dashboard` **in the background**: it serves the progress page and opens it on this session (or reopens the running one). Do it without asking |
| ANALYZE | judge every case like a careful tester (below), then hand it in | `oqa analyze --template --out <scratch>/analysis.json` → fill → `oqa analyze --submit <file>`. A case that needs the user keeps the session here: the questions are asked for you (all at once, on the dashboard); `oqa wait` in the background, stop, and when they answer fetch the template again |
| CONNECT | sign every profile in | `oqa connect` (`--headed` if a two-factor code is needed: ask the user) |
| CONFIRM | build the strategy, show it, **stop** | `oqa strategy`. Tell the user the strategy is waiting on the dashboard that is already open. Only they confirm (the Confirm button there, or `oqa confirm` in their terminal). You cannot, and must not try. Then `oqa wait` **in the background** |
| EXECUTE | one worker per case, each in its own playwright-cli browser, acts live and records every step with a picture | `oqa live start ID` → act with `playwright-cli -s=oqa-ID …` → `oqa live step ID --n N …` for each step → `oqa live end ID` |
| REPORT | close | `oqa report`: it writes ONE Excel evidence workbook (a summary sheet, then a sheet per case: what the testcase asked, how you understood it, every step with its red-framed picture, the runs) and **opens it on the user's screen**. Tell them the file path. They can also rebuild or download it any time from the dashboard |

**Setup is declared once, everything lives in the oqa Home (`~/.odoo-test`, or `$ODOO_TEST_HOME`)**, except the packages, which are always in `~/.odoo-test/venv` whatever the Home is: `workspaces/<name>/workspace.json` (URL, logins, prefix), `knowledge/` (sitemap), `scripts/`, `sessions/<id>/auth/` (the signed-in state per profile) and `secrets.json` (saved logins, mode 600; Windows uses Credential Manager, macOS/Linux with a desktop keyring use it). `oqa init` fills it once; later sessions only run `oqa session new`. Every skill command runs through `oqa.py`, which re-runs itself with the python of that venv, so any `python` / `python3` works: never report a missing environment, run `oqa doctor --fix`. `oqa connect` signs in with the saved login; with none, it reuses the newest signed-in session of the workspace, and asks for `oqa init` only when that has expired. There is no env file.

**EXECUTE is run by one worker per case, all started in the same turn** (Sonnet 5.5; the coordinator, you, starts them with the Agent tool and reads their reports). There is no exploring pass and no script to verify first: each worker opens ITS OWN browser (`oqa live start`), reads the real page, acts, fixes its own mistakes in the browser, and records the outcome of each step once. Roles and the worker prompt: [references/agents.md](references/agents.md). The coordinator never drives a case by hand.

**Nothing waits for the user to remind you.** `oqa session new` starts the dashboard by itself. While the user owes an action (an answer, the Confirm button) an `oqa wait` must be running in the background: the Stop hook pushes back if it is not. The hooks in this file only run once the skill is loaded; for the setup form, the dashboard and the user's dashboard actions to be watched from the very first message, those two hooks (UserPromptSubmit, Stop) are written into `~/.claude/settings.json` by `oqa doctor --fix` or `oqa hooks --install` (a backup is kept, nothing else is changed); `oqa hooks` alone only prints the snippet. They take effect in the next Claude Code window.

Anytime: `oqa ref add|list|show` (files the user pointed at by path) · `oqa status` · `oqa ask "<question>" [--case ID]` (then `oqa wait` in the background and stop) · `oqa wait` (ends when the user acts on the dashboard: confirms, answers, sends a hint, stops or resumes, and says which; the dashboard has a Stop button, and a hint box for a case that keeps failing) · `oqa knowledge search|learn` · `oqa sitemap find|buttons|fields|path|codegen|build` (where a screen is, its buttons and fields: the only helper kept besides playwright-cli) · `oqa dashboard`.

## ANALYZE: the part that decides everything

Act as a person given this job would. The workbook is written for people: short, with assumed data. A blocked case in EXECUTE is a mistake made here, so take the time now. In this order, for each case:
1. **Read everything the case rests on.** `session new` printed warnings: embedded pictures, cell comments, other sheets are not read as text, so `oqa ref show "<workbook>"` (it saves the pictures: open them with the Read tool). A file the case names is read with `oqa ref show <name>`.
2. **Never guess what was left unsaid.** Intake flags wording that cannot be run as written (`vague` in the template): a result judged against a document nobody named ("as designed", "đúng design", "仕様通り", an attachment without a file), or one that cannot be measured ("works correctly", "hiển thị bình thường", no expected result). Each flag needs a `vague_resolved` entry: `concrete` = what will be checked as facts (which fields, which labels, in which order, which value), `source` = `ref:<file the user gave>` or `user_answer` (or `workbook` / `domain` for what only needs reading). The validator refuses a guess. No file yet: the case is `needs_user`, and the question names what you need ("the basic design of the order screen: send me its path").
3. **Make what is missing instead of asking or blocking.** Data the environment may lack (a record, a second company, more rows than a page, a record in a given state, a file to import) is a `data_need` with the `fallback` that creates it through the UI or an import; most "missing data" ends here. Check first what exists (`oqa sitemap find`, the page itself).
4. **Only then ask.** `needs_user` is for what only the user can say (the meaning of the case, a file you do not have, a business rule). Write full-sentence questions with the options you see, and say in `data_considered` what you already tried. `blocked` is the last resort (an outside service, no right to create anything): `data_considered` says why creating it is not possible.
5. **Files come by path, never by upload.** Tell the user to paste the path (in the dashboard's *Tệp tham chiếu* box or inside their answer): it becomes a reference by itself, and the next `--template` carries the answers (`_user_answers`) and the files (`_shared_files`) and finds the missing references among them. Read them with `oqa ref show`.

Then fill the template honestly:
- `clarity` 1-5 (is the intent unambiguous?) and `sufficiency` 1-5 (does it carry what is needed to run?). Below 3 means `needs_user`: put the questions in `questions` and `oqa ask` them now, not after a failed run.
- `refs`: every file or record the case points at, each with its real `path` (an `ok` reference with no file at its path is refused). Intake already marked which files exist; a missing one is `needs_user` unless it can truly be waived (the reason goes in `note`), never invented. A document the case judges against (the design, the spec) is a ref even when the workbook gives no file name.
- `data_needs`: data the environment may lack (a list long enough to paginate, a second company, a record in a given state), each with a `fallback`: the step that creates it (`o.reach`, `o.new` + `o.save`). A case must run on **any** environment, so plan the creation, do not hope.
- `edge_cases`: what can differ between environments or runs: empty list, more than one page, permissions of the profile, language, default values, a record already existing.
- `depends_on` + `reuse`: when a case can start from where another ended, say so; the runner then keeps the screen instead of starting over.
- `steps`: what you will actually do, finer than the workbook, each with an observable `expected`. They become the captions of the evidence pictures.
- `previous` (in the template, only when this file was tested before): how the case ended last time, why, and the user's hints. **Use it**: follow the hints, plan for the reason it failed (a precondition to create, a selector that did not fit), do not repeat an approach that failed, and do not ask again what the user already answered.
- `profile`: which profile (Admin, Teamlead, …) runs it.
- `timeout_s` (optional, 5-120, default 8): for a case whose pages are slow (a report, a wizard, a long list).
- Read each case the way the product works, not only as written: what must be true for a step to work (a required field, a state, a permission, a record that must exist)? Put it in `data_needs` / `refs`, or `needs_user`.
- A phrase with two readings that give different results ("the customer who bought the most": one big order, or the largest total across orders?): write the reading you chose into the step, and ask when the outcomes differ.

## EXECUTE: one worker, one browser, one case (playwright-cli)

What a worker does for its case, in this order. No retry loop: a wrong move is fixed in the browser, and only the outcome is recorded.

1. `oqa live start ID` opens the case's own browser (`playwright-cli -s=oqa-ID`, already signed in; oqa loads the saved sign-in itself, you never see it) and prints the planned steps. The browser stays open until `oqa live end`: keep working in it, do not close it, do not open another.
2. **Read before acting.** `playwright-cli -s=oqa-ID snapshot` gives the page as a tree with refs (`e15`). Search it with `playwright-cli -s=oqa-ID find "<text>"`. Labels are in the page's language (Japanese on a Japanese Odoo): read the real list, row or form, then act on a ref or a locator you saw. Never type a selector you did not see. Useful: `oqa sitemap find|buttons|fields|path` for where a screen is and what it holds.
3. **Act** with `playwright-cli -s=oqa-ID click|fill|select|press|goto …`. A wrong click, a dropdown that did not take the value, a form that did not save: look at the new snapshot, correct it right there. Data you need is made through the UI, with the workspace prefix; look first at what exists.
4. **Record each planned step once**, in order: `oqa live step ID --n N --status PASS|FAIL --actual "what the page showed" --target "<locator>"`. `--actual` is a fact read from the page (a value, a message, a count) that you compared with the step's expected result; PASS only when they match. `--target` is the element the step is about (a CSS selector or `getByRole('button', { name: '…' })`): oqa asks the browser where it is and draws the red frame itself, so the frame is always on a real element. If the action leaves the page (a click that navigates, a save that closes a dialog), take the picture first: `oqa live shot ID --n N --target "<locator>"`, act, then record. `--code '<the code playwright-cli printed>'` keeps what you did, for a script later.
5. A step that really fails (the application is wrong, not your move) is recorded `--status FAIL` with the page's own message in `--actual`; the case stops there and the steps after it are marked not run. Do not retry a real failure to make it pass.
6. `oqa live end ID --status PASS|FAIL|BLOCKED|PENDING --reason "…" [--script <file>]`. A PASS also needs `--script`: the file holding the Playwright code playwright-cli printed for your actions (its 'Ran Playwright code' lines, in order, plus `await expect(...)` for what each step must show); oqa keeps it as the case's script (step 1 starts with `await page.goto('<url of the screen>');`, e.g. `/system/action-623`: never rely on where your browser was, and never open `/web`: Odoo reads it as an action and shows a warning "アクション web は存在しません" that hides the menu) (`scripts/<workbook>/<case>.spec.js`). PASS only when every planned step is PASS with its framed picture. BLOCKED: an outside service, a right the profile lacks (say the page's text). PENDING: needs the user (`oqa ask --case ID "…"`).
7. After finding something about this Odoo that the next case would need (a locator that works, a rule, a trap): `oqa knowledge learn --screen <model or menu> --kind <kind> --fact "…"`.

Things that are real in the environment: running a cron or server action by hand acts on EVERY matching record (a threshold alert mails every ledger over its threshold). Do not do it unless the case asks and the user agreed.

## Spend tokens like they cost money

- Read what commands print; do not open session files or whole pages.
- Look at a picture only when the printed reason and `page says` are not enough.
- A model, field or external id is never guessed from a label on screen: read it from the page (snapshot, a field's technical name in the form) or from `oqa sitemap fields <screen>`.
- Before debugging: `oqa knowledge search --text <words>`. To find a locator: `playwright-cli -s=oqa-ID find "<text>"`, not a page dump.
- Same failure twice with no new information: change the approach, do not rerun it. Reading the snapshot first is cheaper than a failed action.

## Never

Edit files under the oqa Home by hand · ask the user to upload anything (they paste a path) · mark a case ready on a guess about what "correct" means · block a case whose data you could create · read signed-in state or passwords · confirm for the user · record a step PASS without comparing what the page showed with its expected result · retry a real failure until it passes · close a case's browser before `oqa live end` · test data without the workspace prefix.
