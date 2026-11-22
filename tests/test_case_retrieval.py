import json

from oqa import analysis, cli, intake, knowledge, retrieval, sitemap, worker_mcp
from oqa.state import Session
from oqa.store import read_json

from conftest import make_workbook


def make_case(ws, tmp_path):
    workbook = make_workbook(tmp_path / "renamed-workbook.xlsx", [
        ("NEW-42", "Requests: từ chối item thuộc site khác", "", "Lưu request có item thuộc site khác", "Dòng sai không được lưu"),
    ])
    doc = intake.build(workbook)
    doc["cases"][0].update(role="Manager", pre="Request ở trạng thái Draft")
    session = Session.create(ws["name"], workbook, doc)
    sitemap.save(ws["name"], {
        "nodes": {
            "menu:1": {"kind": "menu", "label": "OQA / Requests"},
            "window:2": {"kind": "action", "label": "Requests", "model": "oqa.harness.request"},
            "window:3": {"kind": "action", "label": "Taxes", "model": "account.tax"},
        },
        "edges": [{"from": "menu:1", "to": "window:2", "kind": "opens"}],
    })
    return session, doc


def test_analysis_and_on_demand_retrieve_scoped_rules_and_routes(ws, tmp_path):
    session, doc = make_case(ws, tmp_path)
    name = ws["name"]
    parser = cli.build_parser()
    common = ["--ws", name, "--session", session.id]
    learned, _ = cli.cmd_knowledge(parser.parse_args([
        "knowledge", "learn", "--screen", "oqa.harness.request", "--kind", "rule",
        "--action", "luu", "--condition", "item khac site", "--profile", "Manager", "--state", "Draft",
        "--evidence", "Kiểm tra dòng sai chưa được lưu", "--fact", "Hiện lỗi item khác site", "--ref", "Design §4.2", *common,
    ]))
    assert learned["result"] == "added"
    knowledge.learn(name, "oqa.harness.request", "rule", "Sai quyền", action="lưu", condition="item khác site", profile="Operator")
    knowledge.learn(name, "oqa.harness.request", "rule", "Sai trạng thái", action="lưu", condition="item khác site", state="Approved")
    knowledge.learn(name, "oqa.harness.request", "rule", "Sai điều kiện", action="lưu", condition="số lượng bằng 0")
    knowledge.learn(name, "oqa.harness.request", "navigation", "Đường dẫn cũ cho item khác site")
    knowledge.learn(name, "account.tax", "rule", "Khai báo item khác site", condition="item khác site")
    knowledge.learn("another-workspace", "oqa.harness.request", "rule", "Quy tắc workspace khác", condition="item khác site")

    template, _ = cli.cmd_analyze(parser.parse_args(["analyze", "--template", *common]))
    case = template["cases"][0]
    context = case["_context"]
    assert case["previous"] is None
    assert context["sitemap"][0]["key"] == "menu:1"
    assert context["sitemap"][0]["action"] == "window:2"
    assert context["sitemap"][0]["label"] == "OQA / Requests"
    assert len(context["knowledge"]) == 1
    assert "Hiện lỗi item khác site" in context["knowledge"][0]
    assert "dòng sai chưa được lưu" in context["knowledge"][0]
    assert context["knowledge"][0].endswith("Design §4.2")

    lean, _ = cli.cmd_analyze(parser.parse_args(["analyze", "--template", "--no-context", *common]))
    assert "_context" not in lean["cases"][0]
    only, _ = cli.cmd_case(parser.parse_args([
        "case", "context", "NEW-42", "--only", "knowledge", "--screen", "window:2",
        "--profile", "Operator", "--state", "Draft", *common,
    ]))
    assert set(only) == {"case", "knowledge"}
    assert len(only["knowledge"]) == 1 and "Sai quyền" in only["knowledge"][0]

    packet = json.loads(worker_mcp.call(session, "NEW-42", "worker", "context", {"only": "sitemap", "screen": "window:2"}))
    assert packet == {"case": "NEW-42", "sitemap": context["sitemap"]}
    session.analysis_submitted(document=template)
    assert "_context" not in read_json(session.dir / "analysis.json")["cases"][0]


def test_template_budget_and_legacy_note(ws, tmp_path, monkeypatch):
    _, doc = make_case(ws, tmp_path)
    name = ws["name"]
    legacy = knowledge.file_of(name)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("- [screen:oqa.harness.request] [kind:rule] [tag:request] Hiện lỗi item khác site -> design.md\n",
                      encoding="utf-8")
    doc["cases"] *= 4
    monkeypatch.setattr(retrieval, "WORKBOOK_LIMIT", 450)
    template = analysis.template(doc, ws)
    assert template["_context_more"]
    assert any("design.md" in snippet for case in template["cases"]
               for snippet in case.get("_context", {}).get("knowledge", []))
    assert sum(len(json.dumps(case["_context"], ensure_ascii=False)) for case in template["cases"] if "_context" in case) <= 450
    assert not template["cases"][-1].get("_context")


def test_navigation_ranks_the_menu_whose_view_has_the_requested_column(ws):
    sitemap.save(ws["name"], {
        "nodes": {
            "menu:1": {"kind": "menu", "label": "Operations / Dispatch / Queue"},
            "menu:2": {"kind": "menu", "label": "Operations / Warehouse / Queue"},
            "window:1": {"kind": "action", "label": "Queue", "model": "request"},
            "window:2": {"kind": "action", "label": "Queue", "model": "request"},
            "view:1": {"kind": "view", "label": "Request list", "fields": [{"label": "Loading Site"}]},
            "view:2": {"kind": "view", "label": "Request list", "fields": [{"label": "Storage Site"}]},
        },
        "edges": [
            {"from": f"{source}:{number}", "to": f"{target}:{number}", "kind": kind}
            for number in (1, 2) for source, target, kind in (("menu", "window", "opens"), ("window", "view", "shows"))
        ],
    })
    context = retrieval.WorkspaceContext(ws["name"])
    case = {"title": "Mở Queue có cột Storage Site"}
    for options in ({}, {"screen": "Queue", "issue": "Storage Site", "only": "sitemap"}):
        hit = context.lookup(case, **options)["sitemap"][0]
        assert hit["key"] == "menu:2"
        assert hit["match"] == "field 'Storage Site'"


def test_unreadable_map_still_allows_analysis_and_knowledge(ws, tmp_path):
    _, doc = make_case(ws, tmp_path)
    sitemap.file_of(ws["name"]).write_text("invalid JSON", encoding="utf-8")
    knowledge.learn(ws["name"], "Requests", "rule", "Hiện lỗi item khác site", condition="item khác site")
    template = analysis.template(doc, ws)
    assert "sitemap" in template["_context_unavailable"]
    assert "Hiện lỗi item khác site" in template["cases"][0]["_context"]["knowledge"][0]


def test_learn_updates_the_same_scope_and_keeps_each_facts_evidence(ws):
    name = ws["name"]
    file = knowledge.file_of(name)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "# Notes\n"
        "- [screen:request] [kind:rule] [profile:Manager] Reject a request with a foreign site item -> design.md\n"
        "- [screen:request] [kind:rule] [profile:Operator] Keep the Approve button hidden -> access.md\n"
        "- [screen:Request] [kind:rule] [profile:Manager] Use a same-site control row -> qa.md\n",
        encoding="utf-8",
    )
    scope = {"profile": "Manager"}
    assert knowledge.learn(name, "request", "rule", "Check that no invalid row persisted",
                           evidence="Inspect list after reload", ref="run A", **scope) == "updated"
    assert knowledge.learn(name, "Request", "rule", "REJECT A REQUEST with a foreign site item",
                           evidence="Read the error dialog", ref="run B", **scope) == "updated"
    assert knowledge.learn(name, "request", "rule", "Reject a request with a foreign site item",
                           evidence="Read the error dialog", ref="run B", **scope) == "duplicate"
    assert knowledge.learn(name, "request", "rule", "Approve a submitted request", profile="Manager", state="Submitted") == "added"
    lines = file.read_text(encoding="utf-8").splitlines()
    assert sum(line.casefold().startswith("- [screen:request] [kind:rule] [profile:manager]") for line in lines) == 2
    assert sum(line.startswith("  - ") for line in lines) == 3
    assert "Keep the Approve button hidden -> access.md" in "\n".join(lines)
    facts = {item["fact"]: item for item in knowledge.search(name, screen="request", kind="rule")}
    assert facts["Reject a request with a foreign site item"]["ref"] == "design.md | run B"
    assert facts["Check that no invalid row persisted"]["evidence"] == "Inspect list after reload"
    assert facts["Use a same-site control row"]["ref"] == "qa.md"
    assert facts["Approve a submitted request"]["state"] == "Submitted"
