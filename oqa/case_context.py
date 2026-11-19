"""A session-scoped packet for a tester or coordinator."""
import re

from . import claims, events, knowledge, memory, refs, sitemap, workspace
from .state import GateError
from .store import read_json


def build(session, case_id, screen="", issue=""):
    original = next((item for item in session.cases()["cases"] if item["id"] == case_id), None)
    if original is None:
        raise GateError(f"Case {case_id} is not in this session.")
    analysis = read_json(session.dir / "analysis.json", {}) or {}
    assessed = next((item for item in analysis.get("cases", []) if item["id"] == case_id), None)
    state = session.state()
    history = events.read(session.dir)
    query = screen or issue or original["title"]
    prefix = workspace.load(session.ws)["test_prefix"]
    safe_case = re.sub(r"[^A-Za-z0-9_-]", "_", case_id)
    screens = []
    if sitemap.file_of(session.ws).exists():
        for key, node, match in sitemap.find(sitemap.load(session.ws), query, limit=5):
            screens.append({
                "key": key, "label": node.get("label"), "model": node.get("model"),
                "match": match,
            })
    return {
        "session": session.id,
        "workspace": session.ws,
        "phase": state["phase"],
        "fixture_prefix": f"{prefix}{session.id}_{safe_case}_",
        "original": original,
        "analysis": assessed,
        "feedback": [e for e in history if e.get("kind") == "answered"
                     and e.get("case") in (None, case_id)][-8:],
        "references": [ref for ref in refs.items(session) if ref.get("case") in (None, "", case_id)],
        "sitemap": screens,
        "knowledge": knowledge.related(session.ws, f"{query} {issue}"),
        "previous": memory.recall(session.ws, memory.stem_of(state["file"]), case_id),
        "notes": [note for note in claims.notes(session) if note["case"] in ("", case_id)][-8:],
        "recent": [e for e in history if e.get("case") == case_id
                   and e.get("kind") in ("step", "hint", "case_finished", "repair")][-8:],
    }
