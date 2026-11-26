"""Build reusable data setup for an approved live case."""
import json
import re
from urllib.parse import urlsplit

from . import events, live, record_names, replays, sitemap, workspace
from .state import GateError


CREATE = """async spec => {
  const rpc = async (method, args, kwargs = {}) => {
    const response = await fetch('/web/dataset/call_kw/' + spec.model + '/' + method, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {model: spec.model, method, args, kwargs}})
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error?.data?.message || response.statusText);
    return result.result;
  };
  const remember = id => {
    sessionStorage.setItem('oqa:fixture:' + spec.values[spec.key], String(id));
    return id;
  };
  const resolve = value => {
    if (Array.isArray(value)) return value.map(resolve);
    if (!value || typeof value !== 'object') return value;
    if (typeof value.$fixture === 'string') {
      const id = sessionStorage.getItem('oqa:fixture:' + value.$fixture);
      if (!id) throw new Error('Prepare fixture first: ' + value.$fixture);
      return Number(id);
    }
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, resolve(entry)]));
  };
  const find = () => rpc('search_read', [[[spec.key, '=', spec.values[spec.key]]]], {fields: ['id', spec.key], limit: 2});
  const records = await find();
  if (records.length > 1) throw new Error('Fixture key is ambiguous: ' + spec.values[spec.key]);
  if (records.length) return remember(records[0].id);
  return remember(await rpc('create', [resolve(spec.values)]));
}"""


def form_for(data, screen):
    model_form = next(
        ((key, node) for key, node in data["nodes"].items()
         if node.get("type") == "form" and node.get("model") == screen), None
    )
    if model_form:
        return model_form
    key = sitemap.resolve(data, screen)
    forms = [data["nodes"][view] for view in sitemap.views_of(data, key)
             if data["nodes"][view]["type"] == "form"]
    if not forms:
        raise GateError(f"No mapped form for {screen!r}.")
    return key, forms[0]


def require_run_key(session, value, key_field, case):
    """Require a run-owned key so the case cannot reuse an earlier run's record."""
    prefix = workspace.load(session.ws)["test_prefix"]
    if not record_names.owns(session, value, prefix, case) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise GateError(f"{key_field} must carry this run's unique key, not a fixed name: take it from fixture(action=key, label=NAME) "
                        f"(`oqa fixture key CASE --label NAME`). A fixed name would reuse a record an earlier run left, with its old links and extra rows.")


def fixture_spec(session, case, screen, key_field, values):
    session.require("EXECUTE")
    live._strategy_case(session, case)
    attempt = live._attempt(session, case)
    _, form = form_for(sitemap.load(session.ws), screen)
    writable = {field["name"] for field in form.get("fields", []) if not field.get("readonly")}
    if not isinstance(values, dict) or not values or values.keys() - writable:
        unexpected = sorted(values.keys() - writable) if isinstance(values, dict) else []
        detail = f" Unexpected: {', '.join(unexpected)}." if unexpected else ""
        raise GateError(f"Supply writable field values for {form['model']}: {', '.join(sorted(writable))}.{detail}")
    value = values.get(key_field)
    if key_field not in values:
        raise GateError("The key argument is the technical field name, for example code. Include that field in values.")
    require_run_key(session, value, key_field, case)
    prepared = [event for event in events.read(session.dir) if event["kind"] == "fixture_prepared"
                and event.get("case") == case and event.get("attempt") == attempt]
    normalized = dict(values)
    for field in form.get("fields", []):
        name = field["name"]
        relation = values.get(name)
        if field.get("type") != "many2one" or relation in (None, False) or isinstance(relation, dict):
            continue
        match = next((event for event in prepared if event.get("model") == field.get("relation")
                      and relation in (event["key"], event.get("record_id"))), None)
        if not match:
            raise GateError(f"{name} needs a portable relation. Prepare a {field.get('relation')} fixture and use {{\"$fixture\": \"its key\"}}.")
        normalized[name] = {"$fixture": match["key"]}
    return {"model": form["model"], "key": key_field, "values": normalized}


def expression(spec):
    return "page.evaluate(" + " ".join(CREATE.split()) + ", " + json.dumps(spec, ensure_ascii=True) + ")"


def make_code(session, case, screen, key_field, values):
    return expression(fixture_spec(session, case, screen, key_field, values))


def apply(session, case, screen, key_field, values):
    spec = fixture_spec(session, case, screen, key_field, values)
    call = expression(spec)
    result = live._run(live.browser_case(session, case), "--raw", "run-code", "async page => " + call).strip()
    code = f"await {call};"
    attempt = live._attempt(session, case)
    events.append(session.dir, "fixture_prepared", case=case, attempt=attempt, key=values[key_field],
                  code=code, model=spec["model"], record_id=int(result) if result.isdecimal() else None)
    return {"id": result, "key": values[key_field], "code": code}


# --- reusable UI fixtures -------------------------------------------------------------------------------------------------------------
# Records a case needs are created the way a user would: through the screen, with locators that depend on the field's technical name (not on a label that changes
# with the language). No ORM write and no DOM edit stands in for an outcome. Existing records are only ever read.

PRESETS = {"ledger": "license.ledger", "license_user": "license.user", "contract": "contract.ledger"}
"""Short names for the records most cases need. A workspace can override or extend them with `fixture_models` in workspace.json."""
_RPC_READ = {"search_read", "search_count", "read", "name_search", "fields_get"}


def preset_model(session, name):
    configured = workspace.load(session.ws).get("fixture_models") or {}
    return {**PRESETS, **configured}.get(name, name)


def run_key(session, case, label=""):
    """A business name with a run-specific suffix for each new record."""
    prefix = workspace.load(session.ws)["test_prefix"]
    base = record_names.fixture_prefix(session, case, prefix)
    log = events.read(session.dir)
    try:
        attempt = live._attempt(session, case, log)
    except GateError:                                   # not started yet (or the last attempt is closed): the key is for the attempt that is about to start
        started = [e["attempt"] for e in log if e["kind"] == "case_started" and e.get("case") == case]
        attempt = started[-1] + 1 if started else 1
    again = f"a{attempt}-" if attempt > 1 else ""          # a case run again in the same session must not meet the record its blocked attempt left
    return base + again + (record_names.slug(label, limit=None) or "ban-ghi")


def date_expression(days=0):
    """A one-line JavaScript expression that gives the date `days` from today in the Odoo user's timezone, written in the user's own date format. Relative dates
    (for example "180 days ago") depend on the user's today, not the machine's, and the field must be typed as the page shows it."""
    body = ("async days => { const post = async (url, params) => (await (await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, "
            "body: JSON.stringify({jsonrpc: '2.0', method: 'call', params})})).json()).result; "
            "const ctx = window.odoo?.__session_info__?.user_context || (await post('/web/session/get_session_info', {}))?.user_context || {}; "
            "const tz = ctx.tz || Intl.DateTimeFormat().resolvedOptions().timeZone; "
            "const langs = await post('/web/dataset/call_kw/res.lang/search_read', {model: 'res.lang', method: 'search_read', args: [[['code', '=', ctx.lang]]], kwargs: {fields: ['date_format'], limit: 1}}); "
            "const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit'})"
            ".formatToParts(new Date(Date.now() + days * 864e5)).map(part => [part.type, part.value])); "
            "return ((langs && langs[0] && langs[0].date_format) || '%m/%d/%Y').replace('%Y', parts.year).replace('%y', parts.year.slice(2)).replace('%m', parts.month).replace('%d', parts.day); }")
    return f"await page.evaluate({body}, {int(days)})"


def _route(session, data, key):
    match = re.search(r'o\.goto\("([^"]+)"\)', sitemap.codegen(data, key))
    if not match:
        raise GateError("The sitemap has no route to this screen; rebuild it with `oqa sitemap build`.")
    base = urlsplit(workspace.load(session.ws)["url"])
    path = base.path.rstrip("/")
    return f"{base.scheme}://{base.netloc}" + re.sub(r"^/odoo\b", path or "/odoo", match.group(1))      # absolute: the browser sandbox has no URL() to resolve a relative one


def check_reference(session, case, model, text):
    """An existing record, read only: the name must match exactly one record, otherwise the case cannot rely on it. Nothing is created or changed."""
    spec = {"model": model, "text": text}
    code = ("async spec => { const response = await fetch('/web/dataset/call_kw/' + spec.model + '/name_search', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
            "body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {model: spec.model, method: 'name_search', args: [], kwargs: {name: spec.text, operator: '=', limit: 3, context: {active_test: false}}}})}); "
            "const result = await response.json(); if (result.error) throw new Error(result.error.data.message); return JSON.stringify(result.result); }")
    out = live._run(live.browser_case(session, case), "--raw", "run-code", "async page => page.evaluate(" + " ".join(code.split()) + ", " + json.dumps(spec) + ")")
    found = re.search(r"\[.*\]", out.replace('\\"', '"'))
    rows = json.loads(found.group(0)) if found else []
    if len(rows) != 1:
        raise GateError(f"{text!r} matches {len(rows)} record(s) of {model}; a case can only rely on an existing record that matches exactly one.")
    return rows[0][1]


def ui_lines(session, case, form, route, values, check=None):
    """The Playwright lines that create one record through the form. Values by field type:
    char/text/number: the value; date: 'text as the page shows it', or {"$date": -180} for 180 days before today (Odoo user timezone and date format);
    many2one: {"$ref": "text"} an existing record (read only, must match exactly one) or {"$fixture": "KEY"} a record this run created, typed by that name; selection: the label; boolean: true/false."""
    prepared = [e for e in events.read(session.dir) if e["kind"] == "data_created" and e.get("case") == case]
    fields = {field["name"]: field for field in form.get("fields", [])}
    unknown = sorted(set(values) - {n for n, f in fields.items() if not f.get("readonly")})
    if unknown:
        raise GateError(f"Not writable fields of {form['model']}: {', '.join(unknown)}.")
    lines = [f"await page.goto({json.dumps(route)});", "await page.locator('.o_list_button_add, .o_form_button_create').first().click();"]
    for name, value in values.items():
        field, widget = fields[name], f".o_field_widget[name={json.dumps(name)}]"
        kind = field.get("type")
        if kind == "many2one":
            if isinstance(value, dict) and "$fixture" in value:
                if not any(e["key"] == value["$fixture"] for e in prepared):
                    raise GateError(f"{name}: no fixture {value['$fixture']!r} was created by this case; create it first.")
                text = value["$fixture"]
            elif isinstance(value, dict) and "$ref" in value:
                text = value["$ref"]
                if check:
                    check(field["relation"], text)
            else:
                raise GateError(f"{name} is a relation: use {{\"$ref\": \"existing record\"}} or {{\"$fixture\": \"key of a record this case created\"}}.")
            lines.append(f"await page.locator({json.dumps(widget + ' input')}).fill({json.dumps(text)});")
            lines.append(f"await page.getByRole('option', {{name: {json.dumps(text)}, exact: true}}).click();")
        elif kind in ("date", "datetime"):
            amount = value.get("$date") if isinstance(value, dict) else None
            if isinstance(value, dict) and not isinstance(amount, int):
                raise GateError(f"{name}: a relative date is {{\"$date\": <days from today>}}.")
            lines.append(f"await page.locator({json.dumps(widget + ' input')}).fill({date_expression(amount) if amount is not None else json.dumps(str(value))});")
        elif kind == "selection":
            lines.append(f"await page.locator({json.dumps(widget + ' select')}).selectOption({{label: {json.dumps(str(value))}}});")
        elif kind == "boolean":
            lines.append(f"await page.locator({json.dumps(widget + ' input[type=checkbox]')}).{'check' if value else 'uncheck'}();")
        else:
            lines.append(f"await page.locator({json.dumps(widget + ' input, ' + widget + ' textarea')}).first().fill({json.dumps(str(value))});")
    lines.append("await page.locator('.o_form_button_save').click();")
    lines.append("await page.locator('.o_form_button_save').waitFor({state: 'hidden'});")
    return lines


def ui_create(session, case, screen, key_field, values):
    """Create a record through the screen (run in the case's own browser) and keep it as replayable setup. The key field must carry a key from `run_key`."""
    session.require("EXECUTE")
    live._strategy_case(session, case)
    attempt = live._attempt(session, case)
    data = sitemap.load(session.ws)
    key, form = form_for(data, preset_model(session, screen))
    value = values.get(key_field)
    require_run_key(session, value, key_field, case)
    if any(e["kind"] == "data_created" and e.get("case") == case and e["key"] == value for e in events.read(session.dir)):
        raise GateError(f"{value} was already created by this case.")
    lines = ui_lines(session, case, form, _route(session, data, key), values,
                     check=lambda model, text: check_reference(session, case, model, text))
    live.run_actions(live.browser_case(session, case), lines)
    code = "\n".join(lines)
    events.append(session.dir, "data_created", case=case, attempt=attempt, model=form["model"], field=key_field, key=value, via="ui")
    events.append(session.dir, "fixture_prepared", case=case, attempt=attempt, key=value, code=code, model=form["model"], record_id=None)
    return {"model": form["model"], "key": value, "code": code}


def created(session, run=None, case=None):
    """Return the tracked keys, optionally translated to the names used in a replay."""
    prefix = workspace.load(session.ws)["test_prefix"]
    saved = replays.load(session) if run else {}
    rewrites = {}
    seen, out = set(), []
    for event in events.read(session.dir):
        ident = event.get("case")
        if event["kind"] != "data_created" or (case is not None and ident != case):
            continue
        key = event["key"]
        if run and key.startswith(record_names.prefixes(prefix)):
            if ident not in rewrites:
                entry = next((item for item in saved.get(ident, {}).get("runs", []) if item.get("run_id") == run), {})
                target = entry.get("prefix") or record_names.replay_stem(prefix, record_names.case_slug(session, ident)) + f"{run}_"
                sources = entry.get("fixture_sources") or (record_names.prefixes(prefix) if entry else record_names.replay_sources(session, ident, prefix))
                rewrites[ident] = record_names.rewriter(sources, target)
            key = rewrites[ident](key)
        if (event["model"], event["field"], key) not in seen:
            seen.add((event["model"], event["field"], key))
            out.append({"model": event["model"], "field": event["field"], "key": key})
    return out


def cleanup(session, case, run=None, apply=False):
    """Remove the records this session's runs created (and only those: keys that start with the test prefix and were tracked). Without `apply` it only lists them.
    Records are found by their tracked key, archived ones included; a record that cannot be removed (the application refuses) is reported, never forced."""
    prefix = workspace.load(session.ws)["test_prefix"]
    items = [item for item in created(session, run, case) if item["key"].startswith(record_names.prefixes(prefix))]
    report = []
    if not items:
        return report
    live.open_browser(session, case)
    for item in items:
        entry = {**item, "found": None, "removed": False}
        probe = ("async spec => { const call = async (method, args, kwargs) => { const response = await fetch('/web/dataset/call_kw/' + spec.model + '/' + method, {method: 'POST', "
                 "headers: {'Content-Type': 'application/json'}, body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {model: spec.model, method, args, kwargs}})}); "
                 "const result = await response.json(); if (result.error) throw new Error(result.error.data.message); return result.result; }; "
                 "const ids = await call('search', [[[spec.field, '=like', spec.key + '%']]], {context: {active_test: false}}); "
                 "if (spec.apply && ids.length) await call('unlink', [ids], {}); return JSON.stringify(ids); }")
        spec = {**item, "apply": bool(apply)}
        try:
            out = live._run(live.browser_case(session, case), "--raw", "run-code", "async page => page.evaluate(" + " ".join(probe.split()) + ", " + json.dumps(spec) + ")")
            found = re.search(r"\[[\d,\s]*\]", out.replace('\\"', '"'))
            ids = json.loads(found.group(0)) if found else []
            entry.update(found=len(ids), removed=bool(apply and ids))
        except GateError as error:
            entry["error"] = str(error)[:200]
        report.append(entry)
    try:
        live._run(live.browser_case(session, case), "close")
    except GateError:
        pass
    return report
