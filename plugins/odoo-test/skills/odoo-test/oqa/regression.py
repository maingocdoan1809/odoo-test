"""Regression results of the saved scripts, kept per workspace and per environment, and the run of every script on one environment.

A script belongs to the workspace (scripts/<workbook>/<case>.spec.js), not to a session, so its regression result does too:

regression.json = {schema_version, scripts: {"<workbook>/<case>": {<environment>: {latest: {...}, runs: [{...}]}}}}
Each run is a replay run (status PASS|FAIL, at, reason, failed_step, run_id, timing, ...) plus the environment it ran on.
A script never run on an environment is NOT RUN there, never an implied pass."""
import hashlib
import os
import re
from pathlib import Path

from . import SCHEMA_VERSION, captions, memory, paths, record_names, replays, workspace
from .state import GateError
from .store import locked, read_json, write_json

NOT_RUN = "NOT RUN"
KEEP_RUNS = 20


def _file(ws):
    return paths.workspace_dir(ws) / "regression.json"


def legacy_key(workbook_file, case):
    return f"{memory.stem_of(workbook_file)}/{re.sub(r'[^\w.-]+', '_', case)}"


def key_of(workbook_file, case):
    workbook, case_name = legacy_key(workbook_file, case).split("/", 1)
    path_id = hashlib.sha256(os.path.normcase(str(Path(workbook_file).resolve())).encode("utf-8")).hexdigest()[:16]
    case_id = hashlib.sha256(case.encode("utf-8")).hexdigest()[:12]
    return f"{workbook[:64]}-{path_id}/{case_name[:64]}-{case_id}"


def record(ws, key, env, entry):
    with locked(_file(ws)):
        data = read_json(_file(ws), {}) or {"schema_version": SCHEMA_VERSION, "scripts": {}}
        slot = data["scripts"].setdefault(key, {}).setdefault(env, {"runs": []})
        slot["runs"] = [*slot["runs"], {**entry, "env": env}][-KEEP_RUNS:]
        slot["latest"] = slot["runs"][-1]
        write_json(_file(ws), data)


def load(ws):
    return (read_json(_file(ws), {}) or {}).get("scripts", {})


def scripts(ws):
    """Every saved script of the workspace: [{key, workbook, case, file}], by workbook then case."""
    base = paths.workspace_dir(ws) / "scripts"
    out = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        for file in sorted(folder.glob("*.spec.js")):
            case = file.name[: -len(".spec.js")]
            out.append({"key": f"{folder.name}/{case}", "workbook": folder.name, "case": case, "file": file})
    from . import live
    by_key = {item["key"]: item for item in out}
    for key, (session, case) in sources(ws).items():
        workbook_file = session.state()["file"]
        file = live.script_file(session, case)
        for old_key in (legacy_key(workbook_file, case), key):
            if old_key in by_key and by_key[old_key]["file"] == file:
                del by_key[old_key]
        by_key[key] = {"key": key, "workbook": Path(workbook_file).name, "case": case, "file": file}
    return [by_key[key] for key in sorted(by_key)]


def _sessions_newest_first(ws):
    from .state import Session

    found = []
    for sid in paths.session_ids(ws):
        try:
            session = Session(ws, sid)
            state = session.state()
            confirmed = state.get("phase") in ("EXECUTE", "REPORT", "DONE")          # strategy.json exists before the user confirms it
            if confirmed and (session.dir / "strategy.json").is_file() and (session.dir / "analysis.json").is_file():
                found.append((state["created"], session, state))
        except Exception:
            continue
    return [s for _, s, _ in sorted(found, key=lambda item: item[0], reverse=True)]


def sources(ws):
    """{script key: (session, case id)}: for each script, the newest session whose confirmed strategy holds the case, because a replay needs its steps."""
    from . import live

    out = {}
    for session in _sessions_newest_first(ws):
        for case in read_json(session.dir / "strategy.json")["order"]:
            file = live.script_file(session, case)
            if file.is_file():
                out.setdefault(key_of(session.state()["file"], case), (session, case))
    return out


def _legacy_keys(origins):
    keys = {}
    for key, (session, case) in origins.items():
        keys.setdefault(legacy_key(session.state()["file"], case), set()).add(key)
    return keys


def matrix(ws):
    """What the dashboard shows: environments and, per script, the latest result on each (NOT RUN when there is none)."""
    config = workspace.load(ws)
    envs = [e["name"] for e in workspace.environments(config)]
    saved, known = load(ws), sources(ws)
    unique_legacy = {next(iter(keys)): old for old, keys in _legacy_keys(known).items() if len(keys) == 1}
    documents = {}
    rows = []
    for item in scripts(ws):
        origin = known.get(item["key"])
        previous = replays.load(origin[0]).get(origin[1], {}) if origin else {}
        title = item["case"]
        if origin:
            session, case = origin
            if session.id not in documents:
                analysis = read_json(session.dir / "analysis.json", {}) or {}
                documents[session.id] = ({row["id"]: row for row in session.cases()["cases"]},
                                         {row["id"]: row for row in analysis.get("cases", [])},
                                         captions.localized_cases(session))
            source, plans, translated = documents[session.id]
            title = record_names.title(source[case], plans.get(case, {}), translated.get(case, {}))
        results = {env: (saved.get(item["key"], {}).get(env, {}).get("latest")
                         or previous.get("envs", {}).get(env)
                         or (previous.get("latest") if env == workspace.MAIN_ENV else None)
                         or saved.get(unique_legacy.get(item["key"]), {}).get(env, {}).get("latest")
                         or {"status": NOT_RUN}) for env in envs}
        rows.append({"key": item["key"], "workbook": item["workbook"], "case": item["case"], "title": title, "results": results,
                     "session": origin[0].id if origin else None, "case_id": origin[1] if origin else None})
    return {"environments": envs, "scripts": rows, "replay_caption_language": config.get("replay_caption_language", "vi")}


def run(ws, env, cases=None, headed=False, video="fail", **delays):
    """Replay every saved script of the workspace on one environment (or only those whose case id is listed). One failure does not stop the rest."""
    from . import live

    env = workspace.for_env(workspace.load(ws), env)["env"]
    chosen = [(key, found) for key, found in sources(ws).items() if cases is None or found[1] in cases]
    if not chosen:
        raise GateError("This workspace has no saved script that can be replayed (a script needs a confirmed session that holds its case).")
    rows = []
    for key, (session, case) in chosen:
        try:
            result = live.replay(session, case, headed=headed, env=env, video=video, **delays)
            if result["replay"] == "CANCELLED":
                rows.append({"script": key, "case": case, "env": env, "replay": "CANCELLED", "reason": result["reason"]})
                break
            rows.append({"script": key, "case": case, "env": env, "replay": "PASS", "total_s": result["timing"]["total_s"]})
        except GateError as error:
            failed = replays.latest(session, case) or {}
            rows.append({"script": key, "case": case, "env": env, "replay": "FAIL", "reason": str(error)[:300], **({"video": failed["video"]} if failed.get("video") else {})})
    return rows


def import_results(ws, target):
    """Put the result.json of runs made by hand (node replay.mjs) into the workspace's regression results. Returns the imported entries; refuses what it cannot
    place (another workspace, an unknown environment or script, a status that is not PASS or FAIL) naming the file, and imports nothing from a refused file."""
    import json
    from pathlib import Path

    root = Path(target).expanduser()
    files = [root] if root.is_file() else sorted(root.rglob("result.json")) if root.is_dir() else []
    if not files:
        raise GateError(f"No result.json found at {target}.")
    config = workspace.load(ws)
    envs = {e["name"].lower(): e["name"] for e in workspace.environments(config)}
    known = {item["key"] for item in scripts(ws)}
    origins = sources(ws)
    legacy, by_case = _legacy_keys(origins), {}
    for key, (_, case) in origins.items():
        by_case.setdefault(case, set()).add(key)
    entries = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise GateError(f"{file}: not a readable result.json ({error}).") from error
        if not isinstance(data, dict) or data.get("status") not in ("PASS", "FAIL"):
            raise GateError(f"{file}: status must be PASS or FAIL.")
        if data.get("workspace") not in (None, ws):
            raise GateError(f"{file}: it ran for workspace '{data.get('workspace')}', not '{ws}'.")
        env = envs.get(str(data.get("env", "main")).lower())
        if not env:
            raise GateError(f"{file}: environment '{data.get('env')}' is not one of {', '.join(envs.values())}.")
        key = data.get("script")
        if not isinstance(key, str) or key not in known:
            matches = legacy.get(key, set()) if isinstance(key, str) else set()
            if key is None and isinstance(data.get("case"), str):
                matches = by_case.get(data["case"], set())
            if len(matches) != 1:
                raise GateError(f"{file}: script '{key}' is not a saved script uniquely identified in workspace '{ws}'.")
            key = next(iter(matches))
        keep = ("status", "at", "run_id", "failed_step", "reason", "total_s", "base_url", "source", "video")
        entries.append((key, env, {k: data[k] for k in keep if k in data}))
    done = load(ws)
    for key, env, entry in entries:
        if entry.get("run_id") and any(run.get("run_id") == entry["run_id"] for run in done.get(key, {}).get(env, {}).get("runs", [])):
            continue                                                        # the same run imported twice counts once
        record(ws, key, env, entry)
    return [{"script": key, "env": env, "status": entry["status"]} for key, env, entry in entries]
