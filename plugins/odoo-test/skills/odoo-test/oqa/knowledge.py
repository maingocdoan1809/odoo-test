"""knowledge/index.md of a workspace: one line per fact, so `grep` finds it. What the assistant learned about THIS Odoo (a screen's quirk, a stable
selector, the page after login, an error and the fix that worked).

    - [screen:sale.order] [kind:quirk] [tag:save] The Save button only enables after a customer is chosen -> -
"""
import re
import unicodedata

from .paths import workspace_dir
from .store import locked

KINDS = ("login", "navigation", "locator", "data", "error", "fix", "quirk", "rule")
SCOPE = ("action", "condition", "profile", "state")
DETAILS = (*SCOPE, "evidence")
BUSINESS_KINDS = ("rule", "quirk", "error", "fix", "data", "locator")
HEADER = """# Knowledge index

Related facts share an entry. Each fact keeps its own evidence and source.
kind: login | navigation | locator | data | error | fix | quirk | rule
Optional labels: action, condition, profile, state, evidence.
Never write a password, token or cookie here.

"""
SECRETISH = re.compile(r"(password|passwd|mật\s*khẩu|token|cookie|session_id|api[_-]?key|secret)\s*[:=]", re.I)
LABELS = re.compile(r"\[([a-z]+):([^\]]+)\]")
PREFIX = re.compile(r"^((?:\[[a-z]+:[^\]]+\]\s*)*)(.*)$")
STOP_WORDS = set(("a an and are as at be by can check click from has in is it of on or that the then to use verify when with "
                  "anh ban bang buoc can cac cho co cua da dang day de den dieu duoc gi hay khi kiem lai la lam len mot "
                  "nhan nhung o qua ra sau se su tai thao thi thuc hien tra tren truoc tu va vao ve voi man hinh "
                  "case test").split())


class KnowledgeError(Exception):
    pass


def file_of(ws):
    return workspace_dir(ws) / "knowledge" / "index.md"


def words(text):
    """Fold Vietnamese accents while leaving Japanese syllables and technical IDs searchable."""
    folded = []
    for char in unicodedata.normalize("NFKD", str(text or "").casefold().replace("đ", "d")):
        if unicodedata.combining(char) and folded and folded[-1].isascii() and folded[-1].isalpha():
            continue
        folded.append(char)
    return tuple(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", "".join(folded))))


def terms(text):
    return {term[:-1] if term.isascii() and len(term) > 4 and term.endswith("s") and not term.endswith("ss") else term
            for term in words(text) if term not in STOP_WORDS and (len(term) > 1 or term.isdigit())}


def mentions(text, phrase):
    query, pattern = " ".join(words(text)), " ".join(words(phrase))
    if not pattern:
        return False
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", pattern):
        return pattern in query
    return f" {pattern} " in f" {query} "


def _normalized(value):
    return " ".join(str(value or "").casefold().split())


def _scope_key(item):
    return tuple(_normalized(item.get(key)) for key in ("screen", "kind", *SCOPE))


def _split(text):
    labels, content = PREFIX.fullmatch(text.strip()).groups()
    return dict(LABELS.findall(labels)), content.strip()


def _item(labels, content, line):
    fact, _, ref = content.partition(" -> ")
    return {"line": line, "screen": labels.get("screen", ""), "kind": labels.get("kind", ""), "tag": labels.get("tag", ""),
            "fact": fact.strip(), "ref": ref.strip() or "-", **{key: labels[key] for key in DETAILS if key in labels}}


def _entries(lines):
    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        start = index
        index += 1
        if not raw.startswith("- ["):
            continue
        labels, content = _split(raw[2:])
        if not labels:
            continue
        if content:
            yield start, index, [_item(labels, content, start + 1)]
            continue
        children = []
        while index < len(lines) and lines[index].startswith("  - "):
            local, fact = _split(lines[index][4:])
            if fact:
                children.append(_item({**labels, **local}, fact, index + 1))
            index += 1
        if children:
            yield start, index, children


def parse(text):
    return [item for _, _, group in _entries(text.splitlines()) for item in group]


def search(ws, screen=None, kind=None, text=None):
    path = file_of(ws)
    items = parse(path.read_text(encoding="utf-8")) if path.exists() else []
    normalized = " ".join(words(text))
    return [i for i in items if (not screen or " ".join(words(screen)) in " ".join(words(i["screen"])))
            and (not kind or i["kind"] == kind)
            and normalized in " ".join(words(" ".join(i.get(key, "") for key in ("fact", "tag", *DETAILS))))]


def related(items, text, *, screens=(), exact_screen=False, profile="", state="", limit=3):
    """Rank reusable checks and troubleshooting notes using the case context and any explicit scope."""
    query = terms(text)
    screen_terms = {words(name) for name in screens if name}
    profiles = {words(name) for name in re.split(r"[,/;|+]", profile or "")}
    scored = []
    for item in items:
        if item["kind"] not in BUSINESS_KINDS:
            continue
        if item.get("profile") and words(item["profile"]) not in profiles:
            continue
        if item.get("state") and not mentions(state, item["state"]):
            continue
        if any(item.get(key) and not (mentions(text, item[key]) or (terms(item[key]) and terms(item[key]) <= query))
               for key in ("action", "condition")):
            continue
        anchored = words(item["screen"]) in screen_terms
        if exact_screen and not anchored:
            continue
        direct = anchored or mentions(text, item["screen"])
        common = (query & terms(item["fact"] + " " + item["tag"])) - terms(item["screen"])
        specific = sum(bool(item.get(key)) for key in SCOPE)
        if not direct and (screen_terms or len(common) < 2):
            continue
        if not common and not specific:
            continue
        score = 8 * direct + 3 * specific + min(len(common), 5) + (item["kind"] == "rule")
        scored.append((score, item))
    return [item for _, item in sorted(scored, key=lambda entry: -entry[0])[:limit]]


def describe(item, limit=None):
    def compact(text, size):
        text = " ".join(str(text).split())
        return text if size is None or len(text) <= size else text[:size - 1].rstrip() + "…"

    limits = (35, 65, 25, 25, 85) if limit else (None,) * len(DETAILS)
    scope = " ".join(f"[{key}:{compact(item[key], size)}]" for key, size in zip(DETAILS, limits) if item.get(key))
    head = f"[screen:{compact(item['screen'], 55 if limit else None)}] [kind:{item['kind']}]"
    source = compact(item["ref"], 90 if limit else None) if item["ref"] != "-" else "knowledge/index.md:" + str(item["line"])
    return f"{head} {scope + ' ' if scope else ''}{compact(item['fact'], limit)} -> {source}"


def _labels(item, keys):
    return " ".join(f"[{key}:{item[key]}]" for key in keys if item.get(key))


def _render(items):
    scope = ("screen", "kind", *SCOPE)
    if len(items) == 1:
        item = items[0]
        return [f"- {_labels(item, (*scope, 'tag', 'evidence'))} {item['fact']} -> {item['ref']}"]
    lines = ["- " + _labels(items[0], scope)]
    for item in items:
        labels = _labels(item, ("tag", "evidence"))
        lines.append(f"  - {labels + ' ' if labels else ''}{item['fact']} -> {item['ref']}")
    return lines


def _merge_detail(existing, incoming):
    changed = False
    for key in ("evidence", "ref"):
        old = existing.get(key, "")
        parts = old.split(" | ") if old and old != "-" else []
        seen = {_normalized(part) for part in parts}
        for value in incoming.get(key, "").split(" | "):
            normalized = _normalized(value)
            if not normalized or normalized == "-" or normalized in seen:
                continue
            parts.append(value)
            seen.add(normalized)
            existing[key] = " | ".join(parts)
            changed = True
    return changed


def learn(ws, screen, kind, fact, tag="", ref="-", *, action="", condition="", profile="", state="", evidence=""):
    """Add a scoped knowledge entry or update its facts, evidence and sources."""
    screen, fact, tag, ref = (str(x or "").strip() for x in (screen, fact, tag, ref))
    labels = {"screen": screen, "kind": kind, "tag": tag, **dict(zip(
        DETAILS, (str(x or "").strip() for x in (action, condition, profile, state, evidence))))}
    if kind not in KINDS:
        raise KnowledgeError(f"kind must be one of {', '.join(KINDS)}")
    if not screen or not fact:
        raise KnowledgeError("a fact needs a screen and a sentence")
    if any("\n" in value or "\r" in value for value in (*labels.values(), fact, ref)) or any("]" in value for value in labels.values()):
        raise KnowledgeError("Use one line per fact. Scope labels cannot contain ']'.")
    if any(SECRETISH.search(value) for value in (*labels.values(), fact, ref)):
        raise KnowledgeError("that looks like a password, token or cookie; write what you learned without the secret")
    path = file_of(ws)
    incoming = {**labels, "fact": fact, "ref": ref or "-"}
    with locked(path):
        text = path.read_text(encoding="utf-8") if path.exists() else HEADER
        lines = text.splitlines()
        key = _scope_key(incoming)
        matches = [(start, end, items) for start, end, items in _entries(lines) if _scope_key(items[0]) == key]
        if matches:
            facts = {}
            for _, _, items in matches:
                for item in items:
                    normalized = _normalized(item["fact"])
                    if normalized in facts:
                        _merge_detail(facts[normalized], item)
                    else:
                        facts[normalized] = item
            normalized = _normalized(fact)
            if normalized not in facts:
                facts[normalized] = incoming
            elif not _merge_detail(facts[normalized], incoming) and len(matches) == 1:
                return "duplicate"
            for start, end, _ in reversed(matches):
                lines[start:end] = _render(list(facts.values())) if start == matches[0][0] else []
            result = "updated"
        else:
            lines.extend(_render([incoming]))
            result = "added"
        pending = path.with_name("." + path.name + ".tmp")
        try:
            pending.write_text("\n".join(lines) + "\n", encoding="utf-8")
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)
        return result
