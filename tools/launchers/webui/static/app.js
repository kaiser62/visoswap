"use strict";
/* VisoSwap dashboard. Vanilla JS, DOM built with textContent only (no innerHTML), no external requests.
   Secrets typed here are posted once and cleared; nothing but the access token (and the theme) is kept in localStorage. */
const NAMES = {kaggle: "Kaggle", colab: "Colab"};
const SUBS = {kaggle: "T4 x2 notebook kernel", colab: "L4 / T4 session"};
const STATE_LABEL = {down: "Down", starting: "Starting", queued: "Queued", running: "Running", unreachable: "Unreachable", healthy: "Healthy", stopping: "Stopping", error: "Error", unknown: "Checking"};
const $ = (id) => document.getElementById(id);
let token = null;
try { token = localStorage.getItem("vs_token"); } catch (e) { /* storage may be blocked */ }

// ------------------------------------------------------------------ helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v; else if (k === "text") el.textContent = v; else if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k.nodeType ? k : document.createTextNode(String(k)));
  return el;
}
function fmtDur(s) {
  if (s == null) return "unknown";
  s = Math.max(0, Math.round(s));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  if (hh) return hh + "h " + String(mm).padStart(2, "0") + "m";
  if (mm) return mm + "m " + String(s % 60).padStart(2, "0") + "s";
  return s + "s";
}
function fmtAge(s) { return s == null ? "" : s < 5 ? "just now" : s < 90 ? Math.round(s) + "s ago" : Math.round(s / 60) + " min ago"; }
function fmtDate(iso) { try { return new Date(iso).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}); } catch (e) { return iso || "unknown"; } }
function say(msg, kind) { const b = $("banner"); b.textContent = msg || ""; b.className = "banner" + (kind ? " " + kind : ""); b.hidden = !msg; }
async function api(path, opts = {}) {
  const r = await fetch(path, {...opts, headers: {"Authorization": "Bearer " + token, "Content-Type": "application/json"}});
  if (r.status === 401) { signOut("Invalid token."); throw new Error("auth"); }
  if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch (e) { /* not json */ } throw new Error(d || ("HTTP " + r.status)); }
  return r.json();
}
const post = (path, body) => api(path, {method: "POST", body: JSON.stringify(body || {})});
function ask(title, text, okLabel) {
  return new Promise((res) => {
    const d = $("confirm"); $("confirm-title").textContent = title; $("confirm-text").textContent = text; $("confirm-ok").textContent = okLabel;
    d.addEventListener("close", () => res(d.returnValue === "yes"), {once: true});
    d.returnValue = "no"; d.showModal();
  });
}
async function copy(text, btn) {
  const old = btn.textContent;
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; } catch (e) { btn.textContent = "Copy failed"; }
  setTimeout(() => { btn.textContent = old; }, 1500);
}

// ------------------------------------------------------------------ theme (no inline script: CSP forbids it)
const THEMES = ["auto", "dark", "light"];
function setTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
  $("theme").textContent = "Theme: " + t;
  try { localStorage.setItem("vs_theme", t); } catch (e) { /* ignore */ }
}
let theme = "auto"; try { theme = localStorage.getItem("vs_theme") || "auto"; } catch (e) { /* ignore */ }
$("theme").onclick = () => { theme = THEMES[(THEMES.indexOf(theme) + 1) % 3]; setTheme(theme); };
setTheme(THEMES.includes(theme) ? theme : "auto");

// ------------------------------------------------------------------ sign in / out
function signOut(msg) {
  token = null; try { localStorage.removeItem("vs_token"); } catch (e) { /* ignore */ }
  clearTimeout(tickTimer); $("login").hidden = false; $("app").hidden = true; $("lerr").textContent = msg || "";
}
$("lock").onclick = () => signOut("");
$("lf").onsubmit = (e) => { e.preventDefault(); token = $("tok").value.trim(); $("tok").value = ""; try { localStorage.setItem("vs_token", token); } catch (er) { /* ignore */ } start(); };

// ------------------------------------------------------------------ backend cards
const cards = {};          // target -> {el, refs, filled}
let DASH = null, CHECKS = null, tickTimer = null, lastFetch = 0;

function buildCard(t) {
  const r = {};
  r.badge = h("span", {class: "chip", role: "status"});
  r.help = h("p", {class: "state-help"});
  r.link = h("a", {target: "_blank", rel: "noopener"}); r.copy = h("button", {class: "ghost", type: "button", text: "Copy URL"});
  r.urlrow = h("div", {class: "urlrow"}, r.link, r.copy);
  r.notes = h("div", {class: "notes"});
  r.meta = h("dl", {class: "meta"});
  r.bar = h("div", {class: "bar", hidden: true}, h("i"));
  r.start = h("button", {class: "primary", type: "button", text: "Start"});
  r.restart = h("button", {type: "button", text: "Restart"});
  r.stop = h("button", {class: "danger", type: "button", text: "Stop"});
  r.opts = optionsForm(t, r);
  const el = h("article", {class: "card", "aria-labelledby": "ct-" + t},
    h("div", {class: "card-head"}, h("div", {}, h("h3", {id: "ct-" + t, text: NAMES[t]}), h("p", {class: "state-help", text: SUBS[t]})), r.badge),
    r.help, r.urlrow, r.notes, r.meta, r.bar, r.opts.details, h("div", {class: "actions"}, r.start, r.restart, r.stop));
  r.start.onclick = () => runAction(t, "start", r.opts.read());
  r.restart.onclick = async () => { if (await ask("Restart " + NAMES[t] + "?", "The backend restarts (about a minute offline). Options above are applied.", "Restart")) runAction(t, "restart", r.opts.read()); };
  r.stop.onclick = async () => {
    const extra = t === "colab" ? " A new GPU session may be hard to get and L4 uses compute units." : " Starting again takes about 5 minutes.";
    if (await ask("Stop " + NAMES[t] + "?", "The backend goes offline and its GPU session or kernel is deleted." + extra, "Stop " + NAMES[t])) runAction(t, "stop", {}, true);
  };
  r.copy.onclick = () => r.url && copy(r.url, r.copy);
  cards[t] = {el, r, filled: false};
  return el;
}
function field(label, input) { const id = "f-" + Math.random().toString(36).slice(2, 8); input.id = id; return h("div", {class: "field"}, h("label", {for: id, text: label}), input); }
function select(opts) { return h("select", {}, opts.map(([v, l]) => h("option", {value: v, text: l}))); }
function optionsForm(t, r) {
  const f = {};
  f.concurrency = select([["", "keep / auto"], ["auto", "auto"], ...[1, 2, 3, 4, 6, 8].map((n) => [String(n), String(n) + " workers"])]);
  f.profile = select([["", "keep"], ["on", "on"], ["off", "off"]]);
  const fields = [];
  if (t === "colab") {
    f.gpu = select([["", "default (L4)"], ["L4", "L4 (uses compute units)"], ["T4", "T4 (cheaper)"]]);
    f.api_key = select([["", "keep as is"], ["on", "require key (key file)"], ["off", "no key"]]);
    fields.push(field("GPU (new session only)", f.gpu), field("Concurrency", f.concurrency), field("Profiling", f.profile), field("Access key", f.api_key));
  } else {
    f.max = h("input", {type: "number", min: "0", max: "720", step: "5", placeholder: "default 90, 0 = never"});
    f.api_key = select([["", "keep as is"], ["on", "require key (key file)"], ["off", "no key"]]);
    fields.push(field("Concurrency (new kernel)", f.concurrency), field("Profiling (new kernel)", f.profile),
                field("Access key", f.api_key), field("Max minutes", f.max));
  }
  const note = h("p", {class: "muted small", text: t === "colab"
    ? "GPU applies when a new session is created. Concurrency, profiling and the key apply to Start or Restart. The key comes from ~/.visoswap/api_key on the host (mode 600) and is never shown."
    : "On a kernel that is already running these apply only with Restart. The access key is baked into the private kernel notebook and is never shown."});
  const details = h("details", {}, h("summary", {text: "Options"}), h("div", {class: "body"}, h("div", {class: "fields"}, fields), note));
  return {
    details, f,
    fill(last) {
      if (last.gpu) f.gpu.value = last.gpu;
      if (last.concurrency != null) f.concurrency.value = String(last.concurrency);
      if (last.profile != null) f.profile.value = last.profile ? "on" : "off";
      if (last.api_key) f.api_key.value = last.api_key;
      if (last.max_minutes != null && f.max) f.max.value = String(last.max_minutes);
    },
    read() {
      const o = {};
      if (f.gpu && f.gpu.value) o.gpu = f.gpu.value;
      if (f.concurrency.value) o.concurrency = f.concurrency.value === "auto" ? "auto" : Number(f.concurrency.value);
      if (f.profile.value) o.profile = f.profile.value === "on";
      if (f.api_key && f.api_key.value) o.api_key = f.api_key.value;
      if (f.max && f.max.value !== "") o.max_minutes = Number(f.max.value);
      return o;
    },
  };
}
function metaRow(dl, label, value, extra) {
  const dd = h("dd", {}, value == null || value === "" ? h("span", {class: "muted", text: "unknown"}) : value);
  if (extra) dd.append(extra);
  dl.append(h("dt", {text: label}), dd);
}
function updateCard(t, d) {
  const c = cards[t] || (cards[t] = {}); const r = c.r;
  r.badge.className = "chip s-" + d.state; r.badge.replaceChildren(STATE_LABEL[d.state] || d.state);
  r.help.textContent = d.pending ? "Querying the backend (can take a few seconds)..." : d.state_help + (d.age_s != null ? " - checked " + fmtAge(d.age_s) : "");
  r.url = d.url; r.urlrow.hidden = !d.url;
  if (d.url) { r.link.href = (d.mobile_url || d.url); r.link.textContent = d.url; }
  r.notes.replaceChildren();
  if (d.error) r.notes.append(h("div", {class: "note bad", text: d.error + (d.next ? " Next: " + d.next : "")}));
  for (const w of d.warnings || []) r.notes.append(h("div", {class: "note", text: w}));
  r.meta.replaceChildren();
  metaRow(r.meta, "GPU", d.gpu);
  metaRow(r.meta, "Concurrency", d.concurrency != null ? String(d.concurrency) : null);
  metaRow(r.meta, "Process mode", d.process_mode);
  metaRow(r.meta, "Access key", d.auth == null ? null : d.auth === "required" ? "required" : "off");
  metaRow(r.meta, "Uptime", d.uptime_s != null ? fmtDur(d.uptime_s) : null, d.uptime_source ? h("span", {class: "muted small", text: " (" + d.uptime_source + ")"}) : null);
  if (t === "kaggle") {
    metaRow(r.meta, "Time left", d.time_left_s != null ? fmtDur(d.time_left_s) : null, d.max_minutes != null ? h("span", {class: "muted small", text: d.max_minutes ? " of " + d.max_minutes + " min cap" : ""}) : null);
    if (d.max_minutes_source) r.meta.append(h("dt"), h("dd", {class: "muted small", text: d.max_minutes_source}));
  }
  if (d.health && d.health.version) metaRow(r.meta, "Version", String(d.health.version));
  const showBar = t === "kaggle" && d.time_left_s != null && d.max_minutes > 0;
  r.bar.hidden = !showBar;
  if (showBar) {
    const pct = Math.max(0, Math.min(100, (100 * d.time_left_s) / (d.max_minutes * 60)));
    r.bar.firstChild.style.setProperty("--w", pct + "%"); r.bar.classList.toggle("low", pct < 20);
    r.bar.setAttribute("role", "img"); r.bar.setAttribute("aria-label", Math.round(pct) + " percent of the time cap left");
  }
  const busy = d.busy || !!activeJob;
  r.start.disabled = busy || d.state === "healthy" || d.state === "running" || d.state === "unreachable"; r.restart.disabled = busy || d.state === "down" || d.state === "queued";
  r.stop.disabled = busy || d.state === "down";
  if (!c.filled) { r.opts.fill(d.last_options || {}); c.filled = true; }
}
function renderDash() {
  if (!DASH) return;
  const box = $("cards");
  for (const t of Object.keys(NAMES)) { if (!cards[t]) box.append(buildCard(t)); updateCard(t, DASH.targets[t]); }
  const b = $("bridge");
  b.hidden = !DASH.bridge.configured;
  if (DASH.bridge.configured) { b.className = "chip s-" + (DASH.bridge.ok ? "ok" : "bad"); b.textContent = "Mac bridge: " + (DASH.bridge.ok ? "connected" : "unreachable"); b.title = DASH.bridge.detail || "Colab runs on the host through the bridge"; }
  if (DASH.fake && !DASH.bridge.configured) { b.hidden = false; b.className = "chip s-warn"; b.textContent = "Fake mode"; }
}
async function loadDash(force) {
  try {
    DASH = await api("/api/dashboard" + (force ? "?refresh=1" : "")); lastFetch = Date.now(); renderDash();
    return DASH.pending;
  } catch (e) { if (e.message !== "auth") say("Cannot reach the dashboard service: " + e.message, "bad"); return false; }
}
function schedule(ms) { clearTimeout(tickTimer); tickTimer = setTimeout(tick, ms); }
async function tick() {
  if (!token) return;
  if (document.hidden) { schedule(5000); return; }
  const pending = await loadDash(false);
  schedule(pending || activeJob ? 1500 : 20000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && token && Date.now() - lastFetch > 10000) tick(); });
$("refresh").onclick = async () => { say(""); const p = await loadDash(true); loadQuotas(true); loadChecks(true); if (p) schedule(1500); };
setInterval(() => { if (!document.hidden && DASH && DASH.targets) for (const t of Object.keys(DASH.targets)) { const d = DASH.targets[t], r = cards[t] && cards[t].r; if (!r || d.uptime_s == null) continue; const dt = (Date.now() - lastFetch) / 1000; r.meta.replaceChildren(); updateCard(t, {...d, uptime_s: d.uptime_s + dt, time_left_s: d.time_left_s == null ? null : Math.max(0, d.time_left_s - dt), age_s: d.age_s == null ? null : d.age_s + dt}); } }, 15000);

// ------------------------------------------------------------------ actions and the live job panel
let activeJob = null, since = 0, activeAct = null, activeTarget = null, pending = null;
async function runAction(t, act, options, confirmed) {
  say("");
  try {
    const r = await post(`/api/targets/${t}/${act}`, {options: options || {}, confirm: !!confirmed});
    follow(r.job, t, act);
  } catch (e) { say("Refused: " + e.message, "bad"); }
}
function follow(job, t, act) {
  activeJob = job; activeTarget = t; activeAct = act; since = 0; hidePrompt();
  $("cancel").hidden = false;
  $("jobbox").hidden = false; $("log").textContent = ""; $("logfor").textContent = NAMES[t] + ": " + act;
  setJobState("running"); renderDash(); pollJob(); schedule(1500);
}
function setJobState(s) { const b = $("jobstate"); b.className = "chip s-" + (s === "failed" ? "error" : s === "done" ? "healthy" : "starting"); b.textContent = s === "waiting" ? "Waiting for you" : s === "running" ? "Running" : s === "done" ? "Done" : "Failed"; }
async function pollJob() {
  if (!activeJob) return;
  try {
    const j = await api(`/api/jobs/${activeJob}?since=${since}`);
    const log = $("log");
    if (j.lines.length) { const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30; log.textContent += j.lines.join("\n") + "\n"; if (stick) log.scrollTop = log.scrollHeight; }
    since = j.next; setJobState(j.state);
    if (j.state === "waiting" && j.prompt) { showPrompt(j.prompt); setTimeout(pollJob, 1500); return; }
    hidePrompt();
    if (j.state === "running") { setTimeout(pollJob, 1000); return; }
    finishJob(j);
  } catch (e) { if (e.message !== "auth") setTimeout(pollJob, 3000); }
}
function finishJob(j) {
  const r = j.result || {}, t = activeTarget, act = activeAct; activeJob = null; $("cancel").hidden = true;
  $("log").textContent += (r.ok ? "DONE" : "FAILED: " + (r.error || "")) + (r.next ? "\nNext: " + r.next : "") + "\n";
  say(r.ok ? NAMES[t] + " " + act + ": done. " + (r.summary || "") : NAMES[t] + " " + act + " failed: " + (r.error || "unknown error"), r.ok ? "ok" : "bad");
  if (!r.ok && Array.isArray(r.checks) && r.checks.length) {      // blocked on a missing login: show it and re-run the action once it is fixed
    CHECKS = CHECKS || {targets: {}, login_methods: null}; CHECKS.targets[t] = {checks: r.checks}; renderLogins();
    if (act !== "login") pending = {t, act};
    $("logins").scrollIntoView({behavior: "smooth", block: "start"});
  }
  if (r.ok && act !== "login") pending = null;
  loadDash(true); loadQuotas(false); loadChecks(true);
  if (r.ok && (act === "login")) setTimeout(continueAfterLogin, 800, t);
  schedule(2000);
}
function continueAfterLogin(t) { if (pending && pending.t === t && !activeJob) { const p = pending; pending = null; say("Continuing: " + p.act + " " + NAMES[t] + "..."); runAction(p.t, p.act, {}, p.act === "stop"); } }
$("cancel").onclick = async () => { if (activeJob) { try { await post(`/api/jobs/${activeJob}/cancel`); } catch (e) { /* already ended */ } } };
function hidePrompt() { const b = $("prompt"); b.hidden = true; b.replaceChildren(); delete b.dataset.msg; }
function secretForm(label, button, onSubmit, multiline) {
  const input = multiline ? h("textarea", {autocomplete: "off", spellcheck: "false", "aria-label": label, placeholder: label}) : h("input", {type: "password", autocomplete: "off", "aria-label": label, placeholder: label});
  const f = h("form", {class: "stack"}, input, h("button", {class: "primary", type: "submit", text: button}));
  f.onsubmit = async (e) => { e.preventDefault(); const v = input.value.trim(); input.value = ""; if (v) await onSubmit(v); };
  return f;
}
function showPrompt(p) {
  const b = $("prompt"); if (!b.hidden && b.dataset.msg === p.msg) return;
  b.hidden = false; b.dataset.msg = p.msg; b.replaceChildren(h("strong", {text: p.msg}));
  if (p.url) b.append(h("a", {href: p.url, target: "_blank", rel: "noopener noreferrer", text: "Open the Google sign-in page"}));
  b.append(secretForm(p.kind === "code" ? "Paste the code here" : "Paste the token here (never stored in the browser)", "Submit", async (v) => {
    try { await post(`/api/jobs/${activeJob}/input`, {value: v}); hidePrompt(); } catch (e) { say("Not accepted: " + e.message, "bad"); } }));
  b.querySelector("input,textarea").focus();
}

// ------------------------------------------------------------------ quotas
let QUOTAS = null;
function bar(pct, cls) { const b = h("div", {class: "bar " + (cls || ""), role: "img", "aria-label": Math.round(pct) + " percent left"}, h("i")); b.firstChild.style.setProperty("--w", Math.max(0, Math.min(100, pct)) + "%"); return b; }
function quotaCard(t, q) {
  const body = [h("div", {class: "card-head"}, h("h3", {text: t === "kaggle" ? "Kaggle GPU hours" : "Colab compute units"}),
    q.warn ? h("span", {class: "chip " + (q.warn === "empty" ? "s-bad" : "s-warn"), text: q.warn === "empty" ? "Empty" : "Low"}) : null)];
  if (q.pending && !q.available) body.push(h("p", {class: "muted", text: "Checking..."}));
  else if (!q.available) {
    body.push(h("p", {class: "muted", text: q.summary || "Not available"}));
    if (q.login_required) body.push(h("p", {class: "note", text: "Log in to Colab (Logins below) to see compute units."}));
  } else if (t === "kaggle") {
    const g = q.gpu;
    body.push(h("div", {class: "big", text: g.remaining + " h left"}), h("p", {class: "muted", text: "of " + g.total + " h this week, used " + g.used + " h"}),
      bar(q.percent_left == null ? 0 : q.percent_left, q.warn), h("p", {class: "muted small", text: "Resets " + fmtDate(g.refresh_at) + ". Limit: 2 GPU kernels at once."}));
  } else {
    body.push(h("div", {class: "big", text: q.balance + " units"}),
      h("p", {class: "muted", text: q.rate ? "Burning " + q.rate + " units/h (about " + q.hours_left + " h left)" : "Not burning units right now (no active GPU session)"}));
  }
  if (q.warn_text) body.push(h("div", {class: "note " + (q.warn === "empty" ? "bad" : ""), text: q.warn_text}));
  if (q.age_s != null) body.push(h("p", {class: "muted small", text: "Updated " + fmtAge(q.age_s) + ". Cached to spare the CLI."}));
  return h("article", {class: "card"}, body);
}
function renderQuotas() { if (!QUOTAS) return; $("quotas").replaceChildren(...Object.keys(NAMES).map((t) => quotaCard(t, QUOTAS[t]))); }
async function loadQuotas(force) {
  try {
    for (let i = 0; i < 40; i++) {
      QUOTAS = await api("/api/quotas" + (force ? "?refresh=1" : "")); renderQuotas();
      if (!QUOTAS.pending) break;
      force = false; await new Promise((r) => setTimeout(r, 1500));
    }
  } catch (e) { /* the cards say "Not available" */ }
}
$("qrefresh").onclick = () => loadQuotas(true);

// ------------------------------------------------------------------ logs
const LOG_SOURCES = [["kaggle", "job", "Kaggle - launcher job log"], ["colab", "job", "Colab - launcher job log"], ["kaggle", "kernel", "Kaggle - kernel log (remote)"],
  ["colab", "uvicorn", "Colab - uvicorn log (remote)"], ["colab", "tunnel", "Colab - tunnel log (remote)"]];
for (const [t, s, l] of LOG_SOURCES) $("lgsrc").append(h("option", {value: t + "/" + s, text: l}));
async function loadLog() {
  const [t, s] = $("lgsrc").value.split("/"), n = $("lgn").value, v = $("lgview");
  $("lgmeta").textContent = "Loading..."; if (s !== "job") $("lgload").disabled = true;
  try {
    const r = await api(`/api/logs/${t}/${s}?lines=${n}&refresh=1`);
    v.textContent = r.ok ? (r.lines.join("\n") || "(empty)") : (r.error || "unavailable");
    $("lgmeta").textContent = (r.ok ? r.lines.length + " lines" : "Not available") + " - secrets are redacted" + (s === "job" ? "" : " - fetched through the launcher, " + fmtAge(r.age_s));
    v.scrollTop = v.scrollHeight;
  } catch (e) { v.textContent = "Cannot load: " + e.message; $("lgmeta").textContent = ""; }
  $("lgload").disabled = false;
}
$("lgload").onclick = loadLog; $("lgsrc").onchange = loadLog; $("lgn").onchange = loadLog;
$("lgcopy").onclick = () => copy($("lgview").textContent, $("lgcopy"));

// ------------------------------------------------------------------ logins
function stateBadge(s) { return h("span", {class: "chip " + (s === "ok" ? "s-ok" : s === "unknown" ? "s-warn" : "s-bad"), text: s === "ok" ? "Logged in" : s === "unknown" ? "Unknown" : s === "invalid" ? "Not logged in" : "Missing"}); }
function readFile(input) { return new Promise((res) => { const f = input.files && input.files[0]; if (!f) return res(""); if (f.size > 8192) return res(null); const fr = new FileReader(); fr.onload = () => res(String(fr.result)); fr.readAsText(f); }); }
function jsonImport(label, hint, path, okMsg, t) {
  const file = h("input", {type: "file", accept: ".json,application/json", "aria-label": label});
  const form = secretForm("or paste the file content", "Import", async (v) => doJson(v), true);
  async function doJson(text) {
    say("Importing...");
    try { const r = await post(path, {json: text}); say(r.ok ? okMsg : "Not accepted: " + (r.error || "rejected"), r.ok ? "ok" : "bad"); if (r.ok) { loadChecks(true); loadDash(true); loadQuotas(true); continueAfterLogin(t); } }
    catch (e) { say("Refused: " + e.message, "bad"); }
  }
  file.onchange = async () => { const txt = await readFile(file); file.value = ""; if (txt === null) return say("That file is too large to be a credentials file.", "bad"); if (txt) doJson(txt); };
  return [h("p", {class: "muted small", text: hint}), h("label", {text: "Choose the file"}), file, form];
}
function cmdBlock(c) { const b = h("button", {class: "ghost", type: "button", text: "Copy"}); b.onclick = () => copy(c, b); return h("div", {class: "row"}, h("code", {text: c}), b); }
function methodBody(svc, m, t) {
  const note = !m.available ? [h("p", {class: "note", text: m.why || "Not available here."})] : [];
  if (m.id === "cli") return [h("p", {class: "muted small", text: "Run this in a terminal on the host, from the repository root:"}), cmdBlock(m.command)];
  if (svc === "kaggle" && m.id === "token") return [h("p", {class: "muted small", text: m.hint}), h("button", {class: "primary", type: "button", text: "Start token login", onclick: () => runAction("kaggle", "login")})];
  if (svc === "kaggle" && m.id === "json") return jsonImport("kaggle.json", m.hint, "/api/targets/kaggle/kaggle-json", "kaggle.json stored and verified.", "kaggle");
  if (svc === "colab" && m.id === "relay") return [...note, h("p", {class: "muted small", text: "Opens Google's sign-in; you paste the code it shows." + (m.hint ? " " + m.hint + "." : "")}), h("button", {class: "primary", type: "button", disabled: !m.available, text: "Log in with Google", onclick: () => runAction("colab", "login")})];
  if (svc === "colab" && m.id === "token-file") return [...jsonImport("Colab token file", m.hint, "/api/targets/colab/colab-token", "Colab login imported and verified.", "colab"), h("p", {class: "muted small", text: "Export command (macOS, on a machine where Colab is logged in):"}), cmdBlock(m.command)];
  if (svc === "cloudflare") return []; // handled per target below
  return [];
}
function cfForms(t, methods) {
  const tokenM = methods.find((m) => m.id === "token"), apiM = methods.find((m) => m.id === "api-token"), cli = methods.find((m) => m.id === "cli");
  return [
    h("details", {}, h("summary", {text: "Paste a tunnel token"}), h("div", {class: "body"}, h("p", {class: "muted small", text: tokenM.hint}),
      secretForm("Tunnel token", "Save token", async (v) => {
        try { const r = await post(`/api/targets/${t}/cloudflare-token`, {token: v}); say(r.ok ? "Tunnel token stored (mode 600)." : "Not stored: " + r.error, r.ok ? "ok" : "bad"); loadChecks(true); if (r.ok) continueAfterLogin(t); }
        catch (e) { say("Refused: " + e.message, "bad"); } }))),
    h("details", {}, h("summary", {text: "Use a Cloudflare API token (creates tunnel + DNS)"}), h("div", {class: "body"}, h("p", {class: "muted small", text: apiM.hint}),
      secretForm("Cloudflare API token", "Create tunnel", async (v) => {
        say("Creating the tunnel and DNS record...");
        try { const r = await post(`/api/targets/${t}/cloudflare-api-token`, {token: v}); say(r.ok ? (r.summary || "Tunnel ready.") : "Failed: " + r.error, r.ok ? "ok" : "bad"); loadChecks(true); if (r.ok) continueAfterLogin(t); }
        catch (e) { say("Refused: " + e.message, "bad"); } }))),
    h("details", {}, h("summary", {text: "Native command"}), h("div", {class: "body"}, cmdBlock(cli.command)))];
}
function svcCard(title, check, bodyNodes) {
  return h("article", {class: "card svc"}, h("div", {class: "card-head"}, h("h3", {text: title}), check ? stateBadge(check.state) : h("span", {class: "chip s-unknown", text: "Checking"})),
    check && check.detail ? h("p", {class: "state-help", text: check.detail}) : null, bodyNodes);
}
function renderLogins() {
  const box = $("logins"); if (!CHECKS || !CHECKS.login_methods) { box.replaceChildren(h("p", {class: "muted", text: "Checking logins..."})); return; }
  const M = CHECKS.login_methods, find = (t, re) => ((CHECKS.targets[t] || {}).checks || []).find((c) => re.test(c.id));
  const nodes = [];
  const ka = find("kaggle", /auth$/), co = find("colab", /login$/);
  const methods = (svc, t, check) => (check && check.state === "ok" ? [h("p", {class: "muted small", text: "Backup methods are listed below in case this login expires."})] : []).concat(
    M[svc].map((m) => h("details", {open: !!(check && check.state !== "ok" && m.available && m.id !== "cli" && m === M[svc].find((x) => x.available && x.id !== "cli")), class: m.available ? "" : "off"}, h("summary", {text: m.label + (m.available ? "" : " (not available here)")}), h("div", {class: "body"}, methodBody(svc, m, t)))));
  nodes.push(svcCard("Kaggle", ka, methods("kaggle", "kaggle", ka)));
  nodes.push(svcCard("Colab (Google)", co, methods("colab", "colab", co)));
  const cfs = ["kaggle", "colab"].map((t) => [t, find(t, /^cloudflare$/)]);
  const cfBody = [];
  for (const [t, c] of cfs) {
    cfBody.push(h("div", {class: "checkrow"}, h("strong", {text: NAMES[t] + " tunnel"}), c ? stateBadge(c.state) : h("span", {class: "chip s-unknown", text: "Checking"}), c && c.detail ? h("span", {class: "muted small", text: c.detail}) : null));
    if (c && c.state !== "ok" && c.state !== "unknown") cfBody.push(...cfForms(t, M.cloudflare));
  }
  const allOk = cfs.every(([, c]) => c && c.state === "ok");
  nodes.push(h("article", {class: "card svc"}, h("div", {class: "card-head"}, h("h3", {text: "Cloudflare tunnel"}), allOk ? stateBadge("ok") : null), cfBody));
  box.replaceChildren(...nodes);
}
async function loadChecks(force) {
  try { CHECKS = await api("/api/checks" + (force ? "?refresh=1" : "")); renderLogins(); } catch (e) { /* shown as checking */ }
}
// logins refresh with the global Refresh button

// ------------------------------------------------------------------ start
async function start() {
  if (!token) { signOut(""); return; }
  $("login").hidden = true; $("app").hidden = false;
  renderLogins(); loadLog.called = false;
  const p = await loadDash(false);
  loadQuotas(false); loadChecks(false);
  schedule(p ? 1500 : 20000);
  try { const d = DASH; for (const t of Object.keys(NAMES)) if (d && d.targets[t].busy && !activeJob) { /* an action started elsewhere: nothing to follow without its id */ } } catch (e) { /* ignore */ }
}
start();
