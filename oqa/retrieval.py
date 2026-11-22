"""Small, workspace-scoped context for testcase planning and focused lookups."""

import json

from . import knowledge, sitemap

CASE_LIMIT = 1100
WORKBOOK_LIMIT = 16000
DETAIL_LIMIT = 4800


def _short(text, size):
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def fit(context, budget):
    """Keep entire snippets, giving both sources a chance before adding more hits."""
    result = {key: [] for key in context}
    for index in range(max((len(items) for items in context.values()), default=0)):
        for key, items in context.items():
            if index >= len(items):
                continue
            size = len(json.dumps(items[index], ensure_ascii=False)) + len(key) + 8
            if size <= budget:
                result[key].append(items[index])
                budget -= size
    return {key: items for key, items in result.items() if items}


class WorkspaceContext:
    def __init__(self, ws, lang="en", only=""):
        self.unavailable = {}
        self.notes = []
        if only != "sitemap":
            try:
                self.notes = knowledge.search(ws)
            except (OSError, UnicodeError):
                self.unavailable["knowledge"] = "Cannot read knowledge/index.md."
        language = sitemap.output_language(lang)
        self.nodes, edges = {}, []
        if sitemap.file_of(ws).is_file():
            try:
                graph = sitemap.localized(sitemap.load(ws, language), language)
                self.nodes, edges = graph["nodes"], graph["edges"]
            except sitemap.SitemapError as error:
                self.unavailable["sitemap"] = str(error)
            except (OSError, ValueError, KeyError, AttributeError):
                self.unavailable["sitemap"] = "Cannot read knowledge/sitemap.json. Run `oqa sitemap build` to refresh it."
        self.menus = {}
        self.controls = {}
        for edge in edges:
            target = self.nodes.get(edge["to"], {})
            if edge["kind"] == "opens" and edge["from"] in self.nodes and target:
                self.menus.setdefault(edge["to"], []).append(edge["from"])
            if edge["kind"] == "shows":
                controls = self.controls.setdefault(edge["from"], set())
                for kind in ("fields", "buttons"):
                    for control in target.get(kind) or []:
                        for label in (control.get("label"), *(control.get("labels") or {}).values()):
                            words = frozenset(knowledge.terms(label))
                            if len(words) >= 2:
                                controls.add((kind[:-1], label, words))
        self.action_menus = {menu for menus in self.menus.values() for menu in menus}

    def _screens(self, text, screen, limit):
        query = screen or text
        words = knowledge.terms(query)
        hints = knowledge.terms(text)
        if not words and screen not in self.nodes:
            return []
        ranked = []
        for key, node in self.nodes.items():
            if key in self.action_menus:
                continue
            if node["kind"] != "action" and key != screen and (not screen or node["kind"] != "view"):
                continue
            candidates = self.menus.get(key, [None])
            for menu in candidates:
                route_node = self.nodes[menu] if menu else {}
                route = route_node.get("label", "")
                labels = {node.get("label", ""), route, route.rsplit(" / ", 1)[-1],
                          *(node.get("labels") or {}).values(), *(route_node.get("labels") or {}).values()}
                terms = [knowledge.terms(name) for name in labels if name]
                model, external = node.get("model", ""), node.get("xmlid", "")
                exact = screen in (key, menu) if screen else False
                identifier = any(knowledge.mentions(query, name) for name in (model, external, route_node.get("xmlid")))
                coverage = max((len(names & words) for names in terms if names <= words), default=0)
                phrase = max((len(knowledge.terms(name)) for name in labels
                              if knowledge.mentions(query, name)), default=0)
                hits = [(len(words), kind, label) for kind, label, words in self.controls.get(key, ())
                        if words <= hints and knowledge.mentions(text, label)]
                support, kind, label = max(hits, default=(0, "", ""))
                if not (exact or identifier or coverage or phrase or (support and not screen)):
                    continue
                score = 40 * exact + 10 * identifier + 3 * coverage + 4 * phrase + 5 * support
                ranked.append((score, menu or key, key, node, route, f"{kind} '{label}'" if label else ""))
        results = []
        seen = set()
        for score, target, key, node, route, match in sorted(ranked, key=lambda item: (-item[0], item[1])):
            if target in seen:
                continue
            seen.add(target)
            results.append((score, {
                "key": target, "label": _short(route or node.get("label"), 135),
                "model": _short(node.get("model"), 65), "match": _short(match, 80),
                **({"action": key} if key != target else {}),
            }))
            if len(results) == limit:
                break
        return results

    def lookup(self, case, *, assessed=None, screen="", issue="", profile="", state="", only="", detail=False):
        assessed = assessed or {}
        steps = [step.get("action", "") for step in assessed.get("steps", []) if isinstance(step, dict)]
        intent = " ".join(str(line or "") for line in (
            case.get("title"), case.get("category"), case.get("pre"), *case.get("steps", []), *steps))
        text = " ".join(str(line or "") for line in (screen, issue, intent, case.get("expected"), case.get("note")))
        found = self._screens(f"{issue} {intent}", screen, 5 if detail else 2)
        best = found[0][0] if found else 0
        anchors = [screen, *(hit["model"] for score, hit in found if score == best),
                   *(hit["label"].rsplit(" / ", 1)[-1] for score, hit in found if score == best)]
        context = {}
        if only != "knowledge":
            context["sitemap"] = [hit for _, hit in found]
        if only != "sitemap":
            notes = knowledge.related(self.notes, text, screens=anchors, exact_screen=bool(screen),
                                      profile=profile or assessed.get("profile") or case.get("role", ""),
                                      state=state or case.get("pre", ""), limit=5 if detail else 2)
            context["knowledge"] = [knowledge.describe(note, 550 if detail else 210) for note in notes]
        return fit(context, DETAIL_LIMIT if detail else CASE_LIMIT)
