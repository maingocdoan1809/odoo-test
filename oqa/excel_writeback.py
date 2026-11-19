"""Apply an OQA report to a workbook open in desktop Excel on Windows."""
import json
import os
import subprocess
from pathlib import Path


def apply(source, staged, patch, folder):
    if os.name != "nt":
        return False, "Live write-back requires Windows desktop Excel."
    request = folder / "excel-writeback.json"
    request.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
             str(Path(__file__).with_name("excel_writeback.ps1")),
             "-WorkbookPath", str(source), "-ReportPath", str(staged), "-PatchPath", str(request)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    return result.returncode == 0, result.stderr.strip()
