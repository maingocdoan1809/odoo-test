"""Acceptance-suite stand-in for the host: it plays the coordinator that Claude Code or Copilot CLI is in real use. It asks oqa for the briefs (`dispatch.plan`), gives each to one
`claude --print` process, then settles the case. This belongs to the test harness only: oqa itself starts no model."""
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oqa import claims, dispatch  # noqa: E402
from oqa.state import GateError  # noqa: E402
from oqa.store import read_json  # noqa: E402

HIDDEN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _agent(session, worker, model, timeout):
    folder = session.dir / "logs" / worker["agent"]
    folder.mkdir(parents=True, exist_ok=True)
    args = [shutil.which("claude"), "--print", "--model", model, "--disable-slash-commands", "--permission-mode", "dontAsk", "--allowedTools", "Bash"]
    with (folder / "worker.log").open("w", encoding="utf-8") as output:
        try:
            subprocess.run(args, input=worker["brief"], stdout=output, stderr=subprocess.STDOUT, text=True, encoding="utf-8", timeout=timeout, cwd=folder, creationflags=HIDDEN)
        except subprocess.TimeoutExpired:
            return f"{worker['agent']} timed out"
    return ""


def run(session, worker_model="haiku", coordinator_model="sonnet", timeout=480, rounds=6):
    """Hand every free case to its own agent, settle each, and go round again until every case has a result or `rounds` is used. Returns {phase, results, pending}."""
    if not shutil.which("claude"):
        raise GateError("The harness needs the Claude CLI to stand in for the host's agents.")
    order = (read_json(session.dir / "strategy.json", {}) or {})["order"]
    for _ in range(rounds):
        state = session.state()
        if state["phase"] != "EXECUTE" or state.get("stopped") or state.get("waiting"):
            break
        batch = dispatch.plan(session)["workers"]
        if not batch:
            break
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            ended = list(pool.map(lambda worker: _agent(session, worker, worker_model, timeout), batch))
        for worker, problem in zip(batch, ended):
            case = worker["case"]
            if case in session.state()["results"]:
                dispatch.settle(session, case)
            else:
                claims.add_note(session, "coordinator", problem or "The agent ended without closing the case.", case)
                claims.release(session, case)
    state = session.state()
    return {"phase": state["phase"], "results": state["results"], "pending": [case for case in order if case not in state["results"]]}
