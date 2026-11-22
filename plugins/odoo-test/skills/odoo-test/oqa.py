"""Entry point: `python <skill folder>/oqa.py <command>` works from any folder, so the assistant never has to cd or set PYTHONPATH.

The packages (openpyxl, playwright, pillow) live in ~/.odoo-test/venv (always that path): when this was started with another
python, run again with the venv's python so nobody has to know or report a missing environment."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _venv_python():
    root = Path.home() / ".odoo-test" / "venv"  # fixed: does not follow $ODOO_TEST_HOME
    exe = root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return root, exe


if not os.environ.get("OQA_NO_VENV"):
    try:
        _root, _exe = _venv_python()
        if _exe.exists() and Path(sys.prefix).resolve() != _root.resolve():
            os.environ["OQA_NO_VENV"] = "1"
            os.execv(str(_exe), [str(_exe), str(Path(__file__).resolve()), *sys.argv[1:]])
    except OSError:
        pass

from oqa.cli import main  # noqa: E402

sys.exit(main())
