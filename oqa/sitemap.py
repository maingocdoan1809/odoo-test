"""The sitemap of the Odoo under test: which menu opens which action, which views the action shows, which button leads where.

It is read from Odoo's own definitions (menus, actions, view definitions) through read-only calls: nothing is clicked, so nothing is changed and a crawl
takes minutes, not hours. A button of type `object` calls a Python method: where it leads is only known by running it, so it is listed as such.

    knowledge/sitemap.json = {schema_version, meta, nodes: {key: node}, edges: [{from, to, kind, label?, name?}], unresolved: [{where, why}]}

node keys:  menu:<id>  window:<id> | server:<id> | client:<id> | url:<id> | report:<id>  (actions)  view:<model>:<type>:<id>
edge kinds: menu (parent -> child) · opens (menu -> action) · shows (action -> view) · row (list or kanban -> form of the same model) · button (view -> action)

`build` takes any `rpc(model, method, args, kwargs)` (the signed-in `Odoo.rpc`); everything else reads the saved file, so the CLI and the MCP server share it."""
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone

from .paths import workspace_dir
from .sitemap_i18n import text as _t

SCHEMA = 1
MAX_NODES = 8000
STALE_DAYS = 30
KINDS = {"ir.actions.act_window": "window", "ir.actions.server": "server", "ir.actions.client": "client", "ir.actions.act_url": "url", "ir.actions.report": "report"}
ACTION_FIELDS = {"window": ["name", "res_model", "views", "view_mode", "target"], "server": ["name", "model_name"], "client": ["name", "tag"], "url": ["name", "url"], "report": ["name", "model"]}
OPEN_ROW = ("open", "edit")                          # kanban cards: `<a type="open">` opens the record
LANGUAGES = {"en": "en_US", "ja": "ja_JP"}


class SitemapError(Exception):
    pass


def file_of(ws):
    return workspace_dir(ws) / "knowledge" / "sitemap.json"


def save(ws, data):
    path = file_of(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)
    return path


def load(ws, lang="en"):
    path = file_of(ws)
    if not path.exists():
        raise SitemapError(_t(output_language(lang), "No sitemap for this workspace yet: run `oqa sitemap build` (it reads the menus and views, it clicks nothing)."))
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version", 0) > SCHEMA:
        raise SitemapError(_t(output_language(lang), "This sitemap was written by a newer odoo-test: update the skill."))
    return data


# --- building -----------------------------------------------------------------------
def _short(text, size=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[:size - 1] + "…"


def buttons_of_arch(arch, info=None):
    """The clickable controls of a view definition that act: [{label, name, type, [invisible], [stat]}]; None when the definition is not readable XML."""
    try:
        root = ET.fromstring(arch.strip())
    except (ET.ParseError, AttributeError):
        return None
    out = []
    for element in root.iter():
        kind = element.get("type")
        if element.tag not in ("button", "a") or kind not in ("action", "object") + OPEN_ROW:
            continue
        label = element.get("string") or " ".join(" ".join(element.itertext()).split())
        if not label:
            # Stat buttons often put their label on a child statinfo field, not the button.
            label = " / ".join(field.get("string") or ((info or {}).get(field.get("name")) or {}).get("string") or ""
                               for field in element.iter("field")).strip(" / ")
        item = {"name": element.get("name") or "", "type": kind, "label": _short(label or element.get("title") or element.get("name"), 60)}
        if element.get("invisible"):
            item["invisible"] = _short(element.get("invisible"))
        if "oe_stat_button" in (element.get("class") or ""):
            item["stat"] = True
        out.append(item)
    return out


def disabled_of_arch(arch):
    """What the view switches off in its root tag (`create="0"` hides New, `edit="0"` makes it read only): ['create', 'edit', ...]."""
    try:
        root = ET.fromstring(arch.strip())
    except (ET.ParseError, AttributeError):
        return []
    return [name for name in ("create", "edit", "delete", "duplicate") if (root.get(name) or "").lower() in ("0", "false")]


def fields_of_arch(arch, info):
    """The fields a view shows (not the columns inside a one2many, those belong to that field): [{name, label, type, required, readonly, [relation, options, invisible, widget]}].
    `info` is what Odoo says about the model's fields (label, type, required, selection values); the view adds its own `string`, `required`, `readonly`, `widget`."""
    try:
        root = ET.fromstring(arch.strip())
    except (ET.ParseError, AttributeError):
        return []
    out, stack = [], [root]
    while stack:
        element = stack.pop()
        if element.tag == "field" and element.get("name"):
            name, meta = element.get("name"), info.get(element.get("name")) or {}
            hidden = element.get("invisible")
            if hidden not in ("1", "True", "true"):
                item = {"name": name, "label": _short(element.get("string") or meta.get("string") or name, 60), "type": meta.get("type") or "",
                        "required": _flag(element.get("required"), meta.get("required")), "readonly": _flag(element.get("readonly"), meta.get("readonly"))}
                if meta.get("relation"):
                    item["relation"] = meta["relation"]
                if meta.get("selection"):
                    item["options"] = [f"{v}={l}" for v, l in meta["selection"][:12]]
                if hidden:
                    item["invisible"] = _short(hidden)
                if element.get("widget"):
                    item["widget"] = element.get("widget")
                out.append(item)
            continue                                             # what is inside a field (a sub-list, a sub-form) is not a field of this view
        stack.extend(reversed(list(element)))
    return out


def _flag(from_view, from_model):
    """True / False, or the expression the view uses (the field is only sometimes required or read only)."""
    if from_view in (None, ""):
        return bool(from_model)
    return True if from_view in ("1", "True", "true") else False if from_view in ("0", "False", "false") else _short(from_view)


def _action_ref(name, by_name):
    """(action model or None, id) a button's `name` points at: `%(module.xmlid)d`, `module.xmlid` or a number; None when it is a method name."""
    name = (name or "").strip()
    wrapped = re.fullmatch(r"%\((.+)\)d", name)
    name = wrapped.group(1) if wrapped else name
    if name.isdigit():
        return (None, int(name))
    return by_name.get(name)


def build(rpc, profile="", version="", languages=None, context=None, lang="en"):
    """Read active English/Japanese translations without changing the user's language.

    Explicit languages must be active in Odoo; by default use whichever of English/Japanese
    are active. The graph stays keyed by technical IDs, with translations attached to labels.
    """
    lang = output_language(lang)
    requested = list(LANGUAGES.values()) if languages is None else (languages.split(",") if isinstance(languages, str) else list(languages))
    requested = list(dict.fromkeys(LANGUAGES.get(code.strip(), code.strip()) for code in requested))
    if not requested or any(code not in LANGUAGES.values() for code in requested):
        raise SitemapError(_t(lang, "Sitemap languages must be en_US / ja_JP (or en / ja)."))
    active = {row["code"] for row in rpc("res.lang", "search_read", [[["active", "=", True]]], {"fields": ["code"], "context": dict(context or {})})}
    missing = [code for code in requested if code not in active]
    if languages is not None and missing:
        raise SitemapError(_t(lang, "Activate these languages in Odoo before building the sitemap: {languages}", languages=", ".join(missing)))
    available = [code for code in requested if code in active]
    if not available:
        raise SitemapError(_t(lang, "Activate English (en_US) or Japanese (ja_JP) in Odoo before building the sitemap."))
    data = None
    for code in available:
        def localized_rpc(model, method, args=None, kwargs=None):
            kw = dict(kwargs or {})
            kw["context"] = {**(context or {}), **kw.get("context", {}), "lang": code}
            return rpc(model, method, args, kw)
        translated = _build(localized_rpc, profile, version)
        if data is None:
            data = translated
        _merge_language(data, translated, code)
        if translated is not data:
            data["unresolved"].extend({**item, "language": code} for item in translated["unresolved"])
    data["meta"].update(languages=available, default_language=available[0], missing_languages=missing)
    return data


def _merge_language(data, translated, code):
    def labels(target, source):
        target.setdefault("labels", {})[code] = source["label"]
        if "options" in source:
            target.setdefault("options_by_language", {})[code] = source["options"]

    for key, node in data["nodes"].items():
        other = translated["nodes"].get(key)
        if other is None:
            continue
        labels(node, other)
        for collection in ("buttons", "fields"):
            # Repeated fields/buttons may have different labels: align their occurrences.
            by_identity = {}
            for item in other.get(collection, []):
                by_identity.setdefault((item["name"], item.get("type")), deque()).append(item)
            for item in node.get(collection, []):
                matches = by_identity.get((item["name"], item.get("type")))
                if matches:
                    labels(item, matches.popleft())
    by_edge = {(e["from"], e["to"], e["kind"], e.get("name")): e for e in translated["edges"]}
    for edge in data["edges"]:
        other = by_edge.get((edge["from"], edge["to"], edge["kind"], edge.get("name")))
        if other and "label" in other:
            labels(edge, other)


def _build(rpc, profile="", version=""):
    nodes, edges, unresolved = {}, [], []

    def issue(where, template, **values):
        unresolved.append({"where": where, "why": template.format(**values), "message": template, "values": values})

    def edge(source, target, kind, **extra):
        edges.append({"from": source, "to": target, "kind": kind, **{k: v for k, v in extra.items() if v}})

    by_id, by_name = {}, {}
    try:
        for row in rpc("ir.model.data", "search_read", [["|", ["model", "=", "ir.ui.menu"], ["model", "=like", "ir.actions.%"]]], {"fields": ["module", "name", "model", "res_id"]}):
            by_id[(row["model"], row["res_id"])] = f"{row['module']}.{row['name']}"
            by_name[f"{row['module']}.{row['name']}"] = (row["model"], row["res_id"])
    except AssertionError as error:
        issue("external ids", "not readable with this profile, so menus and actions have no external id and `%(module.name)d` buttons cannot be followed: {error}", error=str(error))

    menus = rpc("ir.ui.menu", "search_read", [[]], {"fields": ["name", "parent_id", "action", "sequence"], "order": "sequence,id"})
    menu_by_id = {m["id"]: m for m in menus}

    def trail(menu):
        names, seen = [], set()
        while menu and menu["id"] not in seen:
            seen.add(menu["id"])
            names.append(menu["name"])
            menu = menu_by_id.get(menu["parent_id"][0]) if menu["parent_id"] else None
        return " / ".join(reversed(names))

    pending = {}                                         # action model (None: only the number is known) -> ids still to read
    number_key = {}                                      # an action known by number -> its node key, once its type is read
    waiting = []                                         # (view key, button) whose action is known by number only

    def want(model, res_id):
        if f"{KINDS[model]}:{res_id}" not in nodes:
            pending.setdefault(model, set()).add(res_id)

    for menu in menus:
        key = f"menu:{menu['id']}"
        nodes[key] = {"kind": "menu", "label": trail(menu), "name": menu["name"], **({"xmlid": by_id[("ir.ui.menu", menu["id"])]} if ("ir.ui.menu", menu["id"]) in by_id else {})}
        if menu["parent_id"]:
            edge(f"menu:{menu['parent_id'][0]}", key, "menu")
    for menu in menus:
        model, _, res_id = str(menu["action"] or "").partition(",")
        if res_id.isdigit() and model in KINDS:
            want(model, int(res_id))
            edge(f"menu:{menu['id']}", f"{KINDS[model]}:{res_id}", "opens")

    def settle(ids_by_model):
        """Read the actions asked for and make their nodes (an action known by number only has its type read first). Returns [(key, row)]."""
        unknown = ids_by_model.pop(None, set())
        if unknown:
            for row in rpc("ir.actions.actions", "read", [sorted(unknown)], {"fields": ["type"]}):
                if row["type"] in KINDS:
                    ids_by_model.setdefault(row["type"], set()).add(row["id"])
                    number_key[row["id"]] = f"{KINDS[row['type']]}:{row['id']}"
        made = []
        for model, ids in ids_by_model.items():
            kind = KINDS[model]
            ids = sorted(i for i in ids if f"{kind}:{i}" not in nodes)
            for row in (rpc(model, "read", [ids], {"fields": ACTION_FIELDS[kind]}) if ids else []):
                key = f"{kind}:{row['id']}"
                node = {"kind": "action", "type": kind, "label": row.get("name") or key}
                if (model, row["id"]) in by_id:
                    node["xmlid"] = by_id[(model, row["id"])]
                if kind == "window":
                    node.update(model=row["res_model"], target=row.get("target") or "current")
                elif kind in ("server", "report"):
                    node["model"] = row.get("model_name") or row.get("model") or ""
                nodes[key] = node
                made.append((key, row))
        return made

    def read_views(key, row):
        model = row["res_model"]
        wanted = [[v[0], v[1]] for v in (row.get("views") or []) if v[1] != "search"] or [[False, t] for t in (row.get("view_mode") or "list,form").split(",") if t != "search"]
        try:
            answer = rpc(model, "get_views", [], {"views": wanted, "options": {}})
            got = answer["views"]
        except AssertionError as error:
            issue(key, "the views of {model} could not be read: {error}", model=model, error=_short(error, 160))
            return
        nodes[key]["views"] = sorted(got)
        known = (answer.get("models") or {}).get(model) or {}
        known = known["fields"] if isinstance(known.get("fields"), dict) else known       # Odoo 17 answers {field: info}, later versions {fields: {...}}
        made = {}
        for kind, info in got.items():
            view_key = f"view:{model}:{kind}:{info.get('id') or 0}"
            made[kind] = view_key
            edge(key, view_key, "shows")
            if view_key in nodes:
                continue
            found = buttons_of_arch(info.get("arch") or "", known)
            if found is None:
                issue(view_key, "the view definition is not readable XML")
                found = []
            nodes[view_key] = {"kind": "view", "type": kind, "model": model, "label": f"{model} {kind}", "buttons": found, "fields": fields_of_arch(info.get("arch") or "", known), "disabled": disabled_of_arch(info.get("arch") or "")}
            for button in found:
                if button["type"] == "object":
                    counts["object"] += 1
                elif button["type"] == "action":
                    ref = _action_ref(button["name"], by_name)
                    if ref is None or (ref[0] is not None and ref[0] not in KINDS):
                        issue(view_key, "button '{label}' points at action '{name}', which is not a known action", label=button['label'], name=button['name'])
                    elif ref[0] is None:
                        waiting.append((view_key, button, ref[1]))
                        pending.setdefault(None, set()).add(ref[1])
                    else:
                        want(*ref)
                        edge(view_key, f"{KINDS[ref[0]]}:{ref[1]}", "button", label=button["label"], name=button["name"])
        for kind, view_key in made.items():
            if kind != "form" and "form" in made:
                edge(view_key, made["form"], "row", label="open a row")

    counts = {"object": 0}
    while pending and len(nodes) < MAX_NODES:
        batch, pending = pending, {}
        for key, row in settle(batch):
            if key.startswith("window:"):
                read_views(key, row)
        for view_key, button, number in list(waiting):
            if number in number_key:
                edge(view_key, number_key[number], "button", label=button["label"], name=button["name"])
                waiting.remove((view_key, button, number))
    truncated = bool(pending)
    for view_key, button, number in waiting:
        if number not in number_key:
            issue(view_key, "button '{label}' points at action number {number}, which could not be read", label=button['label'], number=number)
    for e in [e for e in edges if e["to"] not in nodes]:
        edges.remove(e)
        issue(e["from"], "leads to {target}, which could not be read", target=e['to'])
    kinds = [n["kind"] for n in nodes.values()]
    return {"schema_version": SCHEMA,
            "meta": {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "profile": profile, "odoo_version": version,
                     "menus": kinds.count("menu"), "actions": kinds.count("action"), "views": kinds.count("view"), "object_buttons": counts["object"], "truncated": truncated},
            "nodes": nodes, "edges": edges, "unresolved": unresolved}


# --- reading ------------------------------------------------------------------------
def unresolved_text(item, lang="en"):
    if "message" not in item:
        return item["why"]
    return _t(output_language(lang), item["message"], **item.get("values", {}))


def age_days(data):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(data["meta"]["generated"])).days
    except (KeyError, ValueError):
        return 0


def output_language(lang):
    """The workspace's Vietnamese default keeps the existing English sitemap output."""
    if not lang or lang == "vi":
        return "en"
    code = str(lang).replace("-", "_").split("_")[0]
    if code not in LANGUAGES:
        raise SitemapError("Sitemap output language must be en or ja.")
    return code


def localized(data, lang):
    """A display copy: keep all aliases searchable and leave the saved graph untouched."""
    code = LANGUAGES[output_language(lang)]

    def item(value):
        return {**value, **({"label": value["labels"].get(code, value["label"])} if value.get("labels") else {}),
                **({"options": value["options_by_language"].get(code, value.get("options", []))} if value.get("options_by_language") else {})}

    return {**data, "nodes": {key: {**item(node), **{kind: [item(v) for v in node[kind]] for kind in ("buttons", "fields") if kind in node}}
                              for key, node in data["nodes"].items()}, "edges": [item(edge) for edge in data["edges"]]}


def header(data, lang="en"):
    lang = output_language(lang)
    m = data["meta"]
    old = age_days(data)
    return (_t(lang, "sitemap of {profile} on Odoo {version}, built {date}: {menus} menus, {actions} actions, {views} views",
               profile=m.get("profile") or "?", version=m.get("odoo_version") or "?", date=m["generated"][:10], menus=m["menus"], actions=m["actions"], views=m["views"])
            + (_t(lang, ". It is {days} days old: if a module was installed since, run `oqa sitemap build` again.", days=old) if old >= STALE_DAYS else "")
            + (_t(lang, ". Not complete: the crawl stopped at its size limit.") if m.get("truncated") else "")
            + (_t(lang, ". Label languages: {languages}", languages=", ".join(m["languages"])) if m.get("languages") else "")
            + (_t(lang, ". Languages not active in Odoo: {languages}", languages=", ".join(m["missing_languages"])) if m.get("missing_languages") else ""))


def _words(text):
    return re.findall(r"\w+", _normalized(text))


def _normalized(text):
    return unicodedata.normalize("NFKC", str(text)).casefold()


def _search_text(item):
    return _normalized(" ".join(str(item.get(k) or "") for k in ("label", "name", "model", "xmlid"))
                       + " " + " ".join((item.get("labels") or {}).values())
                       + " " + " ".join(item.get("options") or [])
                       + " " + " ".join(value for options in (item.get("options_by_language") or {}).values() for value in options))


def find(data, text, limit=12, kind="", lang="en"):
    """[(key, node, reason)] for the screens whose label, model, external id or button names contain the words of `text`, best first."""
    words = _words(text)
    if not words:
        return []
    order = {"menu": 0, "action": 1, "view": 2}
    scored = []
    for key, node in data["nodes"].items():
        if kind and node["kind"] != kind:
            continue
        hay = _search_text(node)
        reason, score = "", sum(1 for w in words if w in hay)
        for button in node.get("buttons") or []:
            if all(w in _search_text(button) for w in words):      # a button counts only when it holds every word asked
                reason, score = _t(lang, "button '{label}'", label=button["label"]) + (f" [name={button['name']}]" if button["name"] else ""), max(score, len(words))
                break
        if not reason:
            for field in node.get("fields") or []:
                if all(w in _search_text(field) for w in words):
                    reason, score = _t(lang, "field '{label}' [name={name}]", label=field["label"], name=field["name"]), max(score, len(words))
                    break
        if score:
            scored.append((-score, order[node["kind"]], key, node, reason))
    scored.sort(key=lambda s: s[:3])
    # Once every word matches a screen, omit partial matches that only share a module prefix.
    if scored and -scored[0][0] == len(words):
        scored = [item for item in scored if -item[0] == len(words)]
    return [(key, node, reason) for _, _, key, node, reason in scored[:limit]]


def resolve(data, text):
    """The node key `text` stands for: a key itself, or the best match of `find`."""
    if text in data["nodes"]:
        return text
    found = find(data, text, 1)
    return found[0][0] if found else None


def _out(data):
    table = {}
    for e in data["edges"]:
        table.setdefault(e["from"], []).append(e)
    return table


def views_of(data, key):
    """The view nodes reached from a menu, an action or a view itself."""
    out = _out(data)
    node = data["nodes"][key]
    if node["kind"] == "view":
        return [key]
    if node["kind"] == "menu":
        return [v for e in out.get(key, []) if e["kind"] == "opens" for v in views_of(data, e["to"])]
    return [e["to"] for e in out.get(key, []) if e["kind"] == "shows"]


def path(data, source, target, lang="en"):
    """The shortest way from the screen `source` (any menu root when empty) to `target`: [edge], or None."""
    goal = resolve(data, target)
    if goal is None:
        raise SitemapError(_t(lang, "nothing in the sitemap matches '{text}'", text=target))
    start = resolve(data, source) if source else None
    if source and start is None:
        raise SitemapError(_t(lang, "nothing in the sitemap matches '{text}'", text=source))
    out = _out(data)
    children = {e["to"] for e in data["edges"] if e["kind"] == "menu"}
    roots = [start] if start else [k for k, n in data["nodes"].items() if n["kind"] == "menu" and k not in children]
    seen, queue = set(roots), deque((r, []) for r in roots)
    while queue:
        key, way = queue.popleft()
        if key == goal:
            return way
        for e in out.get(key, []):
            if e["to"] not in seen:
                seen.add(e["to"])
                queue.append((e["to"], way + [e]))
    return None


# --- text for people and for the assistant (CLI and MCP share these) -----------------------------
def selector_of(kind, name, view_type=""):
    """A selector to start from (a hint: only `oqa try` proves it on the page)."""
    if kind == "button":
        return f"button[name='{name}']"
    if kind == "menu":
        return f"[data-menu-xmlid='{name}']"
    return f"th[data-name='{name}']" if view_type == "list" else f".o_field_widget[name='{name}']"


def describe(key, node, lang="en"):
    if node["kind"] == "menu":
        return _t(lang, "menu {label}", label=node["label"]) + (f"  [{node['xmlid']}]" if node.get("xmlid") else "")
    if node["kind"] == "action":
        return (_t(lang, '{type} action "{label}"', type=node["type"], label=node["label"])
                + (_t(lang, " on {model}", model=node["model"]) if node.get("model") else "")
                + (_t(lang, " (a dialog)") if node.get("target") == "new" else "") + (f"  [{node['xmlid']}]" if node.get("xmlid") else ""))
    return _t(lang, "{type} view of {model}", type=node["type"], model=node["model"])


def _cut(lines, limit, hint, lang="en"):
    """At most `limit` lines, and when some are left out, say how to narrow the question instead of printing them."""
    return lines if len(lines) <= limit else lines[:limit] + [_t(lang, "    ... {count} more not shown: narrow with {hint}", count=len(lines) - limit, hint=hint)]


def _views(data, key, view=""):
    """The views of a screen, only the ones of type `view` when it is given (`form`, `list`, ...)."""
    return [v for v in views_of(data, key) if not view or data["nodes"][v]["type"] == view]


def _head(data, key, node, view_key, extra="", lang="en"):
    view = data["nodes"][view_key]
    return f"  {describe(view_key, view, lang)}  ({view_key})" + (_t(lang, "  NOT possible here: {disabled}", disabled=", ".join(view["disabled"])) if view.get("disabled") else "") + extra


def text_find(data, text, limit=12, kind="", lang="en"):
    lang = output_language(lang)
    data = localized(data, lang)
    found = find(data, text, limit, kind, lang)
    lines = [f"{key}  {describe(key, node, lang)}" + (f"  <- {reason}" if reason else "") for key, node, reason in found]
    return "\n".join([header(data, lang), *(lines or [_t(lang, "nothing matches '{text}'{scope}. Try fewer or other words (a menu name, a model, a button label).",
                                                       text=text, scope=_t(lang, " among {kind}s", kind=kind) if kind else "")])])


def text_buttons(data, screen, view="", match="", limit=30, lang="en"):
    """The buttons of a screen's views, where each leads. `view`: only that view type; `match`: only buttons whose label or name contains it."""
    lang = output_language(lang)
    data = localized(data, lang)
    key = resolve(data, screen)
    if key is None:
        return _t(lang, "nothing in the sitemap matches '{text}'", text=screen)
    out, node = _out(data), data["nodes"][key]
    lines = [f"{key}  {describe(key, node, lang)}" + (f"  {selector_of('menu', node['xmlid'])}" if node.get("xmlid") and key.startswith("menu:") else "")]
    for view_key in _views(data, key, view):
        vnode = data["nodes"][view_key]
        lines.append(_head(data, key, node, view_key, lang=lang))
        goes = {e.get("name"): e["to"] for e in out.get(view_key, []) if e["kind"] == "button"}
        rows = []
        for b in vnode["buttons"]:
            if b["type"] in OPEN_ROW or (match and not all(w in _search_text(b) for w in _words(match))):
                continue
            where = (f"-> {describe(goes[b['name']], data['nodes'][goes[b['name']]], lang)}" if b["name"] in goes else
                     _t(lang, "-> unknown until it is run (it calls a method)") if b["type"] == "object" else _t(lang, "-> not resolved"))
            rows.append(_t(lang, '    {stat}button "{label}" [name={name}] {selector}', stat=_t(lang, "stat ") if b.get("stat") else "", label=b["label"], name=b["name"], selector=selector_of('button', b['name']))
                        + (_t(lang, " shown only if: not ({condition})", condition=b["invisible"]) if b.get("invisible") else "") + f" {where}")
        lines += _cut(rows, limit, "--match <word> or --view <type>", lang)
        if not match:
            lines += [_t(lang, "    clicking a row -> {target}", target=describe(e['to'], data['nodes'][e['to']], lang)) for e in out.get(view_key, []) if e["kind"] == "row"]
    return "\n".join(lines)


def text_fields(data, screen, view="", match="", required=False, limit=40, lang="en"):
    """The fields of a screen, with a selector to start from, required / read only and the values of a selection. By default only the form (the list repeats it);
    `view`: another view type; `match`: only fields whose name or label contains it; `required`: only fields that are (or can be) required. A field shown twice is listed once."""
    lang = output_language(lang)
    data = localized(data, lang)
    key = resolve(data, screen)
    if key is None:
        return _t(lang, "nothing in the sitemap matches '{text}'", text=screen)
    node = data["nodes"][key]
    views = _views(data, key, view)
    if not view and any(data["nodes"][v]["type"] == "form" for v in views):
        views = [v for v in views if data["nodes"][v]["type"] == "form"]
    lines = [f"{key}  {describe(key, node, lang)}"]
    for view_key in views:
        vnode = data["nodes"][view_key]
        lines.append(_head(data, key, node, view_key, lang=lang))
        rows, seen = [], set()
        for f in vnode.get("fields") or []:
            if f["name"] in seen or (match and not all(w in _search_text(f) for w in _words(match))) or (required and not f["required"]):
                continue
            seen.add(f["name"])
            flags = [_t(lang, w) for w, on in (("required", f["required"]), ("readonly", f["readonly"])) if on is True] + [_t(lang, "{flag} if: {condition}", flag=_t(lang, w), condition=on) for w, on in (("required", f["required"]), ("readonly", f["readonly"])) if isinstance(on, str)]
            rows.append(_t(lang, '    {name} "{label}" {type}', name=f["name"], label=f["label"], type=f["type"]) + (f" -> {f['relation']}" if f.get("relation") else "") + (f" [{', '.join(flags)}]" if flags else "")
                        + (_t(lang, " shown only if: not ({condition})", condition=f["invisible"]) if f.get("invisible") else "") + (f" widget={f['widget']}" if f.get("widget") else "") + f"  {selector_of('field', f['name'], vnode['type'])}"
                        + (_t(lang, "  options: {options}", options=", ".join(f["options"])) if f.get("options") else ""))
        lines += _cut(rows, limit, "--match <word>, --required or --view <type>", lang)
    return "\n".join(lines)


def _last(label):
    """A menu's label holds its whole path ("Sales / Orders"): o.menu wants one name at a time."""
    return str(label).split(" / ")[-1].strip()


def script_of(data, way):
    """The way as lines of a script, one piece per link: submenus are joined into one `o.menu(...)`, a row link opens a record, a button link clicks it. `opens` and
    `shows` link nothing to do (the page already shows it). Where a piece cannot be written from the map (a button that calls a method), it says so."""
    children = {e["to"] for e in data["edges"] if e["kind"] == "menu"}
    nodes, lines, menus = data["nodes"], [], []
    major = re.match(r"\d+", str((data.get("meta") or {}).get("odoo_version", "")))
    modern = major is not None and int(major.group()) >= 18             # Odoo 18+: /odoo/action-ID; before: /web#menu_id=..&action=..

    def flush():
        if menus:
            lines.append("# no action number on this way (a url or a report): open it by hand, a menu is never clicked by its name: " + " / ".join(menus))
            menus.clear()

    for e in way:
        if e["kind"] == "menu":
            if not menus:
                menus.append(_last(nodes[e["from"]]["label"]))
            menus.append(_last(nodes[e["to"]]["label"]))
            continue
        if e["kind"] == "opens" and re.match(r"(window|server|client):\d+$", e["to"]):
            via = " / ".join(menus) if menus else ""
            menus.clear()
            action, menu = e["to"].split(":")[1], e["from"].split(":")[1] if re.match(r"menu:\d+$", e["from"]) else ""
            url = f"/odoo/action-{action}" if modern else f"/web#{'menu_id=' + menu + '&' if menu else ''}action={action}"
            lines.append(f"o.goto({json.dumps(url)})" + (f"  # menu {via}: reached by number, names are only for searching" if via else ""))
            continue
        flush()
        if e["kind"] == "row":
            lines.append("o.open_row(row_text)  # row_text: the text of the record to open")
        elif e["kind"] == "button":
            lines.append(f"o.page.locator({json.dumps(selector_of('button', e.get('name', '')))}).first.click(); o.ready()  # button {json.dumps(e.get('label', ''), ensure_ascii=False)}")
    flush()
    return lines


FILL = {"char": 'o.fill({n}, "<text>")', "text": 'o.fill({n}, "<text>")', "integer": 'o.fill({n}, 1)', "float": 'o.fill({n}, 1.0)', "monetary": 'o.fill({n}, 1.0)',
        "date": 'o.fill({n}, "<date as the page shows it>")', "datetime": 'o.fill({n}, "<date time as the page shows it>")', "boolean": 'o.field({n}).check()'}


SAFE_WIDGETS = {"char": ("char", "url", "email", "phone"), "text": ("text",), "integer": ("integer",), "float": ("float",), "monetary": ("monetary",),
                "date": ("date", "daterange"), "datetime": ("datetime",), "boolean": ("boolean", "checkbox")}


def _fill_line(f):
    """One line that sets a field the way a person would, from its type; None when the map cannot say how (a table of lines, a picture, rich text)."""
    n = json.dumps(f["name"])
    if f["type"] == "many2one":
        return f'o.m2o({n}, "<part of the name of a {f.get("relation", "record")}>")'
    if f["type"] == "selection":
        if f.get("widget") not in (None, "", "select"):
            return None                                          # a custom widget (a timezone check, a radio, a status bar): its page is not a <select>
        value = (f.get("options") or ["<value>"])[0].partition("=")[0]
        return f'o.select({n}, {json.dumps(value, ensure_ascii=False)}, 0)'
    if f.get("widget") and f["widget"] not in SAFE_WIDGETS.get(f["type"], ()):
        return None                                               # a widget of its own (a domain editor, a colour, a tag list): not a plain input
    return FILL[f["type"]].format(n=n) if f["type"] in FILL else None


def codegen(data, screen, lang="en"):
    """Ready-made lines for one screen, from the map alone and with nothing guessed: how to reach it, how to fill each field it lets a person edit, which buttons
    it has, and what to check. Values are placeholders in <angle brackets> to replace. It is a start for `oqa try`: only `oqa try` proves a line on the page."""
    lang = output_language(lang)
    data = localized(data, lang)
    key = resolve(data, screen)
    if key is None:
        raise SitemapError(_t(lang, "nothing in the sitemap matches '{text}'", text=screen))
    forms = [v for v in views_of(data, key) if data["nodes"][v]["type"] == "form"]
    if not forms and data["nodes"][key]["kind"] == "view":
        forms = [key]
    if not forms:
        raise SitemapError(_t(lang, "{screen} has no form view to write lines for", screen=screen))
    view_key = forms[0]
    node = data["nodes"][view_key]
    out = [f"# {node['model']} form  ({view_key})  - values in <angle brackets> are yours to replace; only `oqa try` proves a line", "# --- reach it"]
    way = None
    try:
        way = path(data, "", view_key, lang)
    except SitemapError:
        pass
    if view_key.endswith(":0"):
        out.append("# note: this form is Odoo's default one (no form view of its own): the screen may edit rows inline in the list, and o.new() then opens no form")
    out += script_of(data, way) if way else ["# no way found from the menus: it is reached by a button that calls a method or a link the map cannot follow"]
    if "create" not in (node.get("disabled") or []):
        out.append("o.new()")
    out.append("# --- fields a person can set (* required)")
    seen = set()
    for f in node.get("fields") or []:
        if f["name"] in seen or f["readonly"] is True or f.get("invisible") is True:         # never shown: nothing to fill
            continue
        seen.add(f["name"])
        line = _fill_line(f)
        if line and f.get("invisible"):                                                       # shown only on a condition: it must not run before the condition holds
            line = "# only when not (" + str(f["invisible"]) + "): " + line
        mark = "*" if f["required"] is True else ("?" if f["required"] else " ")
        note = f'{mark} {f["label"]}' + (f"  options: {', '.join(f['options'])}" if f.get("options") else "")
        out.append((f"{line}  # {note}") if line else f"# {f['name']} ({f['type']}): no standard line, write it by hand  # {note}")
    if "edit" not in (node.get("disabled") or []):
        out.append("o.save()")
    buttons = [b for b in node.get("buttons") or [] if b.get("type") in ("action", "object") and b.get("name")]
    if buttons:
        out.append("# --- buttons")
    for b in buttons:
        out.append(f"o.page.locator({json.dumps(selector_of('button', b['name']))}).first.click(); o.ready()  # {b['label']}" + ("  (calls a method: where it leads is known only by running it)" if b["type"] == "object" else ""))
    out.append("# --- checks")
    shown = 0
    for f in node.get("fields") or []:
        if f["readonly"] is True and f["type"] in ("char", "selection", "integer", "float", "monetary", "date", "datetime", "many2one") and shown < 6:
            out.append(f"s.equals(o.value({json.dumps(f['name'])}), \"<expected>\")  # {f['label']} (read only: a result to check)" + (f"  options: {', '.join(f['options'])}" if f.get("options") else ""))
            shown += 1
    return "\n".join(out)


def text_path(data, source, target, lang="en"):
    lang = output_language(lang)
    data = localized(data, lang)
    way = path(data, source, target, lang)
    goal = resolve(data, target)
    if way is None:
        return _t(lang, "no way found to {target} from {source}: it may be reached only by a button that calls a method.", target=describe(goal, data['nodes'][goal], lang), source=source or _t(lang, 'any menu'))
    first = way[0]["from"] if way else goal
    lines = [_t(lang, "1. start at {target}", target=describe(first, data['nodes'][first], lang))]
    for number, e in enumerate(way, 2):
        node = data["nodes"][e["to"]]
        how = {"menu": _t(lang, "open the submenu") + (f" {selector_of('menu', node['xmlid'])}" if node.get("xmlid") else ""), "opens": _t(lang, "it opens"), "shows": _t(lang, "it shows"), "row": _t(lang, "click a row"),
               "button": _t(lang, 'click the button "{label}" {selector}', label=e.get('label', ''), selector=selector_of('button', e.get('name', '')))}[e["kind"]]
        lines.append(f"{number}. {how}: {describe(e['to'], data['nodes'][e['to']], lang)}")
    code = script_of(data, way)
    if code:
        lines += [_t(lang, "script for this way (a start to `oqa try`, not proof; menu names are in this language, the page must show the same):")] + ["    " + c for c in code]
    return "\n".join(lines)
