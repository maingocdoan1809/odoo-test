"""A session-scoped packet for a tester or coordinator."""
import re
from pathlib import Path

from . import captions, claims, events, memory, record_names, refs, retrieval, workspace
from .state import GateError
from .store import read_json


CODE = re.compile(r"\b(?:ERR|MSG|WARN)[_-]?\d+\b", re.I)
GUIDE = ("Message codes such as ERR_015 name a message in the design document; the application shows only the message text, in the user's language, and never the code. "
         "Pass when the text on screen matches the documented meaning (use `message` below), not when the literal code appears. A message that differs in meaning is a FAIL.")


def messages(session, texts, references):
    """For each message code the case mentions: the documentation line that defines it (read from the shared reference files), so the worker matches the text, not the code."""
    codes = sorted({code.upper().replace("-", "_") for text in texts for code in CODE.findall(text or "")})
    if not codes:
        return []
    files = []
    for ref in references:
        base = Path(ref["path"]) if ref.get("path") else None
        if base and base.is_dir():
            files += [(ref.get("name") or base.name, item) for item in sorted(base.iterdir()) if item.is_file() and item.suffix.lower() in (".xlsx", ".xlsm", ".docx", ".md", ".txt", ".csv")]
        elif base:
            files.append((ref.get("name") or base.name, base))
    files.sort(key=lambda entry: "message" not in entry[1].name.lower())        # the message list first: that is where a code is defined
    bodies, found = {}, {}
    for code in codes:
        pattern = re.compile(code.replace("_", "[_-]?"), re.I)
        for name, path in files[:40]:
            if path.name.startswith("~$") or ".result-" in path.name:
                continue
            if path not in bodies:
                try:
                    bodies[path] = refs.describe(path, 400000, None, session.dir / "refs") or ""
                except Exception:                                # an unreadable reference must not break the packet
                    bodies[path] = ""
            hit = pattern.search(bodies[path])
            if hit:
                found[code] = {"source": path.name, "text": " ".join(bodies[path][max(0, hit.start() - 60): hit.end() + 260].split())}
                break
    return [{"code": code, **found.get(code, {"source": "", "text": "not found in the shared references: ask the coordinator, do not guess"})} for code in codes]


def build(session, case_id, screen="", issue="", *, only="", profile="", state=""):
    if only not in ("", "sitemap", "knowledge"):
        raise GateError("Case context section must be sitemap or knowledge.")
    original = next((item for item in session.cases()["cases"] if item["id"] == case_id), None)
    if original is None:
        raise GateError(f"Case {case_id} is not in this session.")
    analysis = read_json(session.dir / "analysis.json", {}) or {}
    assessed = next((item for item in analysis.get("cases", []) if item["id"] == case_id), None)
    config = workspace.load(session.ws)
    retriever = retrieval.WorkspaceContext(session.ws, config.get("language", "en"), only=only)
    context = retriever.lookup(
        original, assessed=assessed, screen=screen, issue=issue,
        profile=profile or (assessed or {}).get("profile") or original.get("role")
        or (config["profiles"][0]["name"] if len(config["profiles"]) == 1 else ""),
        state=state, only=only, detail=bool(only or screen or issue))
    if only:
        result = {"case": case_id, only: context.get(only, [])}
        if only in retriever.unavailable:
            result["unavailable"] = retriever.unavailable[only]
        return result
    state = session.state()
    history = events.read(session.dir)
    prefix = config["test_prefix"]
    translation = captions.localized_cases(session).get(case_id, {})
    references = [ref for ref in refs.items(session) if ref.get("case") in (None, "", case_id)]
    texts = [original.get("expected", ""), original.get("note", ""), original.get("pre", ""), *original.get("steps", []),
             *[f"{step.get('action', '')} {step.get('expected', '')}" for step in (assessed or {}).get("steps", [])]]
    return {
        "session": session.id,
        "workspace": session.ws,
        "phase": state["phase"],
        "title": record_names.title(original, assessed or {}, translation),
        "fixture_prefix": record_names.fixture_prefix(session, case_id, prefix),
        "original": original,
        "analysis": assessed,
        "feedback": [e for e in history if e.get("kind") == "answered"
                     and e.get("case") in (None, case_id)][-8:],
        "references": references,
        "message_codes": messages(session, texts, references),
        "message_guidance": GUIDE,
        "language": "vi",
        "evidence_language": config.get("evidence_language", "vi"),
        "conclusions": {"result_history": (state.get("history") or {}).get(case_id, []),
                        "rule": "FAIL = the application differs from the expectation (verify the operated control first). Automation problems and missing preconditions are BLOCKED/PENDING after `diagnose`."},
        "sitemap": context.get("sitemap", []),
        "knowledge": context.get("knowledge", []),
        **({"context_unavailable": retriever.unavailable} if retriever.unavailable else {}),
        "previous": memory.recall(session.ws, memory.stem_of(state["file"]), case_id),
        "notes": [note for note in claims.notes(session) if note["case"] in ("", case_id)][-8:],
        "recent": [e for e in history if e.get("case") == case_id
                   and e.get("kind") in ("step", "hint", "case_finished", "repair")][-8:],
    }
