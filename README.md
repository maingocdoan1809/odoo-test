# odoo-test

Test an Odoo system from a testcase workbook: plan, run every case in a browser with red-framed screenshots, repair, and write the Excel evidence. Needs Python 3.9+; the first run sets up its own environment (`oqa doctor --fix`).

This repository is both a Claude Code plugin marketplace and a plain skill. The repository is private: sign in to GitHub (`gh auth login`) before installing.

## Install as a plugin (recommended: its hooks are active from the first message)

```
/plugin marketplace add maingocdoan1809/odoo-test
/plugin install odoo-test@odoo-test
```

Update later with `/plugin marketplace update odoo-test`, then `/plugin update odoo-test@odoo-test`.

## Install as a plain skill

```
git clone https://github.com/maingocdoan1809/odoo-test.git ~/.agents/skills/odoo-test
```

The skill carries its own hooks, which run only once the skill is loaded.

## Develop

Edit `SKILL.md`, `oqa.py`, `oqa/`, `hooks/` and `references/`. Run `python -m pytest`.

- `python oqa.py publish` replaces the deployed skill in `~/.agents/skills/odoo-test` and rebuilds the local plugin in `~/.odoo-test/plugin`.
- `python oqa.py repo` refreshes `plugins/odoo-test/` and `.claude-plugin/marketplace.json`, the plugin GitHub users install. Commit them with the change (a test fails when they are stale).
