---
name: odoo-test
description: Run Odoo testcase workbooks with workspace selection, per-case review, live Playwright evidence, saved replay scripts and Excel results. Use for Odoo testcase requests or /odoo-test.
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

Use `python "<folder containing this SKILL.md>/oqa.py" <command>`, abbreviated as `oqa`. Keep the workspace and session ID from intake and pass them explicitly to worker commands. Use the workspace language.

## Workbook run

1. Run `oqa run start --file <workbook> [--cases <selection>]` as a background task. The command opens the workspace picker and returns once the user saves. Use `--channel addin` when invoked from Excel. If a specific dependency is missing, check `oqa doctor`. Install Claude hooks only as platform setup.
2. Get `oqa analyze --template`. Review testcase intent, references, observable results, permissions and dependencies. For cases that change a record, plan to create test records and use their run-specific identifiers. Add comparison records when required by the assertion. Visual-only checks use the existing UI unless a data-dependent state must be reached. Use `oqa case context ID [--screen <name>] [--issue <text>]` for the original case, assessment, feedback, reference paths, sitemap hits, history and knowledge.
3. Submit with `oqa analyze --submit <file>`. The dashboard shows the original and assessed cases. Each case needing input has its own question and skip button. Monitor with `oqa wait --until reviews`. When reviews finish, fetch the template again, read feedback/references and resubmit. Connect with `oqa connect`, build the final scope with `oqa strategy`, and monitor user approval with `oqa wait`. The user approves on the dashboard.
4. Run `oqa dispatch` in the background when using the Claude worker backend. It leases up to five cases to Haiku and consults Sonnet for recovery. For host-managed workers, assign ready cases with `oqa claim --agent WORKER --case ID --packet`. Run dependent cases after their prerequisites pass. Use [worker instructions](references/agents.md). If a host worker stopped, use `oqa live recover ID --reason <diagnosis>` and reassign once. Use `oqa fixture plan <screen>` for fields, route and example commands. Exchange requests and results with `oqa note` and `oqa notes`.
5. A worker opens its signed-in browser with `oqa live start ID`, reads the live UI, acts, and records the outcome of each planned step with `oqa live step`. Evidence has a visible stable target. For a check that an element is absent, frame its parent view. If navigation removes the evidence target, capture it first with `oqa live shot`. To assemble a replay script automatically, include `--verified-code <file> --shows <expected visible text>` per step. Include only actions chosen for the final path. Use observable waits. Finish with `oqa live end ID --status <status>`.
6. Run `oqa report` and return the result path. Path runs save a copy beside the source. Add-in runs write into the matching workbook open in desktop Excel on Windows. The dashboard can download reports and a ZIP with runnable scripts and replay saved cases.

Public formats: [contract](references/contract.md). Odoo conventions: [Odoo reference](references/odoo.md).
