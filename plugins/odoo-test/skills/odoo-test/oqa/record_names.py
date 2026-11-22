"""Business names for AI-authored cases and run-owned Odoo records."""
import re
import unicodedata

from . import captions, workspace
from .store import read_json


def slug(text, limit=32):
    plain = unicodedata.normalize("NFKD", text.lower().replace("đ", "d"))
    ascii_text = plain.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if limit is not None and len(name) > limit:
        return name[:limit + 1].rsplit("-", 1)[0] or name[:limit]
    return name


def title(case, plan, translation):
    authored = (translation.get("title") or plan.get("title") or "").strip()
    if authored:
        return authored
    original = case.get("title", "").strip()
    if original and original != case["id"] and not re.fullmatch(r"(?:test\s*case|case|tc|h)?[\s_-]*\d+", original, re.I):
        return original
    return next((step["action"] for step in plan.get("steps", []) if step.get("action")),
                next(iter(case.get("steps", [])), original or case["id"]))


def case_slug(session, case, plan=None, translation=None):
    if plan is None:
        analysis = read_json(session.dir / "analysis.json", {}) or {}
        plan = next((item for item in analysis.get("cases", []) if item["id"] == case), {})
    if translation is None:
        translation = captions.localized_cases(session).get(case, {})
    original = next((item for item in session.cases()["cases"] if item["id"] == case), {"id": case})
    name = translation.get("fixture_name") or plan.get("fixture_name") or title(original, plan, translation)
    return slug(name) or slug(case) or "testcase"


def fixture_prefix(session, case, prefix, *, name=None):
    name = name if name is not None else case_slug(session, case)
    ident = re.sub(r"[^A-Za-z0-9-]+", "_", case).strip("_") or "case"
    return f"{prefix}{name}_{ident}_{session.id}_"


def prefixes(prefix):
    if prefix == workspace.DEFAULT_TEST_PREFIX:
        return (prefix, workspace.LEGACY_TEST_PREFIX)
    return (prefix,)


def owns(session, value, prefix, case=None):
    if not isinstance(value, str):
        return False
    if case is not None and value.startswith(fixture_prefix(session, case, prefix)):
        return True
    return any(value.startswith(f"{previous}{session.id}_") for previous in prefixes(prefix))


def foreign(session, case, text, prefix):
    """Test-record names in `text` (an action about to be run) that are not this run's own: a name typed literally ("ZZ_AUTOTEST_site", "AUTO_TEST_site") is the same on every run,
    so it meets whatever an earlier run left (a unique constraint, a record that cannot be deleted) and the case judges the leftover instead of the application."""
    pattern = re.compile("|".join([r"[A-Za-z0-9_]*AUTO_?TEST[A-Za-z0-9_-]*"] + [re.escape(p) + r"[A-Za-z0-9_-]*" for p in prefixes(prefix)]), re.I)
    return sorted({token for token in pattern.findall(text or "") if not owns(session, token, prefix, case)})


def replay_sources(session, case, prefix, *, name=None):
    return (fixture_prefix(session, case, prefix, name=name), *prefixes(prefix))


def replay_stem(prefix, name):
    return f"{prefix}{name}_"


def rewriter(sources, target):
    pattern = re.compile("|".join(re.escape(value) for value in sources))
    return lambda text: pattern.sub(lambda match: target, text)
