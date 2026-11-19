# odoo-test (skill `oqa`)

**This folder is the source of truth.** When asked to change the skill (SKILL.md, `oqa/`, `hooks/`, `references/`), change it HERE, run the tests, and only then put it where the agent reads it:

    python3 oqa.py publish        # skill folder (~/.agents/skills/odoo-test, old one backed up in ~/.odoo-test/backup) + plugin (~/.odoo-test/plugin)

Never edit `~/.agents/skills/odoo-test`, `~/.claude/skills/odoo-test` or `~/.odoo-test/plugin` by hand: `publish` overwrites them. A new Claude Code window is needed to load the published version.

- Tests: `~/.odoo-test/venv/bin/python -m pytest -q` (about 6 minutes; `tests/test_hooks.py tests/test_plugin.py tests/test_library.py` run in seconds).
- The skill's frontmatter hooks and the plugin's hooks both exist: hooks must tolerate running twice (see `hooks/stop.py`).
- This file is not copied into the skill or the plugin.
