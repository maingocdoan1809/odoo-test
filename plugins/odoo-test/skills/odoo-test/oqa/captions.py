"""Language for text added to evidence and replay, separate from the testcase."""
from .store import read_json

LANGUAGES = ("vi", "en", "ja")
STEP = {"vi": "Bước", "en": "Step", "ja": "手順"}
STATES = {"vi": {"PASS": "Đạt", "FAIL": "Không đạt"},
          "en": {"PASS": "PASS", "FAIL": "FAIL"},
          "ja": {"PASS": "合格", "FAIL": "不合格"}}


def text(step, language, *, total=None, status=None, number=None):
    n = number if number is not None else step["n"]
    label = f"{STEP[language]} {n}" + (f"/{total}" if total is not None else "")
    if status:
        label += f" · {STATES[language].get(status, status)}"
    names = step.get("captions") or {}
    action = names.get(language) or (step.get("action") if language == "vi" else "")
    return f"{label} — {action}" if action else label


def localized_cases(session):
    """An optional display translation of saved records. Original plans, scripts and evidence stay intact."""
    return (read_json(session.dir / "case-translations.json", {}) or {}).get("cases", {})


def localized_steps(steps, translation):
    by_number = {step["n"]: step for step in translation.get("steps", [])}
    return [{**step, **by_number.get(step["n"], {})} for step in steps]
