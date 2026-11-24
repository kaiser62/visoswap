"use strict";
const TARGETS = {kaggle: "Kaggle (T4 x2)", colab: "Colab (L4 default, T4 optional)"};
let token = null; try { token = localStorage.getItem("vs_token"); } catch (e) {}
let activeJob = null, since = 0, timer = null, pending = null, activeAct = null, activeTarget = null;
const $ = (id) => document.getElementById(id);
async function api(path, opts = {}) {
  const r = await fetch(path, {...opts, headers: {"Authorization": "Bearer " + token, "Content-Type": "application/json"}});
  if (r.status === 401) { signOut("Invalid token."); throw new Error("auth"); }
  if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch (e) {} throw new Error(d || r.status); }
  return r.json();
}
function signOut(msg) { token = null; try { localStorage.removeItem("vs_token"); } catch (e) {} $("login").hidden = false; $("app").hidden = true; $("lerr").textContent = msg || ""; }
$("lock").onclick = () => signOut("");
$("lf").onsubmit = (e) => { e.preventDefault(); token = $("tok").value.trim(); $("tok").value = ""; try { localStorage.setItem("vs_token", token); } catch (e) {} start(); };
function cls(s) { return s.healthy ? "ok" : (s.state === "BUSY" || s.state === "QUEUED" ? "warn" : (s.ok === false ? "bad" : "")); }
function card(t, s) {
  const el = document.createElement("div"); el.className = "card";
  const h = document.createElement("div"); h.className = "row";
  const b = document.createElement("b"); b.textContent = TARGETS[t]; h.append(b);
  const p = document.createElement("span"); p.className = "pill " + cls(s); p.textContent = s.state || (s.ok === false ? "error" : "?"); h.append(p);
  el.append(h);
  const d = document.createElement("div"); d.textContent = s.error || s.summary || ""; el.append(d);
  if (s.next) { const n = document.createElement("small"); n.textContent = "Next: " + s.next; el.append(n); }
  if (s.url) {
    const r = document.createElement("div"); r.className = "row";
    const a = document.createElement("a"); a.href = s.url + "/mobile"; a.textContent = s.url; a.target = "_blank"; a.rel = "noopener"; r.append(a);
    const c = document.createElement("button"); c.className = "ghost"; c.textContent = "Copy URL";
    c.onclick = async () => { try { await navigator.clipboard.writeText(s.url); c.textContent = "Copied"; } catch (e) { c.textContent = s.url; } };
    r.append(c); el.append(r);
  }
  const bar = document.createElement("div"); bar.className = "row";
  for (const [act, label, danger] of [["launch", "Launch"], ["restart", "Restart"], ["provision", "Provision", 1], ["stop", "Stop", 1]]) {
    const x = document.createElement("button"); x.textContent = label; x.disabled = !!s.busy;
    if (act === "stop") x.className = "danger"; else if (act === "provision") x.className = "ghost";
    x.onclick = () => run(t, act); bar.append(x);
  }
  el.append(bar); el.append(checksPanel(t)); return el;
}
let CHECKS = null;
function secretForm(placeholder, button, onSubmit) {
  const f = document.createElement("form"); f.className = "row";
  const i = document.createElement("input"); i.type = "password"; i.autocomplete = "off"; i.placeholder = placeholder; f.append(i);
  const b = document.createElement("button"); b.textContent = button; f.append(b);
  f.onsubmit = async (e) => { e.preventDefault(); const v = i.value; i.value = ""; if (v) await onSubmit(v); };
  return f;
}
function checksPanel(t) {
  const box = document.createElement("div"); box.className = "checks";
  const info = CHECKS && CHECKS.targets[t];
  if (!info) { box.textContent = "Checking logins..."; return box; }
  const canLogin = CHECKS.login_ui && CHECKS.login_ui[t];
  const note = CHECKS.login_note && CHECKS.login_note[t];
  for (const c of info.checks || []) {
    const row = document.createElement("div"); row.className = "row";
    const dot = document.createElement("span"); dot.className = "pill " + (c.state === "ok" ? "ok" : (c.state === "unknown" || !c.blocking ? "warn" : "bad")); dot.textContent = c.state; row.append(dot);
    const l = document.createElement("span"); l.textContent = c.service + (c.detail ? ": " + c.detail : ""); row.append(l);
    if (c.state !== "ok" && c.state !== "unknown") {
      if (c.id === "cloudflare") {
        row.append(secretForm("tunnel token (stored mode 600, never shown)", "Save token", async (v) => {
          try { const r = await api(`/api/targets/${t}/cloudflare-token`, {method: "POST", body: JSON.stringify({token: v})}); $("log").textContent = r.ok ? "Token stored." : "Not stored: " + r.error; loadChecks(true); }
          catch (er) { $("log").textContent = "Refused: " + er.message; } }));
        row.append(secretForm("or a Cloudflare API token: creates tunnel + DNS (used once, not stored)", "Create tunnel", async (v) => {
          $("log").textContent = "Creating the tunnel and DNS record...";
          try { const r = await api(`/api/targets/${t}/cloudflare-api-token`, {method: "POST", body: JSON.stringify({token: v})}); $("log").textContent = r.ok ? (r.summary || "Tunnel ready.") : "Failed: " + r.error; loadChecks(true); continueAfterLogin(t); }
          catch (er) { $("log").textContent = "Refused: " + er.message; } }));
      } else if (c.id.endsWith(".auth") || c.id.endsWith(".login")) {
        if (canLogin) {
          const b = document.createElement("button"); b.textContent = t === "kaggle" ? "Log in (paste token)" : "Log in with Google"; b.onclick = () => run(t, "login"); row.append(b);
        } else if (note) { const n = document.createElement("small"); n.textContent = note; row.append(n); }
        else if (c.fix) { const cmd = document.createElement("code"); cmd.textContent = "In a terminal: " + c.fix; row.append(cmd); }
      } else if (c.fix) { const cmd = document.createElement("code"); cmd.textContent = "In a terminal: " + c.fix; row.append(cmd); }
    }
    box.append(row);
  }
  return box;
}
async function loadChecks(force) {
  try { CHECKS = await api("/api/checks" + (force ? "?refresh=1" : "")); refresh(false, true); } catch (e) {}
}
// after a successful login / tunnel setup, re-run the action that was blocked
function continueAfterLogin(t) { if (pending && pending.t === t && !activeJob) { const p = pending; pending = null; $("log").textContent += "\nContinuing: " + p.act + "\n"; run(p.t, p.act, true); } }
async function run(t, act, auto) {
  let body = {};
  if (act === "stop" && !auto) { if (!confirm("Stop " + t + "? The backend goes offline" + (t === "colab" ? " and a new T4 may be hard to get." : "."))) return; body = {confirm: true}; }
  try { const r = await api(`/api/targets/${t}/${act}`, {method: "POST", body: JSON.stringify(body)}); follow(r.job, t + " " + act, t, act); }
  catch (e) { $("log").textContent = "Refused: " + e.message; }
}
function follow(job, label, t, act) { activeJob = job; activeTarget = t || null; activeAct = act || null; since = 0; hidePrompt(); $("log").textContent = ""; $("logfor").textContent = label; poll(); }
async function poll() {
  if (!activeJob) return;
  try {
    const j = await api(`/api/jobs/${activeJob}?since=${since}`);
    if (j.lines.length) { $("log").textContent += j.lines.join("\n") + "\n"; $("log").scrollTop = 1e9; }
    since = j.next;
    if (j.state === "waiting" && j.prompt) { showPrompt(j.prompt); setTimeout(poll, 2000); return; }
    hidePrompt();
    if (j.state === "running") { setTimeout(poll, 1500); return; }
    const r = j.result || {}; $("log").textContent += (r.ok ? "DONE" : "FAILED: " + (r.error || "")) + (r.next ? "\nNext: " + r.next : "") + "\n";
    const t = activeTarget, act = activeAct; activeJob = null;
    if (!r.ok && Array.isArray(r.checks) && r.checks.length && t) {   // the launch stopped on a missing login: show it and remember the action
      CHECKS = CHECKS || {targets: {}, login_ui: {}, login_note: {}}; CHECKS.targets[t] = {checks: r.checks};
      if (act !== "login") pending = {t, act};
    }
    if (r.ok && act === "login") { await refresh(true); await loadChecks(true); continueAfterLogin(t); return; }
    if (r.ok && act !== "login") pending = null;
    refresh(true); if (!(r.checks && r.checks.length)) loadChecks(true);
  } catch (e) { setTimeout(poll, 4000); }
}
function hidePrompt() { const b = $("prompt"); if (b) { b.hidden = true; b.replaceChildren(); } }
function showPrompt(p) {
  const b = $("prompt"); if (!b.hidden && b.dataset.msg === p.msg) return;
  b.hidden = false; b.dataset.msg = p.msg; b.replaceChildren();
  const m = document.createElement("div"); m.textContent = p.msg; b.append(m);
  if (p.url) { const a = document.createElement("a"); a.href = p.url; a.textContent = "Open Google sign-in"; a.target = "_blank"; a.rel = "noopener noreferrer"; b.append(a); }
  b.append(secretForm(p.kind === "code" ? "paste the code here" : "paste the token here (hidden, never stored in the browser)", "Submit", async (v) => {
    try { await api(`/api/jobs/${activeJob}/input`, {method: "POST", body: JSON.stringify({value: v})}); hidePrompt(); }
    catch (e) { $("log").textContent += "Not accepted: " + e.message + "\n"; } }));
  const c = document.createElement("button"); c.className = "ghost"; c.textContent = "Cancel";
  c.onclick = async () => { try { await api(`/api/jobs/${activeJob}/cancel`, {method: "POST", body: "{}"}); } catch (e) {} hidePrompt(); }; b.append(c);
}
async function refresh(force, keep) {
  try {
    const all = await api("/api/targets" + (force ? "?refresh=1" : "")); const box = $("cards"); box.replaceChildren();
    for (const t of Object.keys(TARGETS)) { box.append(card(t, all[t] || {})); if (!activeJob && all[t] && all[t].job) follow(all[t].job, t); }
  } catch (e) { if (e.message !== "auth") $("cards").textContent = "Cannot reach the launcher service: " + e.message; }
}
function start() { if (!token) { signOut(""); return; } $("login").hidden = true; $("app").hidden = false; refresh(false); loadChecks(false); clearInterval(timer); timer = setInterval(() => { if (!activeJob) refresh(false); }, 30000); }
start();
