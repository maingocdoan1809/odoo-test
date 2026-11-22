"""ANALYZE: what the assistant must work out for every case before anything runs, checked by a script so a half-done analysis cannot pass.

analysis.json = {schema_version, cases: [ {
    id, profile,
    title          text                                 concise Vietnamese description of the behavior under test
    fixture_name   text (optional)                      short Vietnamese business label for generated data
    verdict        ready | needs_user | blocked
    clarity        {score 1-5, notes}                    is the case understandable as written
    sufficiency    {score 1-5, missing: [text]}          does it carry the data to be run
    refs           [{text, status: ok | missing | asked | waived, note}]     one per file / record the case points at
    data_needs     [{what, fallback}]                    data the environment may lack, and the step that creates it (ensure / reach)
    edge_cases     [text]                                what can differ between environments; or `edge_cases_none_reason`
    depends_on     [case id]                             cases whose end state this one starts from
    reuse          text                                  what it takes over from them (optional)
    steps          [{n, action, expected, oracle}]       the strategy for the case, not the one-line description in the workbook.
                                                         oracle = {source: bd | testcase, ref, quote}: where `expected` comes from. The design (BD) outranks the testcase,
                                                         the testcase outranks what the app does today
    bd_searched    text                                  which design document was looked for and why it does not cover a step taken from the testcase alone
    testcase_conflicts [{step, testcase, bd, ref}]       where the testcase says something different from the design: the design wins and the user is warned
    timeout_s      int 5-120 (optional)                  how long one wait may last on this case's pages; default 8. A slow screen, a heavy report
    questions / reason                                   needs_user: what to ask; blocked: why
} ]}

`ready` needs clarity >= 3, sufficiency >= 3 and no ref left `missing`, and may depend only on other `ready` cases."""
import hashlib
import json
from pathlib import Path

from . import SCHEMA_VERSION, events, intake, memory, record_names, refs
from .store import SchemaError

VERDICTS = ("ready", "needs_user", "blocked")
REF_STATUSES = ("ok", "missing", "asked", "waived")
ORACLE_SOURCES = ("bd", "testcase")       # the truth a step is judged against: the design document first, the testcase when no design covers the point
MIN_TIMEOUT, MAX_TIMEOUT = 5, 120
MIN_WORDS = 12          # characters a written justification must have to say anything


def _says(value, minimum=MIN_WORDS):
    return len(str(value or "").strip()) >= minimum


def template(cases_doc, workspace, session=None, *, include_context=True):
    """A skeleton to fill in: every case with its steps and the references intake found, judgement fields left empty. With a `session` it also carries what the user said
    and shared since intake (answers, files they pasted by path), and a reference that is missing is looked for again among those files."""
    profiles = [p["name"] for p in workspace["profiles"]]
    stem = memory.stem_of(cases_doc["source"]["name"])
    folders = [Path(cases_doc["source"]["file"]).parent] + [Path(d) for d in workspace.get("reference_dirs", [])]
    items = []
    for case in cases_doc["cases"]:
        entries = []
        for r in case["refs"]:
            path = r.get("path", "") if r["status"] == "found" else (refs.lookup(session, r["text"], folders) if session else "")
            entries.append({"kind": "file", "text": r["text"], "status": "ok" if path else "missing", "path": path, "note": path})
        items.append({
            "id": case["id"], "title": "", "fixture_name": "", "previous": memory.recall(workspace["name"], stem, case["id"]), "profile": profiles[0] if len(profiles) == 1 else "", "verdict": "",
            "clarity": {"score": 0, "notes": ""}, "sufficiency": {"score": 0, "missing": []},
            "refs": entries,
            "vague": case.get("vague", []),
            "vague_resolved": [{"phrase": v["phrase"], "concrete": "", "source": ""} for v in case.get("vague", [])],
            "data_needs": [], "data_considered": "", "edge_cases": [], "depends_on": list(case["case_refs"]), "reuse": "",
            "bd_searched": "", "testcase_conflicts": [],
            "steps": [{"n": i, "action": line, "expected": "", "oracle": {"source": "", "ref": "", "quote": ""}, "evidence": "", "captions": {"en": "", "ja": ""}}
                      for i, line in enumerate(case["steps"], 1)],
            "_title": case["title"], "_expected": case["expected"],
        })
    out = {"schema_version": SCHEMA_VERSION, "cases": items,
           "_writing": ("Viết tên, bước, mong đợi, phân tích và câu hỏi bằng tiếng Việt. Đặt title theo hành vi nghiệp vụ cần kiểm tra, "
                        "ví dụ «Chặn số lượng âm khi lưu phiếu». Điền fixture_name bằng cụm ngắn mô tả dữ liệu, ví dụ «phiếu hai dòng». "
                        "Giữ id từ file gốc để đối chiếu. Giữ nguyên nhãn và thông báo Odoo khi trích dẫn. "
                        "Điền captions.en và captions.ja bằng chú thích ngắn tương đương cho từng bước."),
           "_evidence_language": workspace.get("evidence_language", "vi")}
    if session:
        if session.state().get("channel"):
            from .store import read_json

            latest = read_json(session.dir / "analysis.json", {}) or {}
            existing = {case["id"]: case for case in latest.get("cases", [])}
            for item in items:
                assessed = existing.get(item["id"])
                if assessed:
                    for field in ("title", "fixture_name", "profile", "verdict", "clarity", "sufficiency", "vague_resolved",
                                  "data_needs", "data_considered", "edge_cases", "edge_cases_none_reason",
                                  "depends_on", "reuse", "steps", "reason", "questions", "timeout_s", "bd_searched", "testcase_conflicts"):
                        if field in assessed:
                            item[field] = assessed[field]
                    refreshed = {ref["text"]: ref for ref in item["refs"] if ref["status"] == "ok"}
                    item["refs"] = [refreshed.get(ref["text"], ref) if ref["status"] in ("missing", "asked") else ref
                                    for ref in assessed.get("refs", item["refs"])]
        out["_user_answers"] = [{"case": e.get("case"), "answer": e["answer"]} for e in events.read(session.dir) if e["kind"] == "answered"]
        out["_shared_files"] = [{"name": i["name"], "path": i["path"], "kind": i["kind"], "case": i.get("case")} for i in refs.items(session)]
    if include_context:
        from . import retrieval

        context = retrieval.WorkspaceContext(workspace["name"], workspace.get("language", "en"))
        if context.unavailable:
            out["_context_unavailable"] = context.unavailable
        budget = retrieval.WORKBOOK_LIMIT
        for case, item in zip(cases_doc["cases"], items):
            found = context.lookup(case, assessed=item)
            compact = retrieval.fit(found, budget)
            if compact:
                item["_context"] = compact
                budget -= len(json.dumps(compact, ensure_ascii=False)) + 20
            if compact != found:
                out["_context_more"] = True
    return out


def _score(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5


def validate(analysis, cases_doc, workspace, choices=None):
    """Raises SchemaError listing every defect. `choices` = what the user decided about testcase/design conflicts (the session's `conflict_choices`)."""
    problems = []
    wanted = [c["id"] for c in cases_doc["cases"]]
    given = [c.get("id") for c in analysis.get("cases", []) if isinstance(c, dict)]
    problems += [f"case {i}: not analysed" for i in wanted if i not in given]
    problems += [f"case {i}: not in the testcase file" for i in dict.fromkeys(given) if i not in wanted]
    problems += [f"case {i}: analysed twice" for i in dict.fromkeys(given) if given.count(i) > 1 and i in wanted]
    by_id = {c["id"]: c for c in analysis.get("cases", []) if isinstance(c, dict) and c.get("id") in wanted}
    profiles = [p["name"] for p in workspace["profiles"]]
    info = {c["id"]: c for c in cases_doc["cases"]}
    for ident, c in by_id.items():
        at = f"case {ident}"
        for field in ("title", "fixture_name"):
            if field in c and not isinstance(c[field], str):
                problems.append(f"{at}: {field} must be text")
        verdict = c.get("verdict")
        if verdict not in VERDICTS:
            problems.append(f"{at}: verdict must be one of {', '.join(VERDICTS)}")
        clarity, sufficiency = c.get("clarity") or {}, c.get("sufficiency") or {}
        if not _score(clarity.get("score")):
            problems.append(f"{at}: clarity.score must be 1-5")
        if not _score(sufficiency.get("score")):
            problems.append(f"{at}: sufficiency.score must be 1-5")
        if c.get("profile") not in profiles:
            problems.append(f"{at}: profile must be one of {', '.join(profiles)}")
        refs = c.get("refs") if isinstance(c.get("refs"), list) else None
        if refs is None:
            problems.append(f"{at}: refs must be a list (empty when the case points at nothing)")
        else:
            for ref in refs:
                if not isinstance(ref, dict) or ref.get("status") not in REF_STATUSES:
                    problems.append(f"{at}: each ref needs a status of {', '.join(REF_STATUSES)}")
                    continue
                label = ref.get("text") or "a reference"
                where = str(ref.get("path") or ref.get("note") or "").strip()
                if ref["status"] == "ok" and (ref.get("kind", "file") == "file") and not (where and Path(where).exists()):
                    problems.append(f"{at}: reference '{label}' is marked ok but there is no file at '{where}'. Give its real path (the user can paste one: `oqa ref add <path>`), or ask for it")
                if ref["status"] == "waived" and not _says(ref.get("note")):
                    problems.append(f"{at}: reference '{label}' is waived: say in its note why the case can be run without it")
        for need in c.get("data_needs") or []:
            if not isinstance(need, dict) or not str(need.get("what", "")).strip() or not str(need.get("fallback", "")).strip():
                problems.append(f"{at}: each data need needs 'what' and a 'fallback' (the step that creates the data)")
        if not c.get("edge_cases") and not str(c.get("edge_cases_none_reason", "")).strip():
            problems.append(f"{at}: list edge_cases, or say why there are none in edge_cases_none_reason")
        steps = c.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append(f"{at}: steps is empty")
        else:
            for position, step in enumerate(steps, 1):
                if not isinstance(step, dict) or step.get("n") != position or not str(step.get("action", "")).strip() or not str(step.get("expected", "")).strip():
                    problems.append(f"{at}: step {position} needs n={position}, an action and an expected result")
                if isinstance(step, dict) and "evidence" in step and verdict == "ready":
                    if not isinstance(step["evidence"], str) or not step["evidence"].strip():
                        problems.append(f"{at}: step {position} needs evidence: describe the visible state and exact region to frame; split combined UI actions into separate steps")
                if isinstance(step, dict) and "oracle" in step and verdict == "ready":
                    problems += _oracle_problems(at, position, step, refs or [])
                if isinstance(step, dict) and "captions" in step:
                    labels = step["captions"]
                    if not isinstance(labels, dict) or any(k not in ("vi", "en", "ja") or not isinstance(v, str) for k, v in labels.items()):
                        problems.append(f"{at}: step {position} captions must map vi, en or ja to text")
        problems += _conflict_problems(at, c, steps if isinstance(steps, list) else [], choices)
        if verdict == "ready" and isinstance(steps, list) and not _says(c.get("bd_searched")) and any(
                isinstance(s, dict) and isinstance(s.get("oracle"), dict) and s["oracle"].get("source") == "testcase" and not s["oracle"].get("chosen_over") for s in steps):
            problems.append(f"{at}: a step takes its expected result from the testcase alone: say in bd_searched which design document was looked for and why it does not cover that point")
        if "timeout_s" in c and not (isinstance(c["timeout_s"], int) and not isinstance(c["timeout_s"], bool) and MIN_TIMEOUT <= c["timeout_s"] <= MAX_TIMEOUT):
            problems.append(f"{at}: timeout_s must be a whole number of seconds from {MIN_TIMEOUT} to {MAX_TIMEOUT}")
        for dep in c.get("depends_on") or []:
            if dep not in wanted:
                problems.append(f"{at}: depends_on '{dep}' is not a case of this file")
            elif dep == ident:
                problems.append(f"{at}: depends on itself")
            elif dep in by_id and by_id[dep].get("verdict") != "ready" and verdict == "ready":
                problems.append(f"{at}: is ready but depends on {dep}, which is not ready")
        if verdict == "ready":
            if _score(clarity.get("score")) and clarity["score"] < 3:
                problems.append(f"{at}: clarity {clarity['score']} is too low for 'ready'")
            if _score(sufficiency.get("score")) and sufficiency["score"] < 3:
                problems.append(f"{at}: sufficiency {sufficiency['score']} is too low for 'ready'")
            if refs and any(isinstance(r, dict) and r.get("status") in ("missing", "asked") for r in refs):
                problems.append(f"{at}: a reference is still missing or only asked for; ask the user (verdict needs_user) or mark it waived with the reason")
            if sufficiency.get("missing") and not c.get("data_needs"):
                problems.append(f"{at}: sufficiency.missing lists what the case lacks, yet the case is ready with no data_needs: either each missing item has a data_need with the step that creates it, or ask the user")
            problems += _vague_problems(at, c, info.get(ident, {}), refs or [])
        if verdict == "needs_user":
            questions = c.get("questions") or []
            if not questions:
                problems.append(f"{at}: needs_user must list the questions to ask")
            elif any(not _says(q, 10) for q in questions):
                problems.append(f"{at}: every question must be a full sentence the user can answer without opening the workbook (what is unclear, and the options you see)")
            if not _says(c.get("data_considered")):
                problems.append(f"{at}: needs_user needs data_considered: what you tried to settle yourself first (data you could create, a file you could read, the sitemap) and why only the user can answer")
        if verdict == "blocked":
            if not str(c.get("reason", "")).strip():
                problems.append(f"{at}: blocked must say why")
            if not _says(c.get("data_considered")):
                problems.append(f"{at}: blocked needs data_considered: why the data it lacks cannot be created through the UI or an import (a blocked case is the last resort)")
    if not problems and _cycle(by_id):
        problems.append("depends_on has a cycle: " + " -> ".join(_cycle(by_id)))
    if problems:
        raise SchemaError(problems, "analysis")
    return analysis


def _oracle_problems(at, n, step, refs):
    """What a step is judged against. Order of authority: the design (BD), then the testcase, then nothing else: what the app does today is never the expectation.
    A step taken from the design cites the design reference of the case (marked ok) and quotes it; a step taken from the testcase is allowed only for what no design covers."""
    oracle = step["oracle"]
    if not isinstance(oracle, dict) or oracle.get("source") not in ORACLE_SOURCES:
        return [f"{at}: step {n} needs oracle.source: bd (the expected result comes from the design document) or testcase (no design document covers this point)"]
    if oracle["source"] != "bd":
        return []
    problems = []
    named = str(oracle.get("ref", "")).strip().lower()
    ok_refs = [str(r.get("text", "")).strip().lower() for r in refs if isinstance(r, dict) and r.get("status") == "ok"]
    if not named or not any(named in text or text in named for text in ok_refs if text):
        problems.append(f"{at}: step {n} oracle.ref '{oracle.get('ref', '')}' is not one of this case's references marked ok. Name the design document and its section")
    if not _says(oracle.get("quote"), 15):
        problems.append(f"{at}: step {n} oracle.quote must quote what the design says (at least 15 characters)")
    return problems


def conflict_key(case, step):
    return f"{case}:{step}"


def conflict_digest(item):
    """Identifies what the user judged: a choice holds only while the testcase text, the design text and the section stay the same."""
    return digest({k: item.get(k) for k in ("testcase", "bd", "ref")})


def chosen(choices, case, item):
    """'bd', 'testcase' or None (not decided, or decided about wording that has since changed)."""
    made = (choices or {}).get(conflict_key(case, item.get("step"))) or {}
    return made.get("choice") if made.get("digest") == conflict_digest(item) else None


def _conflict_problems(at, case, steps, choices=None):
    """A testcase that contradicts the design is reported, never silently followed. The design is the default, so the step is judged against it, until the user
    decides otherwise (they may know the design is the one that is wrong): that decision lives in the session, not in anything the assistant writes."""
    problems = []
    numbered = {s.get("n"): s for s in steps if isinstance(s, dict)}
    for item in case.get("testcase_conflicts") or []:
        if not isinstance(item, dict) or item.get("step") not in numbered or not all(_says(item.get(k), 5) for k in ("testcase", "bd", "ref")):
            problems.append(f"{at}: each testcase_conflicts entry needs the step number, what the testcase says (testcase), what the design says (bd) and the design section (ref)")
            continue
        oracle = numbered[item["step"]].get("oracle")
        user_chose_testcase = chosen(choices, case.get("id"), item) == "testcase" and isinstance(oracle, dict) and oracle.get("source") == "testcase" and oracle.get("chosen_over")
        if not user_chose_testcase and (not isinstance(oracle, dict) or oracle.get("source") != "bd"):
            problems.append(f"{at}: step {item['step']} contradicts the design: its oracle.source must be bd and its expected must state the design's result (the user decides, not you, if the testcase should win)")
    return problems


def apply_choices(document, choices):
    """What the user decided about each conflict, written into the analysis so that everything that reads it (workers, replay, reports) sees one expectation. A step
    the user put on the testcase is judged against the testcase and says so; the design's wording stays in the conflict. Undecided conflicts keep the design."""
    for case in document.get("cases", []):
        for item in case.get("testcase_conflicts") or []:
            step = next((s for s in case.get("steps", []) if isinstance(s, dict) and s.get("n") == item.get("step")), None)
            if step is None:
                continue
            decision = chosen(choices, case.get("id"), item)
            if decision == "testcase":
                if isinstance(step.get("oracle"), dict) and step["oracle"].get("source") == "bd":
                    item["bd_step"] = {"expected": step["expected"], "oracle": step["oracle"]}          # kept so the user can change their mind
                step["oracle"] = {"source": "testcase", "ref": "", "quote": "", "chosen_over": item["ref"]}
                step["expected"] = item["testcase"]
            elif decision == "bd" and isinstance(item.get("bd_step"), dict):
                step["expected"], step["oracle"] = item["bd_step"]["expected"], item["bd_step"]["oracle"]
    return document


def conflicts(document, choices=None):
    """Every place a testcase differs from its design, each with the user's decision ('bd', 'testcase' or None)."""
    return [{"case": c["id"], "step": item.get("step"), "testcase": item.get("testcase"), "bd": item.get("bd"), "ref": item.get("ref"), "choice": chosen(choices, c["id"], item)}
            for c in (document or {}).get("cases", []) if c.get("verdict") == "ready" for item in c.get("testcase_conflicts") or [] if isinstance(item, dict)]


def pending_conflicts(document, choices=None):
    return [c for c in conflicts(document, choices) if not c["choice"]]


def warnings(analysis):
    """Where a testcase differs from its design: shown to the user at review and in the strategy, since the test will follow the design."""
    return [{"case": c["id"], **{k: item.get(k) for k in ("step", "testcase", "bd", "ref")}}
            for c in analysis.get("cases", []) for item in c.get("testcase_conflicts") or [] if isinstance(item, dict)]


def _vague_problems(at, c, case, refs):
    """A case is ready only when nothing in it still has to be guessed: every phrase intake flagged ("as designed", "works correctly", no expected result) has been turned into
    something that can be checked, from a file the user gave, from their answer, or (for what cannot be measured) from the workbook or the product; and no step's `expected`
    says something vague again."""
    problems = []
    resolved = {str(r.get("phrase", "")).strip().lower(): r for r in c.get("vague_resolved") or [] if isinstance(r, dict)}
    ok_refs = [str(r.get("text", "")).strip().lower() for r in refs if isinstance(r, dict) and r.get("status") == "ok"]
    for flag in case.get("vague") or []:
        phrase = flag["phrase"]
        item = resolved.get(phrase.strip().lower())
        if not item:
            problems.append(f"{at}: \"{phrase}\" ({flag['where']}) is not concrete. Add it to vague_resolved with what will be checked and where that comes from, or ask the user (needs_user)")
            continue
        concrete, source = str(item.get("concrete", "")).strip(), str(item.get("source", "")).strip()
        if not _says(concrete, 15) or intake.find_vague({"x": concrete}):
            problems.append(f"{at}: \"{phrase}\": write what is checked as facts (which fields, which labels, in which order, which value), not another general phrase")
        if source.lower().startswith("ref:"):
            named = source[4:].strip().lower()
            if not named or not any(named in text or text in named for text in ok_refs if text):
                problems.append(f"{at}: \"{phrase}\": source '{source}' is not one of this case's references marked ok")
        elif source != "user_answer" and not (flag["kind"] == "unmeasurable" and source in ("workbook", "domain")):
            allowed = "ref:<a reference marked ok> or user_answer" + (" (or workbook / domain for what only needs a reading)" if flag["kind"] == "unmeasurable" else "")
            problems.append(f"{at}: \"{phrase}\": a result judged against a document needs its source: {allowed}. A guess is not a source: ask the user")
    for step in c.get("steps") or []:
        if isinstance(step, dict):
            vague = intake.find_vague({"expected": step.get("expected", "")})
            if vague:
                problems.append(f"{at}: step {step.get('n')} expected is still vague (\"{vague[0]['phrase']}\"): say what the page must show")
    return problems


def _cycle(by_id):
    state, trail = {}, []

    def visit(node):
        state[node] = 1
        trail.append(node)
        for dep in by_id[node].get("depends_on") or []:
            if state.get(dep) == 1:
                return trail[trail.index(dep):] + [dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        state[node] = 2
        trail.pop()
        return None

    for node in by_id:
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def order(analysis, cases_doc):
    """Execution order of the `ready` cases: a case after those it depends on; among those free to go, stay on the same profile as the one just run
    (no re-login), then keep the workbook's order. Returns (order, excluded[{id, verdict, reason}])."""
    position = {c["id"]: i for i, c in enumerate(cases_doc["cases"])}
    by_id = {c["id"]: c for c in analysis["cases"]}
    ready = sorted((i for i, c in by_id.items() if c["verdict"] == "ready"), key=position.get)
    excluded = [{"id": i, "verdict": by_id[i]["verdict"], "reason": "; ".join(by_id[i].get("questions") or []) or by_id[i].get("reason", "")}
                for i in sorted(by_id, key=position.get) if by_id[i]["verdict"] != "ready"]
    left, done, out, last_profile = list(ready), set(), [], None
    while left:
        free = [i for i in left if all(d in done for d in by_id[i].get("depends_on") or [])]
        pick = next((i for i in free if by_id[i]["profile"] == last_profile), free[0])
        left.remove(pick)
        done.add(pick)
        out.append(pick)
        last_profile = by_id[pick]["profile"]
    return out, excluded


def _basis(step):
    """'BD: <ref>' or 'testcase' for a step with a recorded oracle, else ''. What a FAIL on that step is judged against."""
    oracle = step.get("oracle") if isinstance(step, dict) else None
    if not isinstance(oracle, dict) or oracle.get("source") not in ORACLE_SOURCES:
        return ""
    if oracle["source"] == "bd":
        return f"BD: {oracle.get('ref', '').strip()}"
    return f"testcase (user chose it over BD: {oracle['chosen_over']})" if oracle.get("chosen_over") else "testcase"


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def verify_strategy(strategy_doc, analysis):
    """True when strategy.json is untouched since `strategy()` made it and still matches the analysis it came from."""
    body = {k: v for k, v in strategy_doc.items() if k != "sha"}
    return digest(body) == strategy_doc.get("sha") and strategy_doc.get("analysis_sha") == digest(analysis)


LABELS = {
    "vi": {"title": "Chiến lược test", "run": "Sẽ chạy theo thứ tự", "skip": "Chưa chạy (cần xử lý trước)", "profile": "Profile", "deps": "Phụ thuộc", "reuse": "Tận dụng",
           "data": "Dữ liệu có thể thiếu → cách bù", "edge": "Tình huống biên", "steps": "Các bước", "clarity": "Rõ ràng", "suff": "Đủ dữ kiện", "none": "không",
           "concrete": "Điều mơ hồ đã được cụ thể hoá thành", "warn": "Cảnh báo: testcase lệch BD (sẽ chạy theo BD)", "tc": "Testcase", "bd": "BD", "basis": "Căn cứ"},
    "en": {"title": "Test strategy", "run": "Will run in this order", "skip": "Not run yet (needs attention)", "profile": "Profile", "deps": "Depends on", "reuse": "Reuses",
           "data": "Data that may be missing → fallback", "edge": "Edge cases", "steps": "Steps", "clarity": "Clarity", "suff": "Sufficiency", "none": "none",
           "concrete": "Vague wording made concrete", "warn": "Warning: testcase differs from the design (the test follows the design)", "tc": "Testcase", "bd": "Design", "basis": "Basis"},
}


def strategy(analysis, cases_doc, workspace):
    """(strategy dict, markdown). The digest in the dict is what `confirm` checks, so the user approves exactly what will run."""
    ordered, excluded = order(analysis, cases_doc)
    by_id, info = {c["id"]: c for c in analysis["cases"]}, {c["id"]: c for c in cases_doc["cases"]}
    data = {"schema_version": SCHEMA_VERSION, "order": ordered, "excluded": excluded, "analysis_sha": digest(analysis)}
    warned = [w for w in warnings(analysis) if w["case"] in ordered]
    if warned:
        data["warnings"] = warned
    data["sha"] = digest(data)
    t = LABELS.get(workspace.get("language"), LABELS["en"])
    lines = [f"# {t['title']}: {cases_doc['source']['name']}", "", f"## {t['run']} ({len(ordered)})", ""]
    for n, ident in enumerate(ordered, 1):
        c = by_id[ident]
        lines += [f"### {n}. {record_names.title(info[ident], c, {})} ({ident})", "",
                  f"- {t['profile']}: {c['profile']} · {t['clarity']} {c['clarity']['score']}/5 · {t['suff']} {c['sufficiency']['score']}/5",
                  f"- {t['deps']}: {', '.join(c.get('depends_on') or []) or t['none']}" + (f" · {t['reuse']}: {c['reuse']}" if c.get("reuse") else "")]
        if c.get("data_needs"):
            lines.append(f"- {t['data']}: " + "; ".join(f"{d['what']} → {d['fallback']}" for d in c["data_needs"]))
        resolved = [v for v in c.get("vague_resolved") or [] if v.get("concrete")]
        if resolved:
            lines.append(f"- {t['concrete']}: " + "; ".join(f"\"{v['phrase']}\" → {v['concrete']} [{v['source']}]" for v in resolved))
        lines.append(f"- {t['edge']}: " + ("; ".join(c.get("edge_cases") or []) or c.get("edge_cases_none_reason", "")))
        lines += [f"- {t['steps']}:"] + [f"  {s['n']}. {s['action']} → {s['expected']}"
                  + (f" [{t['basis']}: {_basis(s)}]" if _basis(s) else "")
                  + (f" [Evidence: {s['evidence']}]" if s.get("evidence") else "") for s in c["steps"]] + [""]
    if warned:
        lines += [f"## {t['warn']} ({len(warned)})", ""] + [
            f"- {w['case']} bước {w['step']} ({w['ref']}): {t['tc']}: {w['testcase']} ≠ {t['bd']}: {w['bd']}" for w in warned] + [""]
    if excluded:
        lines += [f"## {t['skip']} ({len(excluded)})", ""] + [f"- {e['id']} [{e['verdict']}]: {e['reason']}" for e in excluded] + [""]
    return data, "\n".join(lines)
