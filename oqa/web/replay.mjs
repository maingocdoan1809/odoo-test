import { readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { chromium } from "playwright";

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
function delayOption(name, fallback) {
  const raw = process.env[name] ?? String(fallback);
  const value = Number(raw);
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value > 60000) {
    throw new Error(`${name} must be a whole number from 0 to 60000 milliseconds.`);
  }
  return value;
}
const fast = process.env.OQA_FAST === "1";
const actionDelay = fast ? 0 : delayOption("OQA_ACTION_DELAY_MS", 700);
const stepDelay = fast ? 0 : delayOption("OQA_STEP_DELAY_MS", 1500);
// Wait for the page to stop changing before a step is judged (Save, Discard and onchange re-render the form). An application wait, not a viewing pause.
const settle = page => page.waitForFunction(() => new Promise(resolve => {
  const started = performance.now(); let last = started;
  const watch = new MutationObserver(() => { last = performance.now(); });
  watch.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
  const tick = () => { const now = performance.now(); const busy = document.querySelector(".o_loading_indicator, .o_blockUI, .o_form_saving, .o_loading");
    if ((now - last >= 300 && !busy) || now - started >= 3000) { watch.disconnect(); resolve(true); } else setTimeout(tick, 100); };
  tick();
}), null, { timeout: 8000 });
const pause = ms => ms ? new Promise(resolve => setTimeout(resolve, ms)) : Promise.resolve();
const manifest = JSON.parse(await readFile(new URL("./cases.json", import.meta.url), "utf8"));
const caseId = process.argv[2];
if (!Object.hasOwn(manifest.cases, caseId)) {
  throw new Error("Choose a case: " + Object.keys(manifest.cases).join(", "));
}
const base = new URL(process.env.OQA_BASE_URL || manifest.baseUrl);
const original = new URL(manifest.baseUrl);
const stamp = randomUUID().slice(0, 10);
const prefix = manifest.prefix || "ZZ_AUTOTEST_";
const fresh = text => text.replaceAll(prefix, `${prefix}r${stamp}_`);
const runDir = `evidence/${caseId.replace(/[^\w.-]/g, "_")}-${stamp}`;
await mkdir(runDir, { recursive: true });
// The outcome goes in result.json beside the pictures and in the exit code (0 = PASS, 1 = FAIL), so a run by hand can be told apart without reading the log.
// `oqa results import` puts it on the dashboard. Passwords are never written here.
const started = Date.now();
const environment = process.env.OQA_ENV || "main";
let failedStep = null, reason = "";
async function saveResult(status) {
  const result = { schema: 1, workspace: manifest.workspace || null, script: manifest.cases[caseId].script || null, case: caseId, env: environment,
    status, at: new Date().toISOString().slice(0, 19).replace("T", " "), run_id: `r${stamp}`, failed_step: failedStep, reason: reason.slice(0, 600),
    base_url: base.origin, video: videoPath, total_s: Math.round((Date.now() - started) / 100) / 10, source: "manual" };
  await writeFile(`${runDir}/result.json`, JSON.stringify(result, null, 2));
}

// Video with a note of each step: OQA_VIDEO=on keeps it, OQA_VIDEO=fail keeps it only when the run fails. The note is drawn over the page, again after every page load.
const videoMode = ["on", "fail"].includes(process.env.OQA_VIDEO) ? process.env.OQA_VIDEO : "off";
let videoPath = null, outcome = "FAIL";
const videoPages = [];
const noteScript = () => {
  const draw = () => { try { const text = sessionStorage.getItem("__oqa_note"); let el = document.getElementById("__oqa_note");
    if (!text) { if (el) el.remove(); return; }
    if (!el) { el = document.createElement("div"); el.id = "__oqa_note";
      el.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:10px 16px;background:rgba(20,20,19,.88);color:#fff;font:600 16px/1.4 sans-serif;pointer-events:none";
      (document.body || document.documentElement).appendChild(el); }
    el.textContent = text; } catch (e) {} };
  window.__oqaDraw = draw; document.addEventListener("DOMContentLoaded", draw); window.addEventListener("load", draw);
};

const installedEdge = process.platform === "win32" &&
  [process.env["ProgramFiles(x86)"], process.env.ProgramFiles].some(
    root => root && existsSync(join(root, "Microsoft", "Edge", "Application", "msedge.exe")),
  );
const browser = await chromium.launch({ headless: process.env.OQA_HEADLESS === "1", ...(installedEdge ? { channel: "msedge" } : {}) });
try {
  const accounts = JSON.parse(process.env.OQA_PROFILES || "{}");
  const initial = manifest.cases[caseId].profile;
  const pages = new Map();
  async function signIn(profile) {
    if (pages.has(profile)) return pages.get(profile);
    const account = accounts[profile] || (profile === initial ? {
      login: process.env.OQA_LOGIN,
      password: process.env.OQA_PASSWORD,
      storageState: process.env.OQA_STORAGE_STATE,
    } : {});
    const context = await browser.newContext({ baseURL: base.href, storageState: account.storageState || undefined,
      ...(videoMode !== "off" ? { recordVideo: { dir: `${runDir}/video`, size: { width: 1280, height: 720 } } } : {}) });
    if (videoMode !== "off") await context.addInitScript(noteScript);
    const page = await context.newPage();
    videoPages.push(page);
    if (!account.storageState) {
      if (!account.login || !account.password) {
        throw new Error("Supply credentials for profile " + profile);
      }
      const login = new URL("/web/login", base);
      if (process.env.OQA_DB) login.searchParams.set("db", process.env.OQA_DB);
      await page.goto(login.href);
      await page.locator("input[name=login]").fill(account.login);
      await page.locator("input[name=password]").fill(account.password);
      await page.locator("button[type=submit]").click();
      await page.locator(".o_web_client").waitFor();
    }
    pages.set(profile, page);
    return page;
  }
  for (const step of manifest.cases[caseId].steps) {
    const page = await signIn(step.profile || initial);
    if (videoMode !== "off") {                       // a note that cannot be drawn never fails the run
      const text = `Step ${step.n}/${manifest.cases[caseId].steps.length}: ${step.action || ""}`.slice(0, 200);
      await page.evaluate(([src, note]) => { try { if (!window.__oqaDraw) (0, eval)(`(${src})()`); sessionStorage.setItem("__oqa_note", note); window.__oqaDraw && window.__oqaDraw(); } catch (e) {} }, [noteScript.toString(), text]).catch(() => {});
    }
    const code = step.code.map(line => fresh(line.replaceAll(original.origin, base.origin)));
    const execute = async lines => {
      for (const line of lines) {
        const action = new AsyncFunction("page", line);
        await action(page);
        await pause(actionDelay);
      }
    };
    try {
      if (step.before && code.length) {
        await execute(code.slice(0, -1));
        await page.screenshot({ path: `${runDir}/step-${step.n}-before.png` });
        await execute(code.slice(-1));
      } else {
        await execute(code);
      }
      await settle(page);
      await page.waitForFunction(text => { const norm = value => value.replace(/[\s\u00a0\u202f]+/g, ' ').trim(); return norm(document.body.innerText).includes(norm(text)); }, fresh(step.shows));
      await page.screenshot({ path: `${runDir}/step-${step.n}.png` });
      await pause(stepDelay);
    } catch (error) {
      await page.screenshot({ path: `${runDir}/step-${step.n}-failed.png` }).catch(() => {});
      failedStep = step.n;
      reason = String(error && error.message || error).split("\n")[0];
      throw new Error(`Case ${caseId}, step ${step.n}`, { cause: error });
    }
  }
  outcome = "PASS";
} catch (error) {
  if (!reason) reason = String(error && error.message || error).split("\n")[0];
} finally {
  // A video file exists only once its page is closed.
  const recorded = videoPages.map(page => page.video() ? page.video().path().catch(() => null) : null);
  await browser.close();
  const files = (await Promise.all(recorded)).filter(Boolean);
  if (videoMode === "fail" && outcome === "PASS") await Promise.all(files.map(file => rm(file, { force: true })));
  else videoPath = files[0] || null;
}
await saveResult(outcome);
if (outcome === "PASS") console.log(`${caseId}: PASS on ${environment}. Evidence: ${runDir}`);
else {
  console.error(`${caseId}: FAIL on ${environment}${failedStep ? `, step ${failedStep}` : ""}: ${reason}. Evidence: ${runDir}`);
  process.exitCode = 1;
}
