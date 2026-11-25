// A replay owns its browser. A closed page never goes through the CLI's implicit tab creation.
const { readFileSync } = require("node:fs");
const { setTimeout: wait } = require("node:timers/promises");
const { performance } = require("node:perf_hooks");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function drawCaption(text) {
  function draw() {
    let caption;
    try {
      if (text) sessionStorage.setItem("__oqa_replay_caption", text);
      caption = sessionStorage.getItem("__oqa_replay_caption");
    } catch (_) { return; }
    if (!caption) return;
    let host = document.getElementById("__oqa_replay_caption");
    if (!host) {
      host = document.createElement("div");
      host.id = "__oqa_replay_caption";
      host.style.cssText = "position:fixed;bottom:12px;left:50%;transform:translateX(-50%);max-width:calc(100vw - 32px);z-index:2147483647;pointer-events:none";
      const line = document.createElement("div");
      line.setAttribute("role", "status");
      line.style.cssText = "padding:8px 14px;background:#172032;color:#fff;border-radius:6px;font:600 15px/1.4 system-ui,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis";
      host.attachShadow({ mode: "open" }).appendChild(line);
      document.body.appendChild(host);
    }
    host.shadowRoot.firstElementChild.textContent = caption;
  }
  if (document.body) draw();
  else addEventListener("DOMContentLoaded", draw, { once: true });
}

function drawFrame(box) {
  let host = document.getElementById("__oqa_replay_frame");
  if (!box) {
    if (host) host.hidden = true;
    return;
  }
  const left = Math.max(0, box.x - 3);
  const top = Math.max(0, box.y - 3);
  const right = Math.min(innerWidth, box.x + box.width + 3);
  const bottom = Math.min(innerHeight, box.y + box.height + 3);
  if (right <= left || bottom <= top) {
    if (host) host.hidden = true;
    return;
  }
  if (!host) {
    host = document.createElement("div");
    host.id = "__oqa_replay_frame";
    host.setAttribute("aria-hidden", "true");
    document.body.appendChild(host);
  }
  host.style.cssText = `position:fixed;left:${left}px;top:${top}px;width:${right - left}px;height:${bottom - top}px;box-sizing:border-box;border:3px solid #dc2626;border-radius:4px;pointer-events:none;z-index:2147483646`;
  host.hidden = false;
}

async function replay(config) {
  const { chromium } = require(config.module);
  const started = performance.now();
  const stop = new AbortController();
  const browser = await chromium.launch({ headless: false, ...(config.channel ? { channel: config.channel } : {}) });
  const result = { status: "FAIL", timing: { steps: [], calls: 0, call_s: 0, pause_s: 0 } };
  const pages = [];
  let closing = false;
  let active, profile, step;
  browser.on("disconnected", () => { if (!closing) stop.abort(); });
  const check = () => { if (stop.signal.aborted || active?.isClosed()) throw new Error("The replay window was closed."); };

  async function open(name) {
    check();
    const account = config.accounts[name];
    if (!account.state && !account.password) throw new Error("No signed-in state for profile " + name + ".");
    const context = await browser.newContext({
      viewport: { width: 1920, height: 1080 }, baseURL: config.url,          // the same full HD as the evidence pictures (oqa/browser.py VIEWPORT)
      ignoreHTTPSErrors: true, storageState: account.state || undefined,
      ...(config.videoDir ? { recordVideo: { dir: config.videoDir, size: { width: 1280, height: 720 } } } : {})
    });
    check();
    await context.addInitScript(drawCaption);
    check();
    const page = await context.newPage();
    pages.push(page);
    const previous = active;
    page.on("close", () => { if (!closing && active === page) stop.abort(); });
    page.on("dialog", dialog => {
      if (page.listenerCount("dialog") > 1) return;
      const answer = dialog.type() === "beforeunload" ? dialog.accept() : dialog.dismiss();
      void answer.catch(() => {});
    });
    check();
    active = page;
    profile = name;
    if (previous) await previous.context().close();
    check();
    if (account.state) await page.goto(config.url);
    if (!account.state || /\/login|\/web\/database\/selector/.test(new URL(page.url()).pathname)) {
      if (!account.password) throw new Error("Saved sign-in expired for profile " + name + ".");
      const url = new URL("/web/login", config.url);
      if (config.db) url.searchParams.set("db", config.db);
      await page.goto(url.href);
      await page.locator("input[name=login]").fill(account.login);
      await page.locator("input[name=password]").fill(account.password);
      await page.locator("button[type=submit]").click();
      await page.locator(".o_web_client").waitFor();
    }
  }

  async function actions(lines, kind) {
    const start = performance.now();
    try {
      for (const line of lines) {
        check();
        await new AsyncFunction("page", line)(active);
      }
    } finally {
      step[kind] += (performance.now() - start) / 1000;
      step.calls++;
    }
  }

  async function frame(target) {
    let box = null;
    if (target) {
      try {
        if (await target.count() === 1 && await target.isVisible()) box = await target.boundingBox({ timeout: 120 });
      } catch (_) {} // Evidence targets can be absent while a view changes.
    }
    check();
    await active.evaluate(drawFrame, box).catch(() => {}); // A navigation can replace the document between the lookup and drawing.
    check();
  }

  async function pause(ms, target) {
    check();
    if (!ms) return;
    const start = performance.now();
    try {
      if (!target) {
        await wait(ms, undefined, {signal: stop.signal});
        return;
      }
      let remaining = ms;
      while (remaining > 0) {
        await frame(target);
        remaining = ms - (performance.now() - start);
        if (remaining > 0) await wait(Math.min(remaining, 200), undefined, {signal: stop.signal});
        remaining = ms - (performance.now() - start);
      }
    } finally {
      step.pause_s += (performance.now() - start) / 1000;
    }
  }

  try {
    for (const block of config.blocks) {
      result.failed_step = block.n;
      step = { n: block.n, actions_s: 0, check_s: 0, pause_s: 0, calls: 0 };
      result.timing.steps.push(step);
      const name = block.profile || profile || config.initial;
      if (name !== profile) await open(name);
      check();
      await frame(null);
      await active.evaluate(drawCaption, block.label);
      let target;
      if (block.frame_locator) {
        try { target = await new AsyncFunction("page", "return " + block.frame_locator)(active); } catch (_) {}
      }
      const before = block.before && block.code.length;
      const leading = before ? block.code.slice(0, -1) : block.code;
      if (config.action_delay_ms) {
        for (const action of leading) {
          await actions([action], "actions_s");
          await pause(config.action_delay_ms, target);
        }
      } else {
        await actions(leading, "actions_s");
      }
      if (before) {
        await frame(target);
        await actions(block.code.slice(-1), "actions_s");
        await frame(null);
        await pause(config.action_delay_ms);
      }
      await actions(block.check, "check_s");
      if (!before) await frame(target);
      await pause(config.step_delay_ms, before ? null : target);
    }
    check();
    result.status = "PASS";
    delete result.failed_step;
  } catch (error) {
    if (stop.signal.aborted || active?.isClosed()) {
      result.status = "CANCELLED";
      result.reason = "Đã hủy vì cửa sổ Replay được đóng.";
    } else {
      result.reason = String(error.message || error).slice(0, 600);
    }
  } finally {
    closing = true;
    await browser.close().catch(() => {});
  }
  result.videos = (await Promise.all(pages.map(page => page.video()?.path().catch(() => null)))).filter(Boolean);
  for (const step of result.timing.steps) {
    result.timing.calls += step.calls;
    result.timing.call_s += step.actions_s + step.check_s;
    result.timing.pause_s += step.pause_s;
  }
  result.timing.total_s = (performance.now() - started) / 1000;
  for (const values of [result.timing, ...result.timing.steps]) {
    for (const key of Object.keys(values)) {
      if (key.endsWith("_s")) values[key] = Math.round(values[key] * 100) / 100;
    }
  }
  return result;
}

replay(JSON.parse(readFileSync(0, "utf8"))).then(
  outcome => process.stdout.write(JSON.stringify(outcome)),
  error => process.stdout.write(JSON.stringify({ status: "FAIL", reason: String(error.message || error).slice(0, 600), timing: {} }))
);
