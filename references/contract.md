# Contract: files and commands

## Workbook run entry point

- `oqa run start --file FILE [--cases SELECTION] [--channel path|addin]` waits for the workspace picker and creates the session. The default channel is `path`. The dashboard opens after the first submitted analysis.
- Repeating `run start` for the same workbook, unchanged selected cases and channel reuses the current session while it is in ANALYZE, CONNECT or CONFIRM. Both review rounds update that session's analysis and strategy. `--new-session` explicitly starts a separate run; execution and completed sessions are never implicitly reset.
- For an exact selection across sheets, use comma-separated regions, e.g. `--cases 'License Ledger List!A64:R64,License Ledger Detail (New)!A29:R29'`. These path runs retain the original workbook, qualify case IDs with the sheet name, read merged-cell values from their anchors, and write each result to its original sheet/row in a result copy. Multi-sheet add-in write-back is not supported.
- The dashboard shows the original and assessed case. `POST /api/review` takes `{ws,id,case,text?,skip?}`. A review remains scoped to that case. `oqa wait --until reviews` returns when every pending review is handled. Final strategy approval uses the strategy hash and a separate action.
- `oqa case context CASE [--screen TEXT] [--issue TEXT]` returns both testcase versions, references, feedback, sitemap hits and recent context. `oqa claim` enforces five case slots. A live attempt can be recovered once with `oqa live recover CASE --reason TEXT`.
- `oqa fixture plan SCREEN` returns writable fields, the model and route. `oqa fixture apply SCREEN --case CASE --key FIELD --values JSON_FILE` uses the live case's account, requires a prefixed unique key and includes its setup in the next replayable step. A related field may use `{"$fixture":"PREFIX_key"}` from the same browser session. `--values-json` accepts inline values. `fixture advise` requests a data-agent proposal.
- `oqa live step` accepts `--verified-code JS_FILE --shows TEXT` to build its replay script from the selected actions. The result of live start includes its browser session name. Dashboard `GET /api/scripts?ws=WS&id=SESSION&t=TOKEN` exports an executable bundle. Reruns use the saved case script.
- Path runs write a result copy of the source. Add-in runs apply result cells and evidence sheets through desktop Excel. If Excel write-back is unavailable, the CLI returns the staged result and its write-back error.
- `oqa dispatch` runs the approved queue with the local Claude CLI. Each worker receives only its case-scoped MCP tools for browser actions, evidence, fixtures, references and communication. Sonnet supplies recovery notes. The five-slot limit applies across host and CLI case workers.
- Dashboard replay and `oqa live replay CASE` open a headed browser on the case's saved profile and execute its saved actions and assertions. They use the original test session. Evidence-based verification runs in the harness are a separate operation.
- Replay accepts `--action-delay-ms N` (default 700) and `--step-delay-ms N` (default 1500), whole milliseconds from 0 to 60000. Pauses run after each action and after the step's assertion respectively; 0 disables either. `POST /api/replay` accepts the corresponding `action_delay_ms` and `step_delay_ms` integers. Dashboard inputs persist in that browser. Application `timeout_s` stays separate. Exported ZIP runners accept `OQA_ACTION_DELAY_MS` / `OQA_STEP_DELAY_MS` and show the browser unless `OQA_HEADLESS=1`.
- `oqa live shot CASE --n N --target LOCATOR [--caption TEXT]` retains each pre-action/intermediate capture and appends the step result image on `live step`. Targets must resolve to one element, rather than silently choosing the first match. Dashboard and Excel show all images in capture order. Existing `--from-pre` and post-step supplemental captures remain available.

- Live and Replay are different results. `state.results[case]` is the live conclusion: `{status, attempts, reason?, category?}` where `category` is `application` (FAIL), `automation` or `precondition` (BLOCKED/PENDING). A replaced or reopened conclusion is kept in `state.history[case]` (`{...old result, at, superseded_by}`); evidence pictures are never deleted. `replay.json` beside `state.json` holds `{cases:{case:{latest, runs[≤20]}}}` where a run is `{status PASS|FAIL, at, run_id, prefix, reason?, failed_step?, headed, fast, action_delay_ms, step_delay_ms, timing{total_s, pause_s, call_s, calls, steps[{n, actions_s, check_s, pause_s, calls}]}}`. A case never replayed is NOT RUN. A replay never writes `state.results`. `/api/session` cases carry `live_status`, `replay_status`, `replay`, `history`, `category`; the session carries `replay_counts`. The Excel summary has Live and Replay columns and each case sheet shows Live result, kind of conclusion, Replay result with timing, and the conclusion history.
- `oqa live replay CASE|all [--fast] [--headless] [--action-delay-ms N --step-delay-ms N]`; `--fast` forces both pauses to 0. `POST /api/replay` accepts `fast: true` and `case: "all"`; the ZIP runner accepts `OQA_FAST=1`. `timing` says where a replay spent its time (viewing pauses versus calls into the browser), so a slow replay is measured, not assumed. Before judging a step a replay waits (bounded, 3 s) for the page to stop changing, so Save, Discard and onchange re-renders are complete; text is compared with whitespace normalised.
- `oqa live end CASE --status FAIL --control TEXT [--reason TEXT]`: FAIL is always `application` and needs `--control`. `--status BLOCKED|PENDING --category automation|precondition` needs an earlier `oqa live diagnose CASE --category automation|precondition --detail TEXT (--tried TEXT | --need TEXT)`, which is also posted to the shared notes for the coordinator. `oqa live handover CASE --reason TEXT [--to WORKER]` keeps the open attempt, its steps and the attempt count; the next `oqa live start CASE` continues it (`resumed: true`, `next_step`). `oqa finish CASE --status BLOCKED|PENDING --category ...` is the coordinator's equivalent.
- `oqa case context` adds `message_codes` (each ERR_xxx with the documentation line that defines it), `message_guidance`, `language` and `conclusions`.
- Fixtures: `oqa fixture key|ui|ref|cleanup`. `ui` fills the form by technical field names (`.o_field_widget[name=...]`) and saves with the form's own Save button, records `data_created` (`model, field, key`) and a replayable setup. Presets: `ledger` = license.ledger, `license_user` = license.user, `contract` = contract.ledger; `fixture_models` in workspace.json overrides them. `cleanup` lists tracked records and removes them only with `--apply`, restricted to keys that start with the test prefix; `--run rXXXX` addresses a replay's own data.

This is everything another program (the Office Partner add-in, a dashboard, a script) may rely on. Nothing else in `oqa/` is a public interface.
Every JSON file has `schema_version` (now 1). A reader that meets a larger number must stop and tell the user to update the skill.
`oqa --version` prints the CLI version. Every command accepts `--json`. Exit codes: 0 done, 1 unexpected failure, 2 refused (wrong phase, invalid input, waiting for the user).

## Home

`$ODOO_TEST_HOME`, else the folder in `~/.odoo-test.json` (`{"home": "..."}`), else `~/.odoo-test`. The packages' venv is not under it: it is always `~/.odoo-test/venv`.

```
<home>/workspaces/<ws>/workspace.json
<home>/workspaces/<ws>/knowledge/index.md
<home>/workspaces/<ws>/knowledge/sitemap.json             the screens of this Odoo and how they lead to each other (`oqa sitemap build`)
<home>/workspaces/<ws>/current                       id of the session used when none is named
<home>/workspaces/<ws>/sessions/<id>/
    state.json  cases.json  analysis.json  strategy.json  strategy.md  events.jsonl  evidence/  logs/
```

A session is chosen by `--ws` / `--session`, else `$OQA_WS` / `$OQA_SESSION` (what the add-in sets for a chat), else the workspace's `current`.

## workspace.json

`{schema_version, name, url, db, profiles:[{name, login, note}], environments:[{name, url, db, accounts:[{profile, login}]}], reference_dirs:[path], language: vi|ja|en, test_prefix}`.
Never a password: each profile's password is in the credential store under the key `oqa:<workspace>:<profile>`
(Windows Credential Manager; the `keyring` package elsewhere).

Environments: the workspace's own `url`/`db` is the environment `main`; `environments` lists the others (staging, production...) that run the same scripts. A profile is a role
shared by all environments, but each environment gives it its own login (`accounts`) and password, stored under `oqa:<workspace>:<profile>:<environment>`. A profile an environment
gives no account for fails to sign in there (it never falls back to another system's account). `workspace.for_env(ws, env)` returns the workspace as seen from one environment.

## regression.json (per workspace)

`<home>/workspaces/<ws>/regression.json` = `{schema_version, scripts:{"<workbook>/<case>":{<environment>:{latest, runs[≤20]}}}}`. A run is a replay run (`status PASS|FAIL, env, at, run_id,
reason?, failed_step?, timing, video?, source?`). A script never run on an environment is NOT RUN there. The session's own `replay.json` keeps `latest` for `main` only and `envs` per environment.
Results of runs made by hand are put here with `oqa results import <result.json | folder>`; `result.json` is written by `replay.mjs`
(`{schema, workspace, script, case, env, status, at, run_id, failed_step, reason, base_url, video, total_s, source:"manual"}`, exit code 0 PASS / 1 FAIL) and is refused when its workspace,
environment, script or status cannot be placed. Videos live in `<home>/workspaces/<ws>/videos/<script>/<env>-<run>.webm`: a step note drawn over the page and a chapter per step;
`--video off|on|fail` (`fail` keeps it only for a FAILed run; default off, `fail` for `regress`, off with `--fast`). Dashboard: tab "Scripts" (`/api/regression`, `/api/regress`, `/api/video`).

## sitemap.json

`{schema_version, meta:{generated, profile, odoo_version, menus, actions, views, object_buttons, truncated, languages?, default_language?, missing_languages?}, nodes:{key: node}, edges:[{from, to, kind, label?, name?, labels?}], unresolved:[{where, why, message?, values?, language?}]}`.
Read from Odoo's menus, actions and view definitions through read-only calls (nothing is clicked); it shows what the profile named in `meta` may see.
Node keys: `menu:<id>` · `window|server|client|url|report:<id>` (actions) · `view:<model>:<type>:<id>`. A view node carries `buttons:[{label, name, type: action|object|open|edit, invisible?, stat?}]` and `disabled:[create|edit|delete|duplicate]` (what the view switches off: no New button, read only) and `fields:[{name, label, type, required, readonly, relation?, options?, invisible?, widget?}]` (`required` / `readonly` are true, false or the expression of the view when it depends on other fields; `options` are `value=label` of a selection).
Edge kinds: `menu` (parent to child), `opens` (menu to action), `shows` (action to view), `row` (list or kanban to the form of the model), `button` (view to the action a `type="action"` button opens).
A `type="object"` button calls a method: where it leads is unknown until it is run, so it has no edge (it is counted in `meta.object_buttons`). Asked through `oqa sitemap find|buttons|path` or the MCP server `oqa mcp` (tools `sitemap_find`, `sitemap_buttons`, `sitemap_fields`, `sitemap_path`; `oqa mcp --config` prints its `.mcp.json` entry).

The builder reads active `en_US` and `ja_JP` translations using a per-call `context.lang`; it never changes the user's language or activates languages. `--languages en,ja` explicitly requires both to be active; without this option the builder uses whichever are active and reports missing languages in `meta.missing_languages`. At least one must be active.
Nodes, fields, buttons and labelled edges carry `labels:{en_US: text, ja_JP: text}` for available translations. Selection fields also carry `options_by_language:{code:[value=label]}`. The original `label` / `options` stay in the first built language (`meta.default_language`); technical keys, names, selectors and graph counts do not vary by display language.
Find, screen resolution and `--match` search every stored translation and selection label, with Unicode NFKC normalization (including fullwidth Latin and halfwidth kana) and case folding. CLI `--lang en|ja` and MCP `lang` choose both the labels and explanatory text of the response; by default use the workspace's English/Japanese setting (Vietnamese workspaces retain the existing English sitemap output). Missing label translations fall back to the original label. Older schema-1 maps remain readable; rebuild them to collect translated labels. Structured unresolved messages are translated for display; original Odoo error details are preserved.

Examples:

```sh
python oqa.py sitemap build --languages en,ja --lang ja --ws demo
python oqa.py sitemap find 注文 --lang en --ws demo
python oqa.py sitemap buttons Orders --match 確認 --lang ja --ws demo
python oqa.py sitemap fields 注文 --required --lang ja --ws demo
python oqa.py sitemap path "Cancel order" --lang ja --ws demo
```

MCP example: `sitemap_fields` with `{"screen":"Orders","match":"顧客","lang":"ja"}`. A search can use either language regardless of the output language. Labels are taken from Odoo's installed translations; untranslated Odoo terms retain Odoo's fallback text.

The progress dashboard has a workspace-level `site_map` tab. It reads the saved map (no Odoo connection or session is required) and draws an interactive SVG graph: workspace → root menus → menus → actions → views, with button and row transitions when a view is expanded. Drag to pan, scroll or use +/− to zoom, fit the graph, open/close branches, or return to the overview. A minimap shows the current viewport. Search locates a screen on the graph and highlights its known route. Menus whose parents are unavailable are kept in an explicit "Other menus" group; that display grouping never invents an Odoo navigation route. The inspector shows buttons/fields as cards and navigation as numbered steps in English/Japanese. Workspace changes clear the selected screen and filters; switching back to progress keeps the selected case. Reload refreshes the saved map after a rebuild. Workspaces without a map show an empty state.
Authenticated dashboard routes: `GET /api/workspaces` lists workspace names/languages and map availability; `GET /api/sitemap?ws=<name>&lang=en|ja&what=show|find|screen` returns summary plus compact topology (node labels, IDs, counts and edges; no field/button definitions), search results (`q`, optional `kind`, at most 60), or screen detail (`screen`, optional `view`, `match`, `required=true`). Screen details include bounded structured `controls`, `route`, `route_edges`, and `reachable`, alongside the existing CLI text. These routes use the dashboard's local token and Host/Origin checks. A direct dashboard URL can use `tab=site_map&workspace=<name>`.

## state.json

`{schema_version, id, workspace, file, created, phase, done:[{phase, at}], waiting, strategy_sha, confirmation, attempts:{case:n}, results:{case:{status, attempts}}, connect, stopped (null or {by, at}), hints:{case:[{text, at, by}]}, seen, timeouts:{case:seconds}, progress:{case:furthest failed step}, explore: "required"|"off"}`

Phases: `ANALYZE → CONNECT → CONFIRM → EXECUTE → REPORT → DONE` (INTAKE happens inside `session new`).
`waiting` is `null` or `{question, case, asked_at}`: while set, only `answer`, `status`, `ask` and reading work.
`attempts[case]` counts runs: the first plus at most 3 repairs; the next is refused. An answer about a case resets its count.

## cases.json (INTAKE)

`{schema_version, source:{file, name, sha256, sheet, header_row}, warnings:[text], cases:[{id, title, category, type, role, pre, steps:[text], expected, note, sheet, row, refs:[{kind:"file", text, status: found|missing, path}], case_refs:[id], vague:[{phrase, kind: reference|unmeasurable, where}]}]}`
`vague` = wording a person would ask about before running: a result judged against a document nobody named ("as designed", "đúng design", "仕様通り", an "attachment" with no file name), or one that cannot be measured ("works correctly", "hiển thị bình thường", "正しく", no expected result). `warnings` also says what the workbook holds that is not read as text: embedded pictures, cell comments, other sheets (`oqa ref show <workbook>` reads them).

## analysis.json (ANALYZE)

Produced by `oqa analyze --template`, completed by the assistant, accepted only by `oqa analyze --submit`. Per case:
`id, profile, verdict (ready|needs_user|blocked), clarity{score 1-5, notes}, sufficiency{score 1-5, missing}, refs[{kind:"file", text, status: ok|missing|asked|waived, path, note}], vague_resolved[{phrase, concrete, source}], data_needs[{what, fallback}], data_considered, edge_cases[] or edge_cases_none_reason, depends_on[], reuse, steps[{n, action, expected, evidence}], timeout_s (optional, 5-120, default 8), questions[] (needs_user) / reason (blocked)`.
New templates include `steps[].evidence`: a nonempty description of the state to capture and the relevant region to frame, required when the case is ready. Expand combined actions into separate numbered steps before approval. Older schema-1 analyses without the field remain accepted. See [evidence requirements](evidence.md).
`ready` needs clarity ≥ 3, sufficiency ≥ 3, no reference `missing` or `asked`, and may depend only on other `ready` cases. No cycles. What the validator refuses (every defect is listed at once):
- a reference `ok` with no file at its `path`; a `waived` one with no reason in `note`;
- a flagged phrase of `cases.json` (`vague`) with no matching `vague_resolved` entry, or whose `concrete` is itself a general phrase or shorter than 15 characters; `source` is `ref:<a reference of the case marked ok>` or `user_answer` (a result judged against a document), and for what only needs reading also `workbook` or `domain`; a guess is never a source;
- a step whose `expected` is again vague wording; a `ready` case whose `sufficiency.missing` is not matched by a `data_needs` fallback;
- `needs_user` without full-sentence `questions` and `data_considered` (what was tried first: data created, a file read, the sitemap, and why only the user can answer); `blocked` without `reason` and `data_considered` (why the data cannot be created through the UI or an import).
When a case is `needs_user` on the first submission the session stays in ANALYZE and every question is asked at once (`waiting`, case null); after the answer the template is fetched again (`_user_answers`, `_shared_files`, missing references looked up among the shared files) and the analysis handed in again.

### Case retrieval

`oqa analyze --template` includes up to two sitemap candidates and two workspace knowledge snippets per case in `_context`, subject to per-case and workbook character budgets. An omitted subsection has no match or exceeded the budget. If the workbook budget is reached, `_context_more` directs the agent to fetch context on demand. Pass `--no-context` for the skeleton alone. Context is refreshed on each request. The submitted analysis stores the resulting test plan.

- Sitemap snippets contain a node key, menu path and model when known. Compare candidate paths by node key with `oqa sitemap path <key>` and verify the actual screen before acting.
- Knowledge snippets contain workspace rules, data requirements and known pitfalls with their source. Matching uses the screen or model and terms from the case. Scoped entries can specify `action`, `condition`, `profile`, `state` and `evidence`. Use the workbook and cited sources to determine the expected outcome. A previous run is evidence of what was observed at that time.
- Fetch just one section with `oqa case context ID --only sitemap|knowledge [--screen SCREEN] [--issue TEXT]`. For knowledge, add `--profile NAME` or `--state STATE` when known. The worker MCP `context` tool accepts the same fields. Use `oqa knowledge search --screen MODEL` to see full entries.

Knowledge lives in `knowledge/index.md` in each workspace. Older entries can still be read. Check existing entries with `oqa knowledge search --screen MODEL --kind KIND` before saving a fact. Entries with the same screen, kind, action, condition, profile and state share a heading. Each fact keeps its evidence and source. `learn` reports `added` for a new scope, `updated` when facts or supporting details are added, and `duplicate` for an already recorded fact. For example:

```sh
oqa knowledge learn --screen oqa.harness.request --kind rule --action "lưu" \
  --condition "item khác site" --fact "Request phải từ chối item khác site" \
  --evidence "Thông báo lỗi hiển thị và dòng sai chưa được lưu" --ref "Request design §4.2"
```

## refs.json (files the user points at)

`{schema_version, items:[{path, name, kind: file|folder, by: user|answer|terminal, case, added}]}`. The user pastes a PATH (the dashboard's *Tệp tham chiếu* box, or a path written inside an answer, or `oqa ref add`): nothing is uploaded or copied, the assistant reads the file where it is with `oqa ref show`. Paths under a session's `auth` folder, or naming `storageState` / credentials, are refused. Scripts reach them with `o.ref(name)`.

## strategy.json / strategy.md (CONFIRM)

`strategy.json = {schema_version, order:[case], excluded:[{id, verdict, reason}], analysis_sha, sha}`. `strategy.md` is the same for people.
`confirm` succeeds only if `sha` is intact and matches `analysis.json`: the user approves exactly what will run.
**Only a person confirms**: `oqa confirm` needs a real terminal (the assistant's shell is not one), or the dashboard's Confirm button.

## events.jsonl

One JSON object per line, appended in order; each has `ts` and `kind`. Readers skip a line they cannot parse (it may be half written).

| kind | fields |
|---|---|
| session_started | workspace, file |
| phase | phase, text |
| ask / answered | question / answer, [case], [files: paths written in the answer, now references] |
| ref_added | path, by, [name, what: file\|folder, case]: the user shared a file by its path (the assistant is told by `oqa wait`) |
| confirmed | by, strategy_sha |
| case_planned | case, title, steps[{n, action, expected}], order |
| stopped / resumed | by (the user's Stop and Resume) |
| hint | case, text (the user's advice; the case goes back in the queue with a fresh budget) |
| attempt_finished | case, attempt, status PASS\|FAIL\|STOPPED, reason, [failed_step, script_sha] |
| repair | case, attempt (the failed run), cause, evidence, change, [reanalysis, probed, script_sha, timeout_s, unchanged_reason]: the assistant's note after a failed run; the next run of the case needs one |
| probe | url, count, [case, filter]: the assistant looked at the real page |
| explore | case, step, ok, [code, url, error]: the assistant tried code for a step on the real page |
| case_started | case, attempt, [from_step]: `from_step` > 1 when the run picked up at a step that failed before (the steps before it are carried from the earlier run) |
| step | case, attempt, n, status PASS\|FAIL\|SKIPPED, caption, expected, actual, [checks], images[{raw, annotated}] (`checks` = how many checks of the expected result ran; a PASS step needs at least one) |
| case_finished | case, status PASS\|FAIL\|PENDING\|BLOCKED\|NOT EXECUTED, attempts |
| session_finished | counts |

A `step` with status PASS or FAIL **must** carry at least one image pair (`raw` and `annotated`, the red-box one). The writer refuses anything else.

## knowledge/index.md

`- [screen:<screen>] [kind:<kind>] [tag:<word>] <fact> -> <ref or ->`, one line per fact, newest last.
kind: login | navigation | locator | data | error | fix | quirk | rule. Never a password, token or cookie.

## Commands (this version)

| command | what it does |
|---|---|
| `init [--ws N] [--timeout S] [--no-open] [--status]` | serves the configuration form on 127.0.0.1 and **blocks until it is saved** (exit 0, summary without secrets) or times out (exit 2, nothing written). Run it in the background; `--status` only reports whether a workspace is configured |
| `regress --env E [--cases C] [--headed] [--video off\|on\|fail] [--fast]` / `regress --matrix` | replay every saved script of the workspace on environment E (exit 1 when a script fails) / show the latest result of each script on each environment |
| `results import <result.json\|folder>` | put the `result.json` of runs made by hand (`node replay.mjs`) onto the dashboard |
| `live replay CASE\|all [--env E] [--video off\|on\|fail]` | replay on another environment, optionally with a noted video |
| `doctor [--fix]` | checks Python, packages, browser, credential store; `--fix` installs what is missing |
| `ws show\|list` | the workspace (no secrets: each profile says whether a password is saved) |
| `session new --file F` / `show` / `list` | INTAKE: read the workbook → `cases.json`, session starts in ANALYZE |
| `analyze --template [--out F]` / `--submit F` | the skeleton to fill / hand in the analysis (validated; ANALYZE → CONNECT) |
| `strategy` | order the ready cases, write `strategy.json` and `strategy.md` (phase CONFIRM) |
| `confirm` | the user approves (terminal only); CONFIRM → EXECUTE |
| `ask "Q" [--case ID]` / `answer "text"` | wait for the user / record their reply (a path written in it becomes a reference) |
| `ref add PATH [--case ID]` / `ref list` / `ref show NAME [--sheet S] [--max N]` | the files and folders the user pointed at: remember one, list them, read one as text (xlsx cells and comments, docx, pptx, csv, txt…; embedded pictures are saved and listed so they can be opened with the Read tool; a PDF or picture is named for the Read tool). Also reads the testcase workbook itself |
| `wait [--timeout S]` | blocks until the user acts on the dashboard (confirm, answer, hint, stop, resume), then says what; run it in the background |
| `stop` / `resume` | the USER stops the test (the step in progress finishes) / lets it go on (`resume` needs a real terminal); also buttons in the dashboard |
| `repair ID --cause --evidence --change [--reanalysis] [--timeout S] [--unchanged why]` | after a failed run: record what was found and changed; `run` refuses a rerun without it |
| `probe [--case ID] [--filter T] [--failed]` | what can be clicked or filled on a page; `--failed` reads the page saved when a step failed (no browser); logged as a `probe` event |
| `explore ID [--to N]` / `try ID --step N --code C [--file F] [--reset] [--undo] [--filter T]` | open the page where step N begins / run code for a step on it; kept in `<script>.recipe.json` only when it worked, no attempt used. Needs the runner |
| `build ID [--force]` | write the case script from the verified pieces, sealed with `# oqa-built: <digest>`; `run` refuses a script that is not sealed (session `explore: "required"`) |
| `run [ID...] [--from N] [--fresh] [--inline]` | run in the runner (it keeps the browser between runs; `--inline` runs and closes in this process); a failed case picks up at the step that failed |
| `runner status\|stop` | the background process that keeps the browser (`runner.json` in the session folder holds its address and token) |
| `status` | phase, progress, the next command, cases whose failure is not explained yet |
| `knowledge search\|learn` | look up / add what was learned |

The other commands (`connect`, `assign`, `claim`, `claims`, `release`, `note`, `notes`, `finish`, `sitemap`, `lookup`, `mcp`, `report`, `dashboard`) are listed by `oqa --help`.
