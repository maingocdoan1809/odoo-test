# odoo-test (skill `oqa`)

This repository is the source of truth. Edit `SKILL.md`, `oqa.py`, `oqa/`, `hooks/`, and `references/` here. Put POC code, plans, audit prompts, previews, and other experimental artifacts in `temp/`. Keep regression tests in `tests/` and the Odoo acceptance suite in `harness/`.

Run `python -m pytest` to test changes. Run `python oqa.py publish` from the repository to replace the deployed skill in `~/.agents/skills/odoo-test` and rebuild the Claude Code plugin in `~/.odoo-test/plugin`. The publish command packages only the skill files. Run `python oqa.py repo` to refresh `plugins/odoo-test/` and `.claude-plugin/marketplace.json`, which users install from GitHub; commit them with the change. Use Git history to recover earlier versions.

Make changes in this repository so the next publish includes them. Open a new Claude Code window to load the published version.

Keep the stop hook idempotent. The same event can reach it through the skill and through Claude Code's plugin hooks.
