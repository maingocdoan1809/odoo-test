# odoo-test

Edit the skill at its source in this repository. The shipped files live in `SKILL.md`, `oqa.py`, `oqa/`, `hooks/`, and `references/`.

Put POC code, plans, audit prompts, dashboard previews, and other experimental artifacts in `temp/`. Keep regression tests in `tests/` and the Odoo acceptance suite in `harness/`.

Run `python -m pytest` to test changes. Use `python oqa.py publish` to deploy the skill and rebuild the Claude Code plugin using the packaging rules in `oqa/plugin.py`. Use Git history to recover earlier versions.
