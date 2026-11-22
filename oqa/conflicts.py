"""The user's decision where a testcase differs from its design (BD).

The design is the default, but the design can be the one that is wrong or out of date, so the user is made to choose for each such place: follow the BD or follow the
testcase. Nothing runs until every conflict has a choice. The choice lives in the session (`conflict_choices`), which the assistant cannot write; it is only
made through the dashboard."""
import time

from . import analysis, events, workspace
from .state import GateError
from .store import read_json, write_json

CHOICES = ("bd", "testcase")
OPEN_PHASES = ("ANALYZE", "CONNECT", "CONFIRM")


def decide(session, case, step, choice, by):
    """Record the user's choice, make the analysis follow it, and rebuild the strategy the user is looking at so that what they confirm is what will run."""
    if choice not in CHOICES:
        raise GateError("Chọn theo BD hay theo testcase.")
    with session.update() as state:
        if state["phase"] not in OPEN_PHASES:
            raise GateError(f"Không đổi được lựa chọn ở giai đoạn {state['phase']}: các case đã được duyệt.")
        document = read_json(session.dir / "analysis.json")
        item = next((i for c in (document or {}).get("cases", []) if c["id"] == case and c.get("verdict") == "ready"
                     for i in c.get("testcase_conflicts") or [] if isinstance(i, dict) and i.get("step") == step), None)
        if item is None:
            raise GateError(f"Case {case} bước {step} không có chỗ nào testcase lệch BD.")
        choices = state.setdefault("conflict_choices", {})
        choices[analysis.conflict_key(case, step)] = {"choice": choice, "digest": analysis.conflict_digest(item), "by": by, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        analysis.apply_choices(document, choices)
        write_json(session.dir / "analysis.json", document)
        events.append(session.dir, "conflict_decided", case=case, step=step, choice=choice, by=by, ref=item["ref"])
        if state["phase"] == "CONFIRM" and state.get("strategy_sha") and (session.dir / "strategy.json").is_file():
            data, text = analysis.strategy(document, session.cases(), workspace.load(session.ws))
            write_json(session.dir / "strategy.json", data)
            (session.dir / "strategy.md").write_text(text + "\n", encoding="utf-8")
            state["strategy_sha"] = data["sha"]
