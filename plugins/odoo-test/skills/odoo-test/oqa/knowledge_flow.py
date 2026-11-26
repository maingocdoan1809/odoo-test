"""What a run teaches the workspace, and what a run already knows.

Before the workers start: one cheap agent reads the knowledge index and the cases and names, by meaning, the facts each case needs (`brief` -> `pick`); those facts go into the case
packet. After the last case: a cheap agent drafts the lessons of the run from what the workers reported (`digest` -> `draft`), the user edits them on the dashboard and saves them to
the workspace's own knowledge or to the knowledge every workspace shares (`save`), or skips. oqa starts no model: it only builds the briefs and checks what comes back.

session/knowledge_picks.json  = {case: [fact id]}
session/knowledge_draft.json  = {status: drafting|ready|empty|saved|skipped, items: [{screen, kind, fact, action, condition, profile, state, evidence, ref, scope, case}], at}"""
import time

from . import claims, events, knowledge
from .store import read_json, write_json

NEEDS = knowledge.BUSINESS_KINDS
DRAFT_FIELDS = ("screen", "kind", "fact", "action", "condition", "profile", "state", "evidence", "ref", "tag")
SETTLED = ("saved", "skipped", "empty")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _picks_file(session):
    return session.dir / "knowledge_picks.json"


def _draft_file(session):
    return session.dir / "knowledge_draft.json"


# --- before the workers: pick what each case needs ------------------------------------------------------------------------------------

def candidates(session):
    return [item for item in knowledge.everything(session.ws) if item["kind"] in NEEDS]


def _case_lines(session, cases):
    analysis = read_json(session.dir / "analysis.json", {}) or {}
    assessed = {item["id"]: item for item in analysis.get("cases", [])}
    originals = {item["id"]: item for item in session.cases()["cases"]}
    lines = []
    for case in cases:
        item = assessed.get(case) or originals.get(case) or {}
        steps = [f"{s.get('action', '')} -> {s.get('expected', '')}" for s in item.get("steps", []) if isinstance(s, dict)] or originals.get(case, {}).get("steps", [])
        lines.append(f"{case} | {item.get('title') or originals.get(case, {}).get('title', '')} | profile {item.get('profile') or originals.get(case, {}).get('role', '')}\n  " + "\n  ".join(map(str, steps)))
    return lines


def needs_picks(session, cases):
    """True when there are facts to choose from and some of `cases` has not had its facts chosen yet."""
    if not candidates(session):
        return False
    done = read_json(_picks_file(session), {}) or {}
    return any(case not in done for case in cases)


def brief(session, cases):
    """The brief for ONE cheap agent: pick, for each case, the facts it needs. None when the workspace knows nothing worth choosing from."""
    found = candidates(session)
    if not found:
        return None
    from .dispatch import _oqa_preamble

    target = session.dir / "knowledge_pick_request.json"
    return (f"You choose which known facts each Odoo testcase needs (oqa session {session.id}, workspace {session.ws}). Pick BY MEANING, not by shared words: a fact is needed when the case "
            "would otherwise repeat a mistake it describes (a control that behaves differently, a rule, a data requirement, an error and its fix). Pick nothing for a case that needs nothing. "
            "At most 6 facts per case.\n"
            f"{_oqa_preamble(session)}\n\nKnown facts (id | scope | screen | kind | conditions | fact):\n" + "\n".join(knowledge.compact_index(session.ws)) +
            "\n\nCases:\n" + "\n".join(_case_lines(session, cases)) +
            f"\n\nWrite ONE JSON object {{\"<case id>\": [\"<fact id>\", ...]}} (every case above is a key) to {target}, then run: oqa knowledge pick --file {target}\nReply with one line.")


def pick(session, data):
    """Remember which facts each case needs. Unknown ids are dropped; a case with no pick is recorded with an empty list (it was looked at)."""
    if not isinstance(data, dict):
        raise knowledge.KnowledgeError('the picks must be a JSON object {"<case>": ["<fact id>", ...]}')
    valid = {item["id"] for item in knowledge.everything(session.ws)}
    now = read_json(_picks_file(session), {}) or {}
    for case, ids in data.items():
        now[str(case)] = [i for i in dict.fromkeys(ids if isinstance(ids, list) else []) if i in valid][:6]
    write_json(_picks_file(session), now)
    events.append(session.dir, "knowledge_picked", cases=sorted(data))
    return {case: now[case] for case in data}


def picked(session, case):
    """The facts chosen for `case` (those still in the index), best first."""
    ids = (read_json(_picks_file(session), {}) or {}).get(case, [])
    by_id = {item["id"]: item for item in knowledge.everything(session.ws)}
    return [by_id[i] for i in ids if i in by_id]


# --- after the last case: what the run taught ----------------------------------------------------------------------------------------

def materials(session):
    """What the workers and the user said that might be worth keeping: notes, diagnoses, handovers, repairs, hints, and why a case did not pass."""
    lines = []
    for e in events.read(session.dir):
        kind, case = e.get("kind"), e.get("case") or ""
        if kind == "diagnosis":
            lines.append(f"{case}: worker diagnosis ({e.get('category')}): {e.get('detail')}")
        elif kind == "handover":
            lines.append(f"{case}: worker handed over: {e.get('reason')}")
        elif kind == "repair":
            lines.append(f"{case}: repair: cause {e.get('cause')}; change {e.get('change')}")
        elif kind == "hint":
            lines.append(f"{case}: user hint: {e.get('text')}")
        elif kind == "answered" and e.get("answer"):
            lines.append(f"{case or 'all'}: user answer: {e.get('answer')}")
    for note in claims.notes(session, 80):
        lines.append(f"{note.get('case') or 'all'}: note by {note.get('agent') or 'coordinator'}: {note.get('text')}")
    state = session.state()
    for case, result in (state.get("results") or {}).items():
        if result.get("status") != "PASS":
            lines.append(f"{case}: {result.get('status')} ({result.get('category', '')}): {result.get('reason', '')}")
        elif (state.get("attempts") or {}).get(case, 1) > 1:
            lines.append(f"{case}: PASS after {state['attempts'][case]} attempts")
    return [line for line in dict.fromkeys(" ".join(l.split()) for l in lines) if line.strip(": ")]


def current(session):
    return read_json(_draft_file(session), None)


def pending(session):
    """Why the session must stay open for the knowledge summary, or '' when nothing is owed."""
    draft = current(session)
    if draft is None:
        return "The knowledge summary was not made: run oqa knowledge digest, give its brief to one cheap agent, then oqa knowledge draft." if materials(session) else ""
    if draft["status"] in SETTLED:
        return ""
    if draft["status"] == "drafting":
        return "The knowledge summary is being drafted: have the agent finish with oqa knowledge draft --file."
    return "The user has not saved or skipped the knowledge summary on the dashboard: run oqa wait."


def digest(session):
    """The brief for ONE cheap agent that drafts the lessons of this run. None (and the summary is settled as empty) when the run left nothing to learn from."""
    lines = materials(session)
    if not lines:
        write_json(_draft_file(session), {"status": "empty", "items": [], "at": _now()})
        events.append(session.dir, "knowledge_drafted", count=0)
        return None
    from .dispatch import _oqa_preamble

    write_json(_draft_file(session), {"status": "drafting", "items": [], "at": _now()})
    target = session.dir / "knowledge_draft_request.json"
    return (f"You write the knowledge summary of a finished Odoo test run (oqa session {session.id}, workspace {session.ws}). Keep only what a FUTURE run on this Odoo would need and could not guess: "
            "how a control really behaves, a rule the design states, data a case needs, an error and the fix that worked, a locator that worked. evidence (optional) = what a run should observe or capture to check the fact, NOT where it came from (that is ref). Do NOT write results (PASS or FAIL of a case), "
            "one-off events, anything already known, or any password, token or cookie. Verified facts only; each fact is one sentence in Vietnamese (Odoo text stays as shown).\n"
            f"kind is one of {', '.join(knowledge.KINDS)}; screen is the Odoo model or the menu path; condition/action/profile/state say WHEN it holds (leave empty when always). "
            "scope is \"workspace\" for what is true of this Odoo or its data, \"shared\" for what is true of any Odoo (how a standard control behaves). ref names where it comes from (a design section, "
            "a case id). The user reviews every item, so prefer fewer, better ones; an empty list is a good answer.\n"
            f"{_oqa_preamble(session)}\n\nKnown already (id | scope | screen | kind | conditions | fact):\n" + "\n".join(knowledge.compact_index(session.ws, kinds=knowledge.KINDS)) +
            "\n\nWhat happened in this run:\n- " + "\n- ".join(lines) +
            f"\n\nWrite ONE JSON array [{{\"screen\", \"kind\", \"fact\", \"action\", \"condition\", \"profile\", \"state\", \"evidence\", \"ref\", \"scope\", \"case\"}}] to {target}, then run: "
            f"oqa knowledge draft --file {target}\nReply with one line.")


def _item(raw):
    if not isinstance(raw, dict):
        raise knowledge.KnowledgeError("each item must be an object")
    clean = knowledge._clean(raw.get("screen"), raw.get("kind"), raw.get("fact"), raw.get("tag", ""), raw.get("ref") or "-", action=raw.get("action"), condition=raw.get("condition"),
                             profile=raw.get("profile"), state=raw.get("state"), evidence=raw.get("evidence"))
    scope = raw.get("scope") if raw.get("scope") in knowledge.SCOPES else knowledge.WORKSPACE
    return {**clean, "scope": scope, "case": str(raw.get("case") or "")}


def draft(session, items):
    """The agent's draft. Every item is checked; one refused item refuses the file, with the reason, so the agent can fix it."""
    if not isinstance(items, list):
        raise knowledge.KnowledgeError("the draft must be a JSON array of items")
    problems, clean = [], []
    for number, raw in enumerate(items, 1):
        try:
            clean.append(_item(raw))
        except knowledge.KnowledgeError as error:
            problems.append(f"item {number}: {error}")
    if problems:
        raise knowledge.KnowledgeError("; ".join(problems))
    status = "ready" if clean else "empty"
    write_json(_draft_file(session), {"status": status, "items": clean, "at": _now()})
    events.append(session.dir, "knowledge_drafted", count=len(clean))
    return {"status": status, "count": len(clean)}


def review(session):
    """The summary as the dashboard shows it, or None when there is none yet."""
    draft_ = current(session)
    return None if draft_ is None else {"status": draft_["status"], "items": draft_.get("items", []), "at": draft_.get("at", "")}


def save(session, items, by="dashboard"):
    """The user's edited summary goes to the knowledge: each item to the scope the user chose. Every item is checked before anything is written."""
    if not isinstance(items, list):
        raise knowledge.KnowledgeError("the items must be a list")
    chosen = [_item(raw) for raw in items if isinstance(raw, dict) and raw.get("keep", True) is not False]
    counts = {"added": 0, "updated": 0, "duplicate": 0}
    for item in chosen:
        source = item["ref"] if item["ref"] not in ("", "-") else f"session {session.id}"
        result = knowledge.learn(session.ws, item["screen"], item["kind"], item["fact"], item["tag"], source, action=item["action"], condition=item["condition"],
                                 profile=item["profile"], state=item["state"], evidence=item["evidence"], scope=item["scope"])
        counts[result] = counts.get(result, 0) + 1
    write_json(_draft_file(session), {"status": "saved", "items": [], "saved": counts, "at": _now()})
    events.append(session.dir, "knowledge_saved", saved=counts["added"] + counts["updated"], duplicate=counts["duplicate"], by=by)
    return counts


def skip(session, by="dashboard"):
    draft_ = current(session) or {}
    write_json(_draft_file(session), {"status": "skipped", "items": [], "at": _now()})
    events.append(session.dir, "knowledge_skipped", count=len(draft_.get("items", [])), by=by)
