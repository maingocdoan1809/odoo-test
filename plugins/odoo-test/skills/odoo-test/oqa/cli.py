"""The `oqa` command line. Each command returns (data, text): `--json` prints the data, otherwise the text.

Exit codes: 0 done · 1 unexpected failure · 2 refused (wrong phase, invalid input, waiting for the user). A refusal always says what to run instead."""
import argparse
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import textwrap
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote

from . import VERSION, autostart, build_id, analysis, claims, connect, memory, dashboard_server, doctor, events, globalhooks, init_server, intake, knowledge, mcp_server, paths, probe, refs, report, sitemap, workspace
from .secrets import SecretStoreUnavailable, default_store
from .state import MAX_REPAIRS, GateError, Session
from .store import SchemaError, read_json, write_json
from . import live
from .sitemap_i18n import text as sitemap_text

REFUSED = (GateError, SchemaError, paths.PathError, knowledge.KnowledgeError, SecretStoreUnavailable, connect.LoginFailed, sitemap.SitemapError)


# --- commands ---------------------------------------------------------------------
def cmd_init(args):
    if args.status:
        data = init_server.status(args.ws)
        if data["configured"]:
            return data, "Workspace " + data["workspace"] + " is configured (" + data["url"] + ")"
        started = autostart.ensure_init_form(args.ws)
        return data, "No valid workspace yet. " + ("The setup form is opening in the user's browser (started for you): tell them to fill it in, then `oqa init --status` again." if started else "Run: oqa init (in the background)")
    server = init_server.InitServer(default_store(), args.ws, args.timeout)
    summary = server.run(lambda url: print(f"Waiting for the form to be saved: {url}", flush=True), open_browser=not args.no_open)
    if summary is None:
        raise GateError(f"The form was not saved within {args.timeout} seconds. Nothing was written. Run oqa init again.")
    started = autostart.ensure_prepare(summary["workspace"])
    return summary, f"Saved workspace {summary['workspace']} ({summary['url']}), profiles: {', '.join(summary['profiles'])}" + (
        ". The environment (sitemap, lines for every screen) is being prepared in the background: `oqa prepare --status`." if started else "")


def cmd_doctor(args):
    report = doctor.check()
    if args.fix and not report["ready"]:
        report = doctor.fix(report, lambda line: print(line, file=sys.stderr, flush=True))
    lines = [f"oqa {report['oqa']} · python {report['python']} {'ok' if report['python_ok'] else 'TOO OLD (3.9+ needed)'}",
             "packages: " + ("ok" if not report["missing_packages"] else "missing " + ", ".join(report["missing_packages"])),
             "browser: " + ("ok" if report["browser_installed"] else "not installed"),
             "password store: " + ("ok" if report["secret_store"] else report["secret_store_note"]),
             "global hooks: " + ("ok" if not report["missing_hooks"] else "not installed (" + ", ".join(report["missing_hooks"]) + "). Run: oqa hooks --install"),
             "READY" if report["ready"] else "Not ready. Run: oqa doctor --fix"]
    return report, "\n".join(lines)


def cmd_ws(args):
    if args.what == "list":
        names = paths.workspace_names()
        return {"workspaces": names}, "\n".join(names) or "No workspace yet. Run: oqa init"
    name = paths.resolve_workspace(args.ws)
    ws = workspace.load(name)
    try:
        view = workspace.public_view(ws, default_store())
    except SecretStoreUnavailable:
        view = ws
    lines = [f"{view['name']}  {view['url']}" + (f"  db={view['db']}" if view["db"] else "")]
    lines += [f"  profile {p['name']}: {p['login']}" + ("" if "has_secret" not in p else " (password saved)" if p["has_secret"] else " (NO PASSWORD)") + (f"  {p['note']}" if p["note"] else "") for p in view["profiles"]]
    lines += [f"  environment {e['name']}: {e['url']}" + (f"  db={e['db']}" if e["db"] else "") for e in view.get("environments", [])]
    lines += [f"  reference folder: {d}" for d in view["reference_dirs"]]
    return view, "\n".join(lines)


def cmd_session(args):
    if args.what == "list":
        ws = paths.resolve_workspace(args.ws)
        items = [Session(ws, i).summary() for i in paths.session_ids(ws)]
        return {"sessions": items}, "\n".join(f"{s['session']}  {s['phase']:8} {s['file']}" for s in items) or "No session yet."
    if args.what == "show":
        return cmd_status(args)
    ws = paths.resolve_workspace(args.ws)
    config = workspace.load(ws)
    if not args.file:
        raise GateError("Say which testcase file: oqa session new --file <path to .xlsx> [--cases A,B]")
    path = Path(args.file).expanduser().resolve()
    regions = args.cases if args.cases and "!" in args.cases else None
    cases = intake.build(path, config["reference_dirs"], selection=regions)
    if args.what == "cases":
        return {"cases": [{"id": c["id"], "title": c["title"], "steps": len(c["steps"])} for c in cases["cases"]]}, "\n".join(
            f"{c['id']:14} {len(c['steps']):2} step(s)  {c['title']}" for c in cases["cases"]) + f"\n{len(cases['cases'])} case(s) in {path.name}. Pick some with: oqa session new --file <it> --cases \"A,B\" (ids, A..C ranges or words of the title)."
    cases = intake.select(cases, None if regions else args.cases)
    channel = getattr(args, "channel", None) or ("path" if regions else None)
    session = None
    reused = False
    if getattr(args, "reuse_pending", False):
        # Both review rounds belong to the intake already in progress.
        pointer = paths.workspace_dir(ws) / "current"
        requested = args.session or os.environ.get(paths.SESSION_ENV) or (pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None)
        if requested:
            candidate = Session(ws, requested)
            state = candidate.state()
            previous = candidate.cases()
            scope = lambda doc: [(case["id"], case.get("sheet"), case["row"]) for case in doc["cases"]]
            if (state["phase"] in ("ANALYZE", "CONNECT", "CONFIRM")
                    and not state.get("stopped") and state.get("channel") == channel
                    and Path(state["file"]).resolve() == path
                    and previous["source"]["sha256"] == cases["source"]["sha256"]
                    and scope(previous) == scope(cases)):
                session = candidate
                reused = True
                # References may have become available between the two review rounds.
                write_json(session.dir / "cases.json", cases)
    session = session or Session.create(ws, path, cases, channel=channel)
    missing = [(c["id"], r["text"]) for c in cases["cases"] for r in c["refs"] if r["status"] == "missing"]
    chosen = cases.get("selection")
    lines = [f"{'Reusing session' if reused else 'Session'} {session.id} in workspace {ws}: {len(cases['cases'])} cases " + (f"chosen out of {chosen['of']} " if chosen else "read ") + f"from {path.name} (sheet {cases['source']['sheet']}); the file is used as it is, nothing new is written."]
    lines += [f"  warning: {w}" for w in cases["warnings"]]
    lines += [f"  reference not found: {case} mentions {name}" for case, name in missing]
    lines += memory.summary(ws, memory.stem_of(path), [c["id"] for c in cases["cases"]])
    autostart.ensure_prepare(ws)                      # a workspace that was never prepared gets its sitemap and lines now, not in the middle of a step
    started = autostart.ensure_dashboard(ws, session.id) if channel is None else False
    if channel is None:
        lines.append("Dashboard " + ("is opening in the user's browser (started for you)." if started else "is already running: `oqa dashboard` opens it on this session."))
    lines.append("Next: " + Session.open(ws, session.id).summary()["next"])
    return {"dashboard_started": started, "session": session.id, "reused": reused, "workspace": ws, "cases": len(cases["cases"]), "warnings": cases["warnings"], "missing_refs": [{"case": c, "file": f} for c, f in missing]}, "\n".join(lines)


def cmd_run(args):
    path = Path(args.file).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".xlsx":
        raise GateError(f"Choose an existing .xlsx workbook: {path}")
    server = init_server.InitServer(default_store(), args.ws, args.timeout)
    selected = server.run(lambda url: print(f"Workspace picker: {url}", file=sys.stderr, flush=True),
                          open_browser=not args.no_open)
    if selected is None:
        raise GateError("Workspace selection timed out. Run oqa run start again.")
    args.ws, args.what = selected["workspace"], "new"
    args.reuse_pending = not args.new_session
    return cmd_session(args)


def cmd_analyze(args):
    session = Session.open(args.ws, args.session)
    config = workspace.load(session.ws)
    cases = session.cases()
    if args.template:
        session.require("ANALYZE", "CONNECT", "CONFIRM")
        skeleton = analysis.template(cases, config, session, include_context=not args.no_context)
        if args.out:
            write_json(args.out, skeleton)
            return {"template": args.out}, f"Wrote the template for {len(skeleton['cases'])} cases to {args.out}. Fill every judgement field, then: oqa analyze --submit {args.out}"
        return skeleton, json.dumps(skeleton, ensure_ascii=False, indent=2)
    if not args.submit:
        raise GateError("Use --template to get the skeleton, or --submit <file> to hand in the analysis.")
    session.require("ANALYZE", "CONNECT", "CONFIRM")
    submitted = read_json(args.submit)
    if not isinstance(submitted, dict):
        raise SchemaError(f"{args.submit} is not an analysis file", "analysis")
    state = session.state()
    if state["waiting"]:
        raise GateError("Complete the pending reviews before submitting analysis again.")
    skipped = state.get("skipped_reviews", {})
    for case in submitted.get("cases", []):
        if isinstance(case, dict) and case.get("id") in skipped:
            reason = skipped[case["id"]]
            case.update(verdict="blocked", reason=reason,
                        data_considered=f"The user excluded the case from this run: {reason}", questions=[])
    choices = state.get("conflict_choices")
    analysis.validate(submitted, cases, config, choices)
    analysis.apply_choices(submitted, choices)                # what the user decided about testcase/design conflicts survives a new submission
    counts = {}
    for case in submitted["cases"]:
        counts[case["verdict"]] = counts.get(case["verdict"], 0) + 1
    asking = [c for c in submitted["cases"] if c["verdict"] == "needs_user"]
    question = "\n".join(f"[{c['id']}] {q}" for c in asking for q in c["questions"]) if asking else None
    reviews = {c["id"]: c["questions"] for c in asking} if state.get("channel") else None
    session.analysis_submitted(question, reviews=reviews, document=submitted)
    if session.state().get("channel"):
        marker = session.dir / "dashboard-opened"
        if not marker.exists() and autostart.ensure_dashboard(session.ws, session.id, select_existing=True):
            marker.touch()
    summary = ", ".join(f"{n} {v}" for v, n in counts.items())
    warned = analysis.warnings(submitted)
    if warned:
        notice = "\nWARNING: the testcase differs from the design in " + "; ".join(f"{w['case']} step {w['step']} ({w['ref']})" for w in warned) + \
                 ". The user must choose on the dashboard, for each one, whether the test follows the BD or the testcase (the BD may be the one that is wrong); " \
                 "the strategy cannot be confirmed before. Until they choose the step follows the BD."
        summary += notice
    if question and session.state()["waiting"] and session.state()["phase"] == "ANALYZE":
        return {"accepted": True, "verdicts": counts, "waiting": True}, (
            f"Analysis accepted ({summary}) but {len(asking)} case(s) need the user, so nothing moves on yet. Their questions are now on the dashboard:\n{question}\n"
            "Tell the user (they can answer there or in the chat, and paste a file path instead of uploading anything), then `oqa wait` in the background and stop. "
            "After the answer: `oqa analyze --template` again (it carries their answers and the files they shared), fix those cases and `--submit` again.")
    return ({"accepted": True, "verdicts": counts, **({"warnings": warned} if warned else {})},
            f"Analysis accepted ({summary}). Next: {session.summary()['next']}")


def cmd_ref(args):
    """Files and folders the user pointed at by their path: add, list, read."""
    session = Session.open(args.ws, args.session)
    if args.what == "list":
        return {"refs": refs.items(session)}, refs.listing(session)
    if args.what == "add":
        if not args.target:
            raise GateError("Give the path: oqa ref add <path of a file or folder> [--case ID]")
        try:
            item = refs.add(session, args.target, by="terminal", case=args.case)
        except refs.RefError as error:
            raise GateError(str(error))
        return {"ref": item}, f"Added {item['kind']} {item['path']}. Read it: oqa ref show \"{item['name']}\""
    if not args.target:
        raise GateError("Which one? oqa ref show <name or path of a reference, or of the testcase workbook>")
    config = workspace.load(session.ws)
    folders = [Path(session.cases()["source"]["file"]).parent] + [Path(d) for d in config.get("reference_dirs", [])]
    path = refs.lookup(session, args.target, folders)
    if not path:
        raise GateError(f"No reference called '{args.target}'. Known:\n{refs.listing(session)}")
    try:
        text = refs.describe(path, args.max, args.sheet, session.dir / "refs")
    except refs.RefError as error:
        raise GateError(str(error))
    return {"path": path, "text": text}, text


def cmd_strategy(args):
    session = Session.open(args.ws, args.session)
    session.require("CONFIRM")
    config = workspace.load(session.ws)
    data, text = analysis.strategy(read_json(session.dir / "analysis.json"), session.cases(), config)
    write_json(session.dir / "strategy.json", data)
    (session.dir / "strategy.md").write_text(text + "\n", encoding="utf-8")
    session.strategy_ready(data["sha"])
    return {"order": data["order"], "excluded": data["excluded"], "file": str(session.dir / "strategy.md"), "sha": data["sha"]}, \
        f"Strategy written to {session.dir / 'strategy.md'}: {len(data['order'])} cases to run, {len(data['excluded'])} held back.\nShow it to the user and ask them to approve it."


def cmd_confirm(args, is_tty=None):
    session = Session.open(args.ws, args.session)
    state = session.require("CONFIRM")
    tty = (sys.stdin.isatty() and sys.stdout.isatty()) if is_tty is None else is_tty
    if not tty:
        raise GateError("Only the user can confirm: run `oqa confirm` yourself in a terminal, or press Confirm in the dashboard. The assistant cannot do this for them.")
    if is_tty is None:
        print((session.dir / "strategy.md").read_text(encoding="utf-8"))
        if input("Type 'yes' to approve this strategy and start testing: ").strip().lower() not in ("yes", "y", "có", "co"):
            raise GateError("Not confirmed.")
    strategy_doc = session.confirm_current("terminal")
    return {"confirmed": True, "by": "terminal"}, f"Confirmed. {len(strategy_doc['order'])} cases will run. Next: {session.summary()['next']}"


def cmd_connect(args):
    session = Session.open(args.ws, args.session)
    ws = workspace.load(session.ws)
    info = connect.connect(session, ws, default_store(), headless=not args.headed)
    lines = [f"Signed in: {name} -> {item['landing_url']}" + (f" (Odoo {item['version']})" if item["version"] else "") for name, item in info.items()]
    lines.append(_sitemap_first(args, session.ws))
    return {"profiles": info}, "\n".join(lines) + "\nNext: " + session.summary()["next"]


def _sitemap_first(args, ws_name):
    """The sitemap comes before anything else that touches the screens (exploring, trying): build it right after signing in when there is none. It only reads, so a
    failure here never blocks the session; it is reported and `oqa sitemap build` can be run again."""
    try:
        sitemap.load(ws_name, "en")
        return "Sitemap: already built (oqa sitemap show)."
    except Exception:
        pass
    try:
        argv = argparse.Namespace(**{**vars(args), "what": "build", "target": None, "lang": None, "profile": None, "languages": None, "source": None, "kind": "", "view": None,
                                     "match": None, "required": False, "limit": None})
        _, text = cmd_sitemap(argv)
        return "Sitemap built first (read only): " + text.splitlines()[0]
    except Exception as error:
        return f"Sitemap could not be built ({str(error).splitlines()[0][:160]}): run `oqa sitemap build` before exploring."


HARD_WORDS = ("batch", "cron", "mail", "import", "export", "archive", "user", "contract", "company", "upload", "approve")


def case_difficulty(item):
    """A rough weight for dividing work: steps, data that must be created, edge cases, and words that usually mean a heavy setup (batch, import, users...)."""
    text = " ".join(f"{s.get('action', '')} {s.get('expected', '')}" for s in item.get("steps") or []).lower()
    return len(item.get("steps") or []) + 2 * len(item.get("data_needs") or []) + len(item.get("edge_cases") or []) + min(3, sum(1 for w in HARD_WORDS if w in text))


def cmd_assign(args):
    """Divide the cases without a result between agents: at most `--max` cases each, the total difficulty as even as possible, cases that depend on each other together."""
    session = Session.open(args.ws, args.session)
    state = session.state()
    strategy = read_json(session.dir / "strategy.json") or {"order": []}
    analysis_by_id = {c["id"]: c for c in (read_json(session.dir / "analysis.json") or {"cases": []})["cases"]}
    todo = [c for c in strategy["order"] if c not in state["results"] and c in analysis_by_id]
    if not todo:
        return {"agents": []}, "Every case already has a result: nothing to divide."
    groups, seen = [], set()
    for c in todo:                                                  # cases that depend on each other stay with ONE agent
        if c in seen:
            continue
        group, stack = [], [c]
        while stack:
            x = stack.pop()
            if x in seen or x not in todo:
                continue
            seen.add(x)
            group.append(x)
            stack += [d for d in analysis_by_id[x].get("depends_on") or []] + [o for o in todo if x in (analysis_by_id[o].get("depends_on") or [])]
        groups.append(group)
    most = max(1, args.max)
    count = args.agents or max(1, -(-len(todo) // most))
    if any(len(g) > most for g in groups):
        raise GateError(f"A group of cases that depend on each other is longer than --max {most}: raise it.")
    bins = [{"cases": [], "score": 0} for _ in range(count)]
    for g in sorted(groups, key=lambda g: -sum(case_difficulty(analysis_by_id[x]) for x in g)):
        weight = sum(case_difficulty(analysis_by_id[x]) for x in g)
        room = [b for b in bins if len(b["cases"]) + len(g) <= most]
        if not room:
            raise GateError(f"{len(todo)} cases do not fit {count} agents at {most} each: raise --agents or --max.")
        target = min(room, key=lambda b: (b["score"], len(b["cases"])))
        target["cases"] += g
        target["score"] += weight
    needs = {x: [d.get("what", "") for d in analysis_by_id[x].get("data_needs") or []] for x in todo}
    prep = [x for x in todo if needs[x]]                                      # the cases that need input made first: files to import, records, users
    for b in bins:                                                             # each agent takes the easy cases first, and the ones that wait for prepared data last
        b["cases"].sort(key=lambda x: (x in prep, case_difficulty(analysis_by_id[x])))
    lines = [f"{len(todo)} cases to divide between {count} agent(s), at most {most} each (difficulty = steps + 2 x data to create + edge cases + heavy-setup words):"]
    for i, b in enumerate(bins, 1):
        detail = ", ".join(f"{x} ({case_difficulty(analysis_by_id[x])}{', needs data' if x in prep else ''})" for x in b["cases"])
        lines.append(f"  agent {i}: {detail}  | total difficulty {b['score']}  (easy and independent first)")
    if prep:
        lines.append("Data to prepare first (give it to ONE preparation agent, who makes the files and records and reports the names; the others take the cases that need none first):")
        lines += [f"  case {x}: " + "; ".join(n[:110] for n in needs[x]) for x in prep]
    lines.append("Give each agent only its own ids and the rules in references/agents.md.")
    return {"agents": bins, "prepare": {x: needs[x] for x in prep}}, "\n".join(lines)


def cmd_claim(args):
    session = Session.open(args.ws, args.session)
    case, note = claims.claim(session, args.agent, args.case or None)
    recent = claims.notes(session, 8)
    text = note + ("\nShared notes (latest):\n" + "\n".join(f"  {n['ts'][11:]} [{n['agent']}{' case ' + n['case'] if n['case'] else ''}] {n['text']}" for n in recent) if recent and case else "")
    data = {"case": case, "note": note}
    if case and getattr(args, "packet", False):
        from . import case_context

        data["context"] = case_context.build(session, case)
        return data, json.dumps(data, ensure_ascii=False, indent=2)
    return data, text


def cmd_case(args):
    from . import case_context

    session = Session.open(args.ws, args.session)
    data = case_context.build(session, args.case, args.screen, args.issue,
                              only=args.only or "", profile=args.profile, state=args.state)
    return data, json.dumps(data, ensure_ascii=False, indent=2)


def cmd_fixture(args):
    from . import fixtures

    session = Session.open(args.ws, args.session)
    if args.action == "key":
        if not args.case:
            raise GateError("Key needs --case.")
        key = fixtures.run_key(session, args.case, args.label or "")
        return {"key": key}, key
    if args.action == "cleanup":
        if not args.case:
            raise GateError("Cleanup needs --case: its profile and browser are used to find the records.")
        rows = fixtures.cleanup(session, args.case, run=args.run, apply=args.apply)
        lines = [f"{row['model']} {row['key']}: " + (row.get("error") or (f"{row['found']} found" + (", removed" if row["removed"] else ""))) for row in rows]
        return {"records": rows, "applied": bool(args.apply)}, "\n".join(lines) + ("" if args.apply else "\nDry run: add --apply to remove these records.") if rows else "This session created no tracked data."
    if args.action in ("ui", "ref"):
        if not args.case:
            raise GateError(f"{args.action} needs --case.")
        if args.action == "ref":
            model = fixtures.preset_model(session, args.screen)
            name = fixtures.check_reference(session, args.case, model, args.value or "")
            return {"model": model, "name": name}, f"{model}: {name} exists once and is used read-only."
        if not args.key or not (args.values or args.values_json):
            raise GateError("ui needs --key and --values or --values-json.")
        try:
            values = json.loads(args.values_json) if args.values_json else read_json(args.values)
        except (ValueError, OSError) as error:
            raise GateError(f"Read fixture values: {error}") from error
        data = fixtures.ui_create(session, args.case, args.screen, args.key, values)
        if args.out:
            Path(args.out).write_text(data["code"] + "\n", encoding="utf-8")
        return data, f"Created {data['model']} {data['key']} through the form; its steps are part of the next replay step."
    if args.action == "apply":
        if not args.case or not args.key or not (args.values or args.values_json):
            raise GateError("Apply needs --case, --key and either --values or --values-json.")
        try:
            values = json.loads(args.values_json) if args.values_json else read_json(args.values)
        except (ValueError, OSError) as error:
            raise GateError(f"Read fixture values: {error}") from error
        data = fixtures.apply(session, args.case, args.screen, args.key, values)
        if args.out:
            Path(args.out).write_text(data["code"] + "\n", encoding="utf-8")
        return {"id": data["id"], "key": data["key"], "code_file": args.out}, data["id"]
    data = sitemap.load(session.ws)
    key, form = fixtures.form_for(data, args.screen)
    fields = [{**field, "selector": sitemap.selector_of("field", field["name"], "form")}
              for field in form.get("fields", []) if not field.get("readonly")]
    plan = {
        "model": form["model"], "screen": key,
        "prefix": workspace.load(session.ws).get("test_prefix"),
        "fields": fields, "route": sitemap.text_path(data, "", key),
        "playwright": sitemap.codegen(data, key),
    }
    if args.action == "advise":
        if not args.case or not args.need:
            raise GateError("Advise needs --case and --need.")
        plan["proposal"] = fixtures.advise(session, args.case, plan, args.need)
    return plan, json.dumps(plan, ensure_ascii=False, indent=2)


def cmd_release(args):
    session = Session.open(args.ws, args.session)
    claims.release(session, args.case)
    return {"released": args.case}, f"Case {args.case} is free again."


def cmd_claims(args):
    session = Session.open(args.ws, args.session)
    held, free = claims.table(session), claims.free_cases(session)
    lines = [f"  case {c}: {e['agent']} ({'working' if e['fresh'] else 'quiet for over ' + str(claims.LEASE_S // 60) + ' min: free to take'})" for c, e in held.items()]
    lines.append("  free: " + (", ".join(free) if free else "none"))
    return {"held": held, "free": free}, "\n".join(lines)


def cmd_dispatch(args):
    from . import dispatch

    session = Session.open(args.ws, args.session)
    result = dispatch.run(session, args.worker_model, args.coordinator_model, args.worker_timeout)
    return result, json.dumps(result, ensure_ascii=False, indent=2)


def cmd_worker_mcp(args):
    from . import worker_mcp

    worker_mcp.serve(Session.open(args.ws, args.session), args.case, args.agent)
    return {}, ""


def cmd_note(args):
    session = Session.open(args.ws, args.session)
    claims.add_note(session, args.agent, " ".join(args.text), args.case or "")
    return {"ok": True}, "Note shared with every agent of this session."


def cmd_notes(args):
    session = Session.open(args.ws, args.session)
    rows = claims.notes(session, args.limit)
    return {"notes": rows}, "\n".join(f"{n['ts'][11:]} [{n['agent']}{' case ' + n['case'] if n['case'] else ''}] {n['text']}" for n in rows) or "No notes yet."






def _case_in_strategy(session, case):
    if case not in read_json(session.dir / "strategy.json")["order"]:
        raise GateError(f"{case} is not in the confirmed strategy.")








def default_video(args):
    """No video unless asked for: a fast run has no viewing pauses to read a note in. A regression keeps the video of a failed script by default."""
    if args.fast:
        return "off"
    return "fail" if args.func is cmd_regress else "off"


def cmd_regress(args):
    """Run every saved script of the workspace on one environment (regression), or show the latest result of each script on each environment."""
    from . import regression

    ws = paths.resolve_workspace(args.ws)
    if args.matrix or not args.env:
        table = regression.matrix(ws)
        head = ["script"] + table["environments"]
        lines = ["  ".join(head)] + ["  ".join([row["key"]] + [f"{row['results'][e]['status']}" + (f" ({row['results'][e]['at']})" if row["results"][e].get("at") else "") for e in table["environments"]]) for row in table["scripts"]]
        return table, "\n".join(lines) if table["scripts"] else "No saved script in this workspace yet."
    wanted = [c.strip() for c in args.cases.split(",") if c.strip()] if args.cases else None
    rows = regression.run(ws, args.env, wanted, headed=args.headed, video=args.video or default_video(args), action_delay_ms=args.action_delay_ms,
                          step_delay_ms=args.step_delay_ms, fast=args.fast, caption_language=args.caption_language)
    bad = [row for row in rows if row["replay"] != "PASS"]
    lines = [f"{row['script']} on {row['env']}: {row['replay']}" + (f" ({row['total_s']}s)" if row["replay"] == "PASS" else f" - {row['reason']}") + (f"\n    video: {row['video']}" if row.get("video") else "") for row in rows]
    return {"environment": rows[0]["env"], "runs": rows, "failed": len(bad)}, "\n".join(lines) + f"\nRegression on {rows[0]['env']}: PASS {len(rows) - len(bad)}/{len(rows)}."


def cmd_results(args):
    """Put the result.json of runs made by hand (node replay.mjs from the scripts ZIP) onto the dashboard."""
    from . import regression

    rows = regression.import_results(paths.resolve_workspace(args.ws), args.path)
    return {"imported": rows}, "\n".join(f"{r['script']} on {r['env']}: {r['status']}" for r in rows) + f"\nImported {len(rows)} result(s)."


def cmd_pgadmin(args):
    """Database evidence: run one read-only SQL on the team's PgAdmin and take the picture. The AI passes the SQL only; address and passwords come from oqa init."""
    from . import pgadmin

    ws = workspace.load(paths.resolve_workspace(args.ws))
    store = default_store()
    if args.action == "check":
        with pgadmin.session(ws, store, headless=True):
            return {"ok": True}, "PgAdmin signed in and the database is selected."
    if not args.sql:
        raise GateError("Give the query: --sql \"SELECT ...\"")
    if args.case:
        session = Session.open(args.ws, args.session)
        result, picture = pgadmin.shot(session, ws, store, args.case, args.n, args.sql, args.caption, headless=not args.headed)
        where = f"attached to step {args.n} of {args.case}: {picture}"
    else:
        out = Path(args.out or "pgadmin.png").resolve()
        with pgadmin.session(ws, store, headless=not args.headed) as pg:
            result = pg.query(pgadmin.check_sql(args.sql), out)
        where = str(out)
    header = result["rows"][0] if result["rows"] else []
    lines = [("PgAdmin ERROR: " + result["error"]) if result["error"] else f"PgAdmin returned {result['total']} row(s). Picture {where}"]
    lines += [" | ".join(c.split(chr(10))[0] for c in row[1:]) for row in result["rows"][:20]] if not result["error"] and header else []
    return {"result": result, "picture": where}, "\n".join(lines)


def cmd_live(args):
    """EXECUTE with playwright-cli: the case's own browser, the outcome of each step with a picture framed by oqa (see oqa/live.py)."""
    session = Session.open(args.ws, args.session)
    if args.action == "start":
        return live.start(session, args.case)
    if args.action == "recover":
        return live.recover(session, args.case, args.reason or "")
    if args.action == "handover":
        return live.handover(session, args.case, args.reason or "", args.to or "")
    if args.action == "diagnose":
        return live.diagnose(session, args.case, args.category, args.detail or "", args.tried or "", args.need or "")
    if args.action == "profile":
        return live.select_profile(session, args.case, args.profile)
    code = args.code
    if args.action in ("act", "step") and args.verified_code:
        try:
            code = Path(args.verified_code).read_text(encoding="utf-8")
        except OSError as error:
            raise GateError(str(error)) from error
    if args.action == "act":
        return live.act(session, args.case, args.browser_command, args.browser_arg, code)
    if args.action == "shot":
        return live.shot(session, args.case, args.n, args.target, from_pre=args.from_pre, caption=args.caption)
    if args.action == "step":
        return live.step(session, args.case, args.n, args.status, args.actual or "", args.target, code, args.shows)
    if args.action == "script":
        return live.keep_script(session, args.case, args.script)
    if args.action == "regenerate":
        from . import generalize

        cases = [c for c in read_json(session.dir / "strategy.json")["order"] if live.script_file(session, c).is_file() and (session.state()["results"].get(c) or {}).get("status") == "PASS"] if args.case == "all" else [args.case]
        rows = []
        for case in cases:
            try:
                rows.append(generalize.regenerate(session, case, model=args.model, attempts=args.attempts, replays=0 if args.no_verify else 2))
            except GateError as error:
                rows.append({"case": case, "status": "FAIL", "reason": str(error)})
        bad = [r for r in rows if r["status"] not in ("PASS", "UNVERIFIED")]
        lines = [f"{r['case']}: generic script {r['status']}" + (f" after {r['attempts']} attempt(s)" if r["status"] in ("PASS", "UNVERIFIED") else f" - {r['reason']}") for r in rows]
        return {"regenerated": rows, "failed": len(bad)}, "\n".join(lines)
    if args.action == "replay":
        if args.case == "all":
            rows = live.replay_all(session, headed=not args.headless, action_delay_ms=args.action_delay_ms,
                                   step_delay_ms=args.step_delay_ms, fast=args.fast, env=args.env, video=args.video or default_video(args),
                                   caption_language=args.caption_language)
            bad = [row for row in rows if row["replay"] != "PASS"]
            lines = [f"{row['case']}: Replay {row['replay']}" + (f" ({row['total_s']}s)" if row["replay"] == "PASS" else f" - {row['reason']}") for row in rows]
            return {"replays": rows, "failed": len(bad)}, "\n".join(lines) + f"\nReplay PASS {len(rows) - len(bad)}/{len(rows)}."
        result = live.replay(session, args.case, headed=not args.headless,
                             action_delay_ms=args.action_delay_ms, step_delay_ms=args.step_delay_ms, fast=args.fast, env=args.env,
                             video=args.video or default_video(args), caption_language=args.caption_language)
        if result["replay"] == "CANCELLED":
            return {**result, "failed": 1}, result["reason"]
        t = result["timing"]
        return result, (f"Saved script completed for {args.case}: Replay PASS in {t['total_s']}s "
                        f"({t['pause_s']}s viewing pauses, {t['call_s']}s in {t['calls']} browser calls).")
    return live.end(session, args.case, args.status, args.reason or "", args.script, category=args.category, control=args.control)








def cmd_finish(args):
    session = Session.open(args.ws, args.session)
    session.declare(args.case, args.status, args.reason, args.category or "")
    return {"case": args.case, "status": args.status}, f"{args.case} recorded as {args.status}. Next: " + session.summary()["next"]


def _session_and_workspace(args):
    """The session (None when there is none yet) and the workspace of a command that only looks at the Odoo, so it works before a session exists."""
    try:
        session = Session.open(args.ws, args.session)
        return session, workspace.load(session.ws)
    except paths.PathError:
        return None, workspace.load(paths.resolve_workspace(args.ws))








def cmd_report(args):
    session = Session.open(args.ws, args.session)
    ws = workspace.load(session.ws)
    if args.partial or session.state()["phase"] == "DONE":           # again after the session closed: rebuild it, change nothing
        session.require("EXECUTE", "REPORT", "DONE", working=False)
        result = report.build(session, ws)
    else:
        result = report.finish(session, ws)
    opened = not args.no_open and report.open_file(result["main"])
    result["opened"] = opened
    back = result.get("testcase_file") or {}
    text = [f"Result: {result['main']}" + (" (the testcase file itself: each case's status is in its own row, the evidence is in the new sheets)" if back.get("in_place") else "") + (" (opened on the user's screen)" if opened else ""),
            *([back["note"]] if back.get("note") else []), f"The original file was copied to {back['backup']} first." if back.get("backup") else "",
            ", ".join(f"{k} {v}" for k, v in sorted(result["counts"].items()))]
    return result, "\n".join(text)


def _alive(url):
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def _retire(running, wait=4.0):
    """Ask a dashboard that runs older code to leave; one too old to know how is ended by its process id (the address in the pointer answered, so that process is the one)."""
    base, _, token = running["url"].partition("/?t=")
    try:
        urllib.request.urlopen(urllib.request.Request(f"{base}/api/quit?t={token}", data=b"{}", method="POST", headers={"Content-Type": "application/json"}), timeout=3).read()
    except Exception:
        pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and _alive(running["url"]):
        time.sleep(0.2)
    if _alive(running["url"]) and running.get("pid"):
        try:
            os.kill(int(running["pid"]), signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and _alive(running["url"]):
            time.sleep(0.2)


def cmd_dashboard(args):
    """Serve the dashboard and open it. Only one runs per Home: asking again opens the running one on the session you mean (a new tab), it does not start a second."""
    try:
        select = paths.resolve_session(args.ws, args.session)
    except paths.PathError:
        select = None
    pointer = paths.home() / "dashboard.json"
    running = read_json(pointer, None)
    if running and _alive(running["url"]):
        if running.get("build") == build_id():
            url = running["url"] + (f"#{quote(select[0])}/{quote(select[1])}" if select else "")
            if not args.no_open:
                webbrowser.open(url)
            return {"url": url, "reused": True}, f"Dashboard already running: {url}"
        _retire(running)                         # started from older files: its pages and its answers no longer match, so a new one takes its place

    def announce(url):
        write_json(pointer, {"url": url, "pid": os.getpid(), "build": build_id()})
        print(f"Dashboard: {url}", flush=True)

    try:
        dashboard_server.Dashboard(select, args.timeout).serve(announce, open_browser=not args.no_open, forever=True)
    finally:
        pointer.unlink(missing_ok=True)
    return {"stopped": True}, "Dashboard stopped."


def _write_snippets(ws, data, lang, folder=None):
    """One file of ready lines per model that has a form, into the workspace's knowledge/snippets. Returns (folder, written, skipped)."""
    folder = Path(folder).expanduser() if folder else paths.workspace_dir(ws) / "knowledge" / "snippets"
    folder.mkdir(parents=True, exist_ok=True)
    done, skipped = [], []
    for key, node in sorted(data["nodes"].items()):
        if node["kind"] != "view" or node["type"] != "form" or node["model"] in done:
            continue
        try:
            (folder / (re.sub(r"[^\w.-]+", "_", node["model"]) + ".txt")).write_text(sitemap.codegen(data, key, lang) + "\n", encoding="utf-8")
            done.append(node["model"])
        except sitemap.SitemapError:
            skipped.append(node["model"])
    return folder, done, skipped


def cmd_prepare(args):
    """Everything about the Odoo that does not depend on a case, once: the sitemap (read only, nothing is clicked) and the ready lines of every screen. Run by init, and
    by `session new` when it was never done, so no step waits for it. Again only when asked (--force) or when the sitemap is older than --max-age hours."""
    ws = workspace.load(paths.resolve_workspace(args.ws))
    lang = sitemap.output_language(ws.get("language"))
    file = sitemap.file_of(ws["name"])
    snippets = paths.workspace_dir(ws["name"]) / "knowledge" / "snippets"
    age = (time.time() - file.stat().st_mtime) / 3600 if file.exists() else None
    if args.status:
        n = len(list(snippets.glob("*.txt"))) if snippets.exists() else 0
        text = (f"Sitemap built {age:.1f}h ago, lines for {n} screen(s)." if age is not None else "Not prepared yet: run `oqa prepare` (or open a session, which starts it).")
        return {"prepared": age is not None, "age_hours": age, "screens": n}, text
    if age is not None and not args.force and age < args.max_age:
        if not any(snippets.glob("*.txt")) if snippets.exists() else True:          # the sitemap is there but its lines are not (made before they existed): no browser needed
            folder, written, skipped = _write_snippets(ws["name"], sitemap.load(ws["name"], lang), lang)
            return {"skipped": False, "screens": len(written)}, f"The sitemap is recent ({age:.1f}h); wrote the lines for {len(written)} screen(s) in {folder} from it."
        return {"skipped": True, "age_hours": age}, f"Already prepared ({age:.1f}h ago): nothing to do. `--force` builds it again."
    from .odoo_ui import Odoo
    started = time.monotonic()
    with probe.signed_in(None, ws, default_store(), args.profile, headless=True) as page:
        o = Odoo(page, ws["url"], ws["test_prefix"])
        data = sitemap.build(o.rpc, args.profile or ws["profiles"][0]["name"], page.evaluate(connect.VERSION_JS) or "", languages=args.languages, lang=lang)
    sitemap.save(ws["name"], data)
    folder, written, skipped = _write_snippets(ws["name"], data, lang)
    seconds = round(time.monotonic() - started)
    return {"sitemap": str(file), "screens": len(written), "skipped": len(skipped), "seconds": seconds}, (
        f"Prepared in {seconds}s: {sitemap.header(data, lang)}\nLines for {len(written)} screen(s) in {folder} ({len(skipped)} skipped). Read one with `oqa sitemap codegen <screen>`.")


def cmd_sitemap(args):
    """The sitemap of the Odoo under test: `build` reads its menus, actions and view definitions (read only, nothing is clicked); the rest reads the saved file."""
    if args.what == "build":
        from .odoo_ui import Odoo
        session, ws = _session_and_workspace(args)
        lang = sitemap.output_language(args.lang or ws.get("language"))
        with probe.signed_in(session, ws, default_store(), args.profile, headless=not args.headed) as page:
            o = Odoo(page, ws["url"], ws["test_prefix"])
            data = sitemap.build(o.rpc, args.profile or ws["profiles"][0]["name"], page.evaluate(connect.VERSION_JS) or "", languages=args.languages, lang=lang)
        file = sitemap.save(ws["name"], data)
        m = data["meta"]
        return {"file": str(file), "meta": m, "unresolved": data["unresolved"]}, "\n".join(
            [sitemap_text(lang, "Sitemap written to {file}", file=file), sitemap.header(data, lang), sitemap_text(lang, "{buttons} buttons call a method (where they lead is only known by running them); {unresolved} things could not be read.", buttons=m['object_buttons'], unresolved=len(data['unresolved']))]
            + [f"  {u['where']}: {sitemap.unresolved_text(u, lang)}" for u in data["unresolved"][:10]])
    ws = paths.resolve_workspace(args.ws)
    lang = sitemap.output_language(args.lang or workspace.load(ws).get("language"))
    data = sitemap.load(ws, lang)
    if args.what == "show":
        return {"meta": data["meta"], "unresolved": data["unresolved"]}, sitemap.header(data, lang)
    if args.what == "codegen" and args.all:
        folder, done, skipped = _write_snippets(ws, data, lang, args.out)
        return {"folder": str(folder), "written": done, "skipped": skipped}, f"Wrote lines for {len(done)} screen(s) to {folder}; {len(skipped)} skipped. Read one with `oqa sitemap codegen <screen>` instead of opening files."
    if not args.target:
        raise GateError(f"Say what to look for: oqa sitemap {args.what} <words>")
    if args.what == "codegen":
        text = sitemap.codegen(data, args.target, lang)
        return {"text": text}, text
    text = {"find": lambda: sitemap.text_find(data, args.target, args.limit or 12, args.kind, lang=lang), "buttons": lambda: sitemap.text_buttons(data, args.target, args.view, args.match, args.limit or 30, lang=lang),
            "fields": lambda: sitemap.text_fields(data, args.target, args.view, args.match, args.required, args.limit or 40, lang=lang), "path": lambda: sitemap.text_path(data, args.source, args.target, lang=lang)}[args.what]()
    return {"text": text}, text


def cmd_mcp(args):
    """Run the sitemap MCP server on stdin/stdout, or with --config print how to register it."""
    root = Path(__file__).resolve().parents[1]
    if args.config:
        entry = {"command": "python", "args": [str(root / "oqa.py"), "mcp"] + (["--ws", args.ws] if args.ws else [])}
        snippet = {"mcpServers": {"oqa-sitemap": entry}}
        return snippet, json.dumps(snippet, indent=2)
    mcp_server.serve(args.ws)
    return {}, ""


def cmd_hooks(args):
    if args.install:
        try:
            path, added, backup = globalhooks.install(args.settings)
        except (OSError, ValueError) as error:
            raise GateError(f"{globalhooks.settings_path(args.settings)} was not changed: {error}")
        if not added:
            return {"installed": [], "path": str(path)}, f"Already installed in {path}. Nothing changed."
        return {"installed": added, "path": str(path), "backup": str(backup) if backup else None}, (
            f"Added {', '.join(added)} to {path}" + (f" (the old file is kept as {backup})" if backup else "") + ". Open a new Claude Code window: hooks are read when it starts.")
    """The three hooks as a snippet for settings.json, with this skill's real path. The hooks in SKILL.md's frontmatter use ${CLAUDE_PLUGIN_ROOT} (checked on
    Claude Code 2.1.325: it is the skill's folder there; ${CLAUDE_SKILL_DIR} expands to nothing in a hook). Use this where that does not hold."""
    root = Path(__file__).resolve().parents[1]
    exe = "python" if sys.platform == "win32" else "python3"      # a Linux or macOS machine often has no `python`
    command = lambda name: f'{exe} "{root / "hooks" / name}"'
    snippet = {"hooks": {"PreToolUse": [{"matcher": "Bash|Write|Edit|MultiEdit|Read|Grep", "hooks": [{"type": "command", "command": command("guard.py")}]}],
                         "Stop": [{"hooks": [{"type": "command", "command": command("stop.py")}]}],
                         "UserPromptSubmit": [{"hooks": [{"type": "command", "command": command("prompt.py")}]}]}}
    return snippet, json.dumps(snippet, indent=2)


def cmd_plugin(args):
    """Build the plugin from this folder (the one source of truth). `--apply` builds it where it is installed and tells Claude Code to take the new version."""
    import subprocess
    from . import plugin
    if args.apply:
        target = plugin.build(Path.home() / ".odoo-test" / "plugin")
        done = subprocess.run(["claude", "plugin", "update", f"{plugin.NAME}@{plugin.NAME}-local"], capture_output=True, text=True) if shutil.which("claude") else None
        note = (done.stdout + done.stderr).strip().splitlines()[-1:] if done else ["`claude` was not found: run `claude plugin update odoo-test@odoo-test-local` yourself"]
        return {"plugin": str(target), "updated": bool(done and done.returncode == 0)}, f"Built {target} from {plugin.ROOT}.\n" + "\n".join(note) + "\nOpen a new Claude Code window to use it."
    if not args.out:
        raise GateError("Say where to build: --out <folder>, or --apply to refresh the installed plugin.")
    target = plugin.build(args.out)
    return {"plugin": str(target), "marketplace": str(target.parent)}, (
        f"Built the plugin at {target}\nTry it once:  claude --plugin-dir {target}\nInstall it:   /plugin marketplace add {target.parent}   then   /plugin install {plugin.NAME}@{plugin.NAME}-local\n"
        "Its hooks start with it, so remove the two this skill added to settings.json (oqa hooks --install) to avoid running them twice.")


def cmd_publish(args):
    """Publish the runnable skill and build its Claude Code plugin."""
    import subprocess
    from . import plugin
    target = Path(args.skills).expanduser().resolve() / plugin.NAME
    _install_skill(target, force=True)
    lines = [f"Skill published to {target}"]
    built = plugin.build(Path.home() / ".odoo-test" / "plugin")
    done = subprocess.run(["claude", "plugin", "update", f"{plugin.NAME}@{plugin.NAME}-local"], capture_output=True, text=True, encoding="utf-8", errors="replace") if shutil.which("claude") else None
    lines.append(f"Plugin rebuilt at {built}" + (f" ({(done.stdout + done.stderr).strip().splitlines()[-1]})" if done and (done.stdout + done.stderr).strip() else ""))
    if shutil.which("claude") and f"{plugin.NAME}@{plugin.NAME}-local" not in (subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout or ""):
        for step in (["marketplace", "add", str(built.parent)], ["install", f"{plugin.NAME}@{plugin.NAME}-local"]):
            run = subprocess.run(["claude", "plugin", *step], capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines.append(("Plugin: " if run.returncode == 0 else "Plugin step failed: ") + " ".join(step[:2]) + (f" - {(run.stdout + run.stderr).strip().splitlines()[-1]}" if (run.stdout + run.stderr).strip() else ""))
    lines.append("Open a new Claude Code window to use the new version.")
    return {"skill": str(target), "plugin": str(built)}, "\n".join(lines)


def cmd_repo(args):
    """Refresh the installable plugin committed in this repository (plugins/odoo-test and .claude-plugin/marketplace.json)."""
    from . import plugin
    target = plugin.build_repo()
    return {"plugin": str(target)}, f"Refreshed {target}. Commit and push it; then anyone can run:\n  /plugin marketplace add <owner>/<repo>\n  /plugin install {plugin.NAME}@{plugin.REPO_MARKET}"


def cmd_install(args):
    """Copy the runnable skill into a skills folder."""
    from . import plugin
    target = Path(args.to).expanduser().resolve() / plugin.ROOT.name
    _install_skill(target, force=args.force)
    return {"installed": str(target)}, f"Installed to {target}"


def _install_skill(target, *, force):
    from . import plugin
    if target.resolve() != target or target == plugin.ROOT or target in plugin.ROOT.parents:
        raise GateError(f"Cannot replace {target} with a skill install.")
    if target.exists():
        if not force:
            raise GateError(f"{target} already exists. Use --force to replace it.")
        if not (target / "oqa.py").exists():
            raise GateError(f"{target} does not look like this skill; remove it yourself if you are sure.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        prepared = Path(staging) / target.name
        plugin.copy_skill(prepared)
        if target.exists():
            shutil.rmtree(target)
        prepared.rename(target)


def cmd_ask(args):
    session = Session.open(args.ws, args.session)
    session.ask(args.question, args.case)
    return {"waiting": True}, "Question recorded. Show it to the user and stop until they answer; then run `oqa answer`."


def cmd_answer(args):
    session = Session.open(args.ws, args.session)
    asked = session.answer(args.text)
    session.mark_seen()                      # the assistant ran this itself: it is not news to it
    return {"answered": asked["question"]}, "Answer recorded. You can continue."


def _describe(event):
    """One user action, as a sentence that says what to do about it."""
    kind, case = event["kind"], event.get("case")
    if kind == "confirmed":
        return "The user confirmed the strategy. Check the execution queue with oqa status."
    if kind == "answered":
        if event.get("skipped"):
            return f"The user skipped {case}: {event['answer']}"
        shared = event.get("files") or []
        return (f"The user answered{f' (about {case})' if case else ''}: {event['answer']}"
                + (f"\nThey shared {len(shared)} file(s) by path: read each with `oqa ref show \"<name>\"` ({'; '.join(shared)})." if shared else ""))
    if kind == "ref_added":
        return f"The user shared {event.get('what', 'file')} {event['path']}: read it with `oqa ref show \"{event.get('name', event['path'])}\"`."
    if kind == "hint":
        return f"The user sent a hint for {case}: {event['text']}\n{case} is back in the queue with a fresh budget."
    if kind == "stopped":
        return "The user stopped the test. Do nothing until they resume it: run `oqa wait` again."
    return "The user resumed the test. Check the execution queue with oqa status."


def cmd_wait(args):
    """Block until the user does something on the dashboard (confirm, answer, hint, stop, resume), then say what. Run it in the background after handing over to the
    user; the assistant is told when it ends."""
    session = Session.open(args.ws, args.session)
    deadline = time.monotonic() + args.timeout
    last_beat = 0.0
    try:
        while True:
            if time.monotonic() - last_beat >= autostart.HEARTBEAT_SECONDS:
                autostart.beat(session)
                last_beat = time.monotonic()
            available = session.news()
            wait_for_reviews = getattr(args, "until", None) == "reviews" and bool(session.state().get("reviews"))
            if wait_for_reviews and not any(event["kind"] == "stopped" for event in available):
                available = []
            news = session.consume_news() if available else []
            if news:
                return {"events": news}, "\n".join(_describe(e) for e in news)
            if time.monotonic() > deadline:
                raise GateError(f"The user did nothing in {args.timeout} seconds. Run `oqa wait` again if you are still waiting for them.")
            time.sleep(0.5)
    finally:
        autostart.clear_beat(session)


def cmd_stop(args):
    session = Session.open(args.ws, args.session)
    done = session.stop("terminal")
    return {"stopped": True, **done}, (f"Stopped. Ended {done['killed']} process(es) of this session, gave back {len(done['released'])} claim(s) and marked "
                                       f"{len(done['closed'])} open run(s) STOPPED. Nothing starts until the user resumes.")


def cmd_repair(args):
    session = Session.open(args.ws, args.session)
    note = session.repair(args.case, args.cause, args.evidence, args.change, reanalysis=args.reanalysis or "", timeout_s=args.timeout,
                          unchanged_reason=args.unchanged or "")
    return note, f"Repair note for {args.case} (run {note['attempt']}) recorded. Next: oqa run {args.case}"


def cmd_resume(args, is_tty=None):
    session = Session.open(args.ws, args.session)
    if not ((sys.stdin.isatty() and sys.stdout.isatty()) if is_tty is None else is_tty):
        raise GateError("Only the user resumes a test they stopped: run `oqa resume` in your own terminal or press Resume in the dashboard.")
    session.resume("terminal")
    return {"stopped": False}, "Resumed."


def cmd_status(args):
    session = Session.open(args.ws, args.session)
    summary = session.summary()
    lines = [f"{summary['workspace']} / {summary['session']}  phase {summary['phase']}  ({summary['cases_done']}/{summary['cases_planned']} cases done)"]
    lines.append("STOPPED BY THE USER: do nothing until they resume (oqa wait)" if summary["stopped"] else
                 "WAITING FOR THE USER: " + summary["waiting"]["question"] if summary["waiting"] else "Next: " + summary["next"])
    if summary["needs_repair"]:
        lines.append("Failed and not yet explained: " + ", ".join(summary["needs_repair"]) + ". Before running again: oqa repair <case> --cause ... --evidence ... --change ...")
    lines += [_describe(e) for e in session.news()]
    return summary, "\n".join(lines)






def cmd_knowledge(args):
    ws = paths.resolve_workspace(args.ws)
    if args.what == "learn":
        result = knowledge.learn(ws, args.screen, args.kind, args.fact, args.tag, args.ref,
                                 action=args.action, condition=args.condition, profile=args.profile,
                                 state=args.state, evidence=args.evidence)
        return {"result": result}, f"Knowledge {result}."
    found = knowledge.search(ws, args.screen, args.kind, args.text)
    return {"items": found}, "\n".join(knowledge.describe(item) for item in found) or "Nothing known yet."


# --- wiring -----------------------------------------------------------------------
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ws", help="workspace name (default: $OQA_WS, or the only one)")
    common.add_argument("--session", help="session id (default: $OQA_SESSION, or the workspace's current one)")
    common.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser = argparse.ArgumentParser(prog="oqa", description="odoo-test: test Odoo from a testcase workbook, in one session per request.")
    parser.add_argument("--version", action="version", version=f"oqa {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_, **kw):
        p = sub.add_parser(name, parents=[common], help=help_, **kw)
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "open the configuration form and wait until it is saved")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--no-open", action="store_true", help="do not open the browser, only print the address")
    p.add_argument("--status", action="store_true", help="only say whether a workspace is configured")
    p = add("prepare", cmd_prepare, "prepare the environment once: the sitemap and the ready lines of every screen (init and `session new` start it for you)")
    p.add_argument("--status", action="store_true", help="only say whether it was done and how old it is")
    p.add_argument("--force", action="store_true", help="build it again even when it is recent")
    p.add_argument("--max-age", type=float, default=24.0, help="hours after which it is built again (default 24)")
    p.add_argument("--languages", help="comma-separated active Odoo languages en_US,ja_JP (default: both when active)")
    p.add_argument("--profile")
    p = add("doctor", cmd_doctor, "check this computer (and --fix what is missing)")
    p.add_argument("--fix", action="store_true")
    p = add("ws", cmd_ws, "show or list workspaces")
    p.add_argument("what", choices=["show", "list"])
    p = add("session", cmd_session, "new / show / list sessions")
    p.add_argument("what", choices=["new", "show", "list", "cases"])
    p.add_argument("--file", help="testcase workbook (.xlsx) for `new` / `cases`")
    p.add_argument("--cases", help="new: test only these cases of the workbook: ids, A..C ranges or words of the title, comma-separated (default: all of them)")
    p = add("case", cmd_case, "get original case, analysis, feedback and related workspace knowledge")
    p.add_argument("action", choices=["context"])
    p.add_argument("case")
    p.add_argument("--screen", default="")
    p.add_argument("--issue", default="")
    p.add_argument("--only", choices=["sitemap", "knowledge"], help="return just this context section")
    p.add_argument("--profile", default="", help="look up knowledge for this role")
    p.add_argument("--state", default="", help="look up knowledge for this initial state")
    p = add("fixture", cmd_fixture, "plan and create approved test records")
    p.add_argument("action", choices=["plan", "apply", "advise", "ui", "ref", "key", "cleanup"])
    p.add_argument("screen", nargs="?", default="", help="a screen/model, or ledger | license_user | contract; not needed for key and cleanup")
    p.add_argument("--case")
    p.add_argument("--label", help="key: a short name for the record within the case")
    p.add_argument("--value", help="ref: the exact name of an existing record to rely on (read only)")
    p.add_argument("--run", help="cleanup: a replay's run id (rXXXXXXXXXX) instead of the live run")
    p.add_argument("--apply", action="store_true", help="cleanup: really remove the tracked records (default: list them)")
    p.add_argument("--values", help="apply: JSON file with model values")
    p.add_argument("--values-json", help="apply: inline JSON object of model values")
    p.add_argument("--key", help="apply: unique writable field with the workspace test prefix")
    p.add_argument("--out", help="apply: reusable Playwright setup fragment")
    p.add_argument("--need", help="advise: describe the data the case needs")
    p = add("run", cmd_run, "choose a workspace before starting a workbook run")
    p.add_argument("action", choices=["start"])
    p.add_argument("--file", required=True)
    p.add_argument("--cases")
    p.add_argument("--channel", choices=["path", "addin"], default="path")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--new-session", action="store_true", help="start a separate run instead of reusing this workbook's pending review session")
    p = add("analyze", cmd_analyze, "get the analysis template, or hand in the analysis")
    p.add_argument("--template", action="store_true")
    p.add_argument("--no-context", action="store_true", help="omit sitemap and knowledge snippets from the template")
    p.add_argument("--submit", metavar="FILE")
    p.add_argument("--out", metavar="FILE", help="with --template: write it here instead of printing")
    add("strategy", cmd_strategy, "build the strategy the user must approve")
    add("confirm", cmd_confirm, "the USER approves the strategy (needs a real terminal)")
    p = add("connect", cmd_connect, "sign every profile in and record how this Odoo behaves after login")
    p.add_argument("--headed", action="store_true", help="show the browser (for a two-factor code only the user can enter)")
    p = add("assign", cmd_assign, "divide the cases without a result between agents (max cases each, difficulty spread evenly)")
    p.add_argument("--agents", type=int, default=0, help="how many agents (default: as few as the maximum allows)")
    p.add_argument("--max", type=int, default=5, help="most cases per agent (default 5)")
    p = add("claim", cmd_claim, "take the next free case (easy and independent first); a claim lasts while the case sees activity")
    p.add_argument("--packet", action="store_true", help="include the case context for the worker")
    p.add_argument("--agent", required=True, help="your name, the same in every call")
    p.add_argument("--case", default="", help="take this exact case (the one you were given) instead of the next free one")
    p = add("release", cmd_release, "give a claimed case back")
    p.add_argument("case")
    add("claims", cmd_claims, "who holds which case, and which cases are free")
    p = add("dispatch", cmd_dispatch, "run the approved queue with Claude CLI case workers")
    p.add_argument("--worker-model", default="haiku")
    p.add_argument("--coordinator-model", default="sonnet")
    p.add_argument("--worker-timeout", type=int, default=480)
    p = add("worker-mcp", cmd_worker_mcp, "serve only the assigned case tools over stdio")
    p.add_argument("case")
    p.add_argument("--agent", required=True)
    p = add("note", cmd_note, "share something every agent of this session must know (data you made, a name, a trap)")
    p.add_argument("text", nargs="+")
    p.add_argument("--agent", default="")
    p.add_argument("--case", default="")
    p = add("notes", cmd_notes, "read what the agents shared")
    p.add_argument("--limit", type=int, default=30)
    p = add("regress", cmd_regress, "regression: replay every saved script of the workspace on one environment, or --matrix to see the latest results")
    p.add_argument("--env", help="the environment to run on (see `oqa ws show`)")
    p.add_argument("--matrix", action="store_true", help="only show the latest result of each script on each environment")
    p.add_argument("--cases", help="only these case ids, comma-separated")
    p.add_argument("--headed", action="store_true", help="show the browser (regression runs without a window by default)")
    p.add_argument("--caption-language", choices=("vi", "en", "ja"), help="override the workspace caption language")
    p.add_argument("--action-delay-ms", type=int, default=0, help="pause after each action in milliseconds (default 0)")
    p.add_argument("--step-delay-ms", type=int, default=0, help="pause after each checked step in milliseconds (default 0)")
    p.add_argument("--fast", action="store_true", help="both viewing pauses 0")
    p.add_argument("--video", choices=list(live.VIDEO_MODES), help="video with a note of each step: off | on (keep every video) | fail (keep only the video of a failed script). Default: fail, but off with --fast")
    p = add("results", cmd_results, "import results of scripts run by hand: results import <result.json or the evidence folder>")
    p.add_argument("what", choices=["import"])
    p.add_argument("path", help="a result.json written by replay.mjs, or a folder holding several")
    p = add("pgadmin", cmd_pgadmin, "database evidence: pgadmin shot --sql \"SELECT ...\" [CASE --n N] | pgadmin check")
    p.add_argument("action", choices=["shot", "check"])
    p.add_argument("case", nargs="?", help="shot: the case whose recorded step gets the picture (without it the picture is only saved to --out)")
    p.add_argument("--n", type=int, help="the recorded step number the picture belongs to")
    p.add_argument("--sql", help="one reading query (SELECT, WITH, SHOW, EXPLAIN)")
    p.add_argument("--out", help="without a case: where to save the picture (default pgadmin.png)")
    p.add_argument("--caption", help="the text under the picture")
    p.add_argument("--headed", action="store_true", help="show the browser")
    p = add("live", cmd_live, "run a case live in its own playwright-cli browser: start | shot | step | end")
    p.add_argument("action", choices=["start", "act", "profile", "shot", "step", "end", "replay", "script", "recover", "diagnose", "handover", "regenerate"])
    p.add_argument("case", help="the case id; `replay all` replays every saved script of the session")
    p.add_argument("--n", type=int, help="the step number (shot, step)")
    p.add_argument("--status", help="step: PASS or FAIL; end: PASS, FAIL, BLOCKED or PENDING")
    p.add_argument("--actual", help="step: what the page showed, in a few words (compared with the expected result)")
    p.add_argument("--target", help="a locator of the element the step is about (CSS or getByRole(...)); oqa frames it in the picture")
    p.add_argument("--script", help="end PASS or live script: the file with the Playwright code playwright-cli printed for your actions (kept as the case's script)")
    p.add_argument("--reason", help="end: why, when the case is not PASS")
    p.add_argument("--headless", action="store_true", help="replay: no visible window (the default shows the browser)")
    p.add_argument("--action-delay-ms", type=int, default=live.DEFAULT_ACTION_DELAY_MS,
                   help="replay: pause after each action in milliseconds (0 disables)")
    p.add_argument("--step-delay-ms", type=int, default=live.DEFAULT_STEP_DELAY_MS,
                   help="replay: pause after each checked step in milliseconds (0 disables)")
    p.add_argument("--category", choices=["application", "automation", "precondition"], help="end/diagnose: what the conclusion means (FAIL is always application; BLOCKED/PENDING are automation or precondition)")
    p.add_argument("--control", help="end FAIL: the control that was operated and how it was verified to be the one the testcase means")
    p.add_argument("--detail", help="diagnose: what you found, concretely")
    p.add_argument("--tried", help="diagnose (automation): what was already tried")
    p.add_argument("--need", help="diagnose (precondition): what is needed to continue")
    p.add_argument("--to", help="handover: the worker that takes the running case")
    p.add_argument("--env", help="replay: the environment to run on (default main); see `oqa ws show`")
    p.add_argument("--video", choices=list(live.VIDEO_MODES), help="replay: video with a note of each step: off (default) | on | fail (keep it only if the replay fails)")
    p.add_argument("--model", default="sonnet", help="regenerate: the model that rewrites the script (default sonnet)")
    p.add_argument("--no-verify", action="store_true", help="regenerate: do not replay the new script (only the static checks)")
    p.add_argument("--attempts", type=int, default=3, help="regenerate: how many rewrites to try before restoring the original script")
    p.add_argument("--fast", action="store_true", help="replay: fast mode, both viewing pauses 0 (application waits are unchanged)")
    p.add_argument("--caption", help="shot: describe the checkpoint shown in this evidence picture")
    p.add_argument("--caption-language", choices=["vi", "en", "ja"], help="replay: override the workspace caption language")
    p.add_argument("--verified-code", help="step: file containing only the confirmed Playwright actions")
    p.add_argument("--from-pre", action="store_true", help="shot: attach a saved live pre-action picture to an already recorded step")
    p.add_argument("--code", help="act or step: one verified Playwright action per line")
    p.add_argument("--shows", help="step: expected page text for replay")
    p.add_argument("--browser-command", help="act: playwright-cli operation in this case's browser")
    p.add_argument("--browser-arg", action="append", default=[], help="act: argument for the browser operation")
    p.add_argument("--profile", help="profile: switch the active case to another signed-in account")
    p = add("finish", cmd_finish, "record FAIL (with evidence), BLOCKED or PENDING for a case you stop working on")
    p.add_argument("case")
    p.add_argument("--status", required=True, choices=["FAIL", "BLOCKED", "PENDING"])
    p.add_argument("--reason", required=True)
    p.add_argument("--category", choices=["automation", "precondition"], help="BLOCKED/PENDING: automation issue or missing test precondition")
    p = add("repair", cmd_repair, "after a failed run: record the cause, the evidence and the change; the next run of the case needs it")
    p.add_argument("case")
    p.add_argument("--cause", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--change", required=True)
    p.add_argument("--reanalysis", help="when the same failure came twice: how you now read the case")
    p.add_argument("--timeout", type=int, help="seconds for this case's steps")
    p.add_argument("--unchanged", help="why the script is unchanged (for example: data created through the UI)")
    p = add("sitemap", cmd_sitemap, "the map of this Odoo's screens: build it once, then find a screen, its buttons or the way between two screens")
    p.add_argument("what", choices=["build", "show", "find", "buttons", "fields", "path", "codegen"])
    p.add_argument("target", nargs="?", help="find: words; buttons / fields: a screen (key or words); path: the screen to reach")
    p.add_argument("--from", dest="source", default="", help="path: the screen to start from (default: the menus)")
    p.add_argument("--kind", default="", choices=["", "menu", "action", "view"], help="find: only this kind of screen")
    p.add_argument("--view", default="", help="buttons / fields: only this view type (form, list, kanban...); fields: the form by default")
    p.add_argument("--match", default="", help="buttons / fields: only those whose label or name contains this")
    p.add_argument("--required", action="store_true", help="fields: only the fields that are, or can be, required")
    p.add_argument("--limit", type=int, default=0, help="most lines to print (find 12, buttons 30, fields 40)")
    p.add_argument("--languages", help="build: comma-separated active Odoo languages en_US,ja_JP (default: both when active)")
    p.add_argument("--lang", choices=["en", "ja", "en_US", "ja_JP"], help="response language (default: workspace language, English for Vietnamese workspaces)")
    p.add_argument("--all", action="store_true", help="codegen: write lines for every screen that has a form, one file per model")
    p.add_argument("--out", help="codegen --all: the folder to write to (default: the workspace's knowledge/snippets)")
    p.add_argument("--profile")
    p.add_argument("--headed", action="store_true")
    p = add("mcp", cmd_mcp, "serve the sitemap to the assistant as an MCP server (stdio); --config prints the .mcp.json entry")
    p.add_argument("--config", action="store_true")
    p = add("report", cmd_report, "write the Excel evidence per case and the summary; closes the session")
    p.add_argument("--partial", action="store_true", help="write what exists now without closing the session")
    p.add_argument("--no-open", action="store_true", help="do not open the workbook")
    p = add("dashboard", cmd_dashboard, "open the progress dashboard (history, cases, steps, pictures)")
    p.add_argument("--timeout", type=int, default=4 * 3600, help="seconds before it stops by itself")
    p.add_argument("--no-open", action="store_true")
    p = add("hooks", cmd_hooks, "print the skill's hooks as a settings.json snippet (absolute paths); --install writes the two that must work before the skill is loaded")
    p.add_argument("--install", action="store_true", help="add prompt.py and stop.py to Claude Code's settings.json (keeps a backup, changes nothing else)")
    p.add_argument("--settings", help="another settings.json than ~/.claude/settings.json")
    p = add("plugin", cmd_plugin, "build this skill as a Claude Code plugin (hooks included, nothing written into settings.json)")
    p.add_argument("--out", help="the folder to build into (a marketplace folder that holds the plugin)")
    p.add_argument("--apply", action="store_true", help="rebuild the installed plugin (~/.odoo-test/plugin) from this folder and update it in Claude Code")
    p = add("publish", cmd_publish, "publish the runnable skill and build its Claude Code plugin")
    p.add_argument("--skills", default="~/.agents/skills", help="the skills folder to write into (default ~/.agents/skills)")
    add("repo", cmd_repo, "refresh the plugin committed in this repo so it installs from GitHub (/plugin marketplace add <owner>/<repo>)")
    p = add("install", cmd_install, "copy this skill into a skills folder")
    p.add_argument("--to", required=True, help="the skills folder (e.g. ~/.claude/skills or %%USERPROFILE%%/.excel-partner/skills)")
    p.add_argument("--force", action="store_true", help="replace an existing copy")
    p = add("ask", cmd_ask, "ask the user something and wait")
    p.add_argument("question")
    p.add_argument("--case")
    p = add("wait", cmd_wait, "block until the user acts on the dashboard (confirm, answer, hint, stop, resume), then say what")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--until", choices=["reviews"], help="wait until all per-case reviews are answered or skipped")
    add("stop", cmd_stop, "the USER stops the test (the step in progress finishes)")
    add("resume", cmd_resume, "the USER lets a stopped test go on (needs a real terminal)")
    p = add("answer", cmd_answer, "record the user's reply (a file path written in it becomes a reference)")
    p.add_argument("text")
    p = add("ref", cmd_ref, "files and folders the user pointed at by path: add, list, show (read their content, nothing is uploaded)")
    p.add_argument("what", choices=["add", "list", "show"])
    p.add_argument("target", nargs="?", help="add: the path; show: a reference's name or path, or the testcase workbook (saves its embedded pictures too)")
    p.add_argument("--case", help="add: the case this file is for")
    p.add_argument("--sheet", help="show: one sheet of a workbook")
    p.add_argument("--max", type=int, default=6000, help="show: most characters to print")
    add("status", cmd_status, "phase, progress and the next command")
    p = add("knowledge", cmd_knowledge, "search or add what was learned about this Odoo")
    p.add_argument("what", choices=["search", "learn"])
    p.add_argument("--screen")
    p.add_argument("--kind")
    p.add_argument("--text")
    p.add_argument("--fact")
    p.add_argument("--tag", default="")
    p.add_argument("--ref", default="-")
    p.add_argument("--action", default="", help="learn: operation the fact applies to")
    p.add_argument("--condition", default="", help="learn: business condition the fact applies to")
    p.add_argument("--profile", default="", help="learn: role the fact applies to")
    p.add_argument("--state", default="", help="learn: initial state the fact applies to")
    p.add_argument("--evidence", default="", help="learn: what to observe or capture")
    return parser


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        data, text = args.func(args)
    except REFUSED as error:
        message = "\n".join(error.problems) if isinstance(error, SchemaError) else str(error)
        if args.json:
            print(json.dumps({"refused": True, "error": message}, ensure_ascii=False))
        else:
            print("REFUSED: " + message, file=sys.stderr)
        return 2
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else text)
    return 1 if args.func is cmd_regress and isinstance(data, dict) and data.get("failed") else 0      # a regression with a failure fails a pipeline
