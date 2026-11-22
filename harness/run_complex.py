"""The ten-case complex suite (imports with several rounds, exports, two roles, boundary values) through the real Claude worker queue.
Same flow as run_matrix.py; the harness persona approves the strategy, and a case that is not PASS is a reported result (some are real defects of the harness module)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_matrix  # noqa: E402

NOTES = """Plan every Odoo testcase below for real browser execution. Use concise English.
Preserve each testcase's complete intent. Use six to ten meaningful evidence steps per case; one meaningful UI action or checked state per step.
The expected text must describe a specific observable state, value or error. Testcases run independently, in the order given.
Create fixture records for mutations with the keys fixture(action=key) returns; never write a literal record name or code in a step, say "a site with this run's own key". Each run prepares its own data: do not depend on records an earlier run may have left. fixture apply can prepare related records
using {"$fixture":"<key>"}; verify the application under test through the UI. Do not skip steps.
CSV references (t01 to t07) hold the tokens {run}, {site}, {site2} and {existing}: upload_csv fills them with this run's own values. For a later round of the same import
(fix a cell and upload again) upload_csv takes overrides: [{row, column, value}] applied on top of the approved file, so each round is one upload.
Include every failure and recovery the testcase specifies. T07 and T09 use Operator and Manager and must re-find the case's own record after switching accounts.
Report what the application really does: an unreadable system error, a missing menu or a rejected action is a finding, never something to work around.
"""

if __name__ == "__main__":
    here = Path(__file__).resolve().parent / "complex-suite"
    run_matrix.run({"workspace": "harness-complex", "workbook": "odoo-harness-complex-10.xlsx", "source": "complex-suite/odoo-harness-complex-10.xlsx",
                    "fixtures": here / "fixtures", "notes": NOTES, "strict": False, "timeout": 900})
