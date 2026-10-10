/* Costs (WP18): today's spending in the top bar, a warning at 80 % of the
   daily cap, the confirmation before a paid conversation once the cap is
   reached, and the last 30 days in Réglages › Coûts.
   - The voice: voice.js emits 'usage' for every response.done (and every
     transcription of what monsieur said). The amounts add up here and go to
     the server every 30 s and when the conversation ends; meanwhile the chip
     prices them itself with the server's table, so it moves at once.
   - Claude: what the tasks report, Claude Code's own estimate at API prices
     (the chip's tooltip says so).
   - Siri (B3): its text model, priced on the server; a day where Siri spoke
     carries text_usd, shown apart in the tooltip and in Réglages › Coûts.
   - Past the cap (/api/config usage_capped): the wake word opens nothing
     (wake.js), and any other way of opening a conversation (orb, Espace, the
     composer, the hotkey) asks here first.
   - Server figures are numbers; every word is set as text, and the chart's
     labels are neutralised as report.js does. */
import { $, TOKEN, api, bus, settings, state } from "./core.js";
import { T, fmtNumber } from "./strings-fr.js";
import { addCard, removeCard } from "./hud.js";
import { label as neutral, loadApexCharts } from "./report.js";

const U = T.usage;
const S = T.siri;
const FLUSH_MS = 30e3;          // the voice's usage goes to the server at most this late
const REFRESH_MS = 10 * 60e3;   // the day turns at midnight: the server's figures follow
const NBSP = "\u00a0";
const COLORS = ["#1e93c4", "#c47b1e", "#9b7be0"];  // report.js's series palette, plus Siri's (dark surfaces)
const KEEPALIVE_BYTES = 60000;  // keepalive bodies are capped at 64 KiB per page

let server = null;              // GET /api/usage, or a POST's answer: today, budget, prices...
const pending = new Map();      // the voice's usage not posted yet: key -> {model, usage}
let posting = null;             // the post on its way
const inflight = new Set();     // entries of that post the server hasn't counted yet
let asking = null;              // the cap's confirmation on screen: one at a time
let dialog = null;
let chart = null, chartSeq = 0;
let usageEl = null;             // Réglages › Coûts' host (settings.js), while it is shown

/* ---------------------------------------------------------- amounts */
export const money = (usd) => `${fmtNumber(Number(usd) || 0, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${NBSP}$`;
const num = (v) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : 0);
const obj = (v) => (v && typeof v === "object" && !Array.isArray(v) ? v : {});

/* Adds a usage object into another, field by field (nested details included). */
function addInto(into, src) {
  for (const [k, v] of Object.entries(obj(src))) {
    if (k === "type") continue;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      if (!into[k] || typeof into[k] !== "object") into[k] = {};
      addInto(into[k], v);
    } else if (typeof v === "number") {
      into[k] = num(into[k]) + num(v);
    }
  }
  return into;
}

/* The same pricing as usage.py (the server's figure replaces it at each post):
   the model's prices, a dated snapshot's, else the dearest model's. */
function pricesFor(model) {
  const table = obj(server && server.prices);
  const name = String(model || "") || String((server && server.model) || "");
  const snap = /^(.+)-\d{4}-\d{2}-\d{2}$/.exec(name);
  return obj(table[snap ? snap[1] : name] || table[server && server.default_model]);
}

function realtimeCost(u, model) {
  const p = pricesFor(model);
  const din = obj(u.input_token_details), dout = obj(u.output_token_details), dc = obj(din.cached_tokens_details);
  let usd = 0, explained = 0;
  for (const kind of ["text", "audio", "image"]) {
    const seen = num(din[`${kind}_tokens`]);
    const cached = Math.min(num(dc[`${kind}_tokens`]), seen);
    usd += (seen - cached) * num(p[`${kind}_in`]) + cached * num(p[`${kind}_cached`]);
    explained += seen;
  }
  usd += Math.max(0, num(u.input_tokens) - explained) * num(p.text_in);
  const audioOut = num(dout.audio_tokens), textOut = num(dout.text_tokens);
  usd += audioOut * num(p.audio_out);
  usd += (textOut + Math.max(0, num(u.output_tokens) - audioOut - textOut)) * num(p.text_out);
  return usd / 1e6;
}

function transcriptionCost(u) {
  const t = obj(server && server.transcribe);
  const perMin = num(t.per_minute);
  if (u.type === "duration") return num(u.seconds) / 60 * perMin;
  const rates = obj(t.per_1m);
  if ("in" in rates) return (num(u.input_tokens) * num(rates.in) + num(u.output_tokens) * num(rates.out)) / 1e6;
  const audio = num(obj(u.input_token_details).audio_tokens) || num(u.input_tokens);
  return audio / 10 / 60 * perMin;  // 1 token per 100 ms of his voice
}

function pendingCost() {
  let usd = 0;
  for (const e of [...pending.values(), ...inflight]) {
    usd += e.usage.type ? transcriptionCost(e.usage) : realtimeCost(e.usage, e.model);
  }
  return usd;
}

function totals() {
  const t = obj(server && server.today);
  const voice = num(t.realtime_usd) + pendingCost(), claude = num(t.claude_usd), text = num(t.text_usd);
  return { voice, claude, text, total: voice + claude + text };
}

/* Did Siri spend anything over these days? (Its column and series only then.) */
const siriSpent = (days) => days.some(d => num(d.text_usd) > 0);

/* 'ok', 'warn' (80 % of the cap) or 'cap'. */
function level(tot = totals()) {
  const cap = num(server && server.budget);
  if (!cap) return "ok";
  if (server.capped || tot.total >= cap) return "cap";
  return tot.total >= cap * (num(server.warn_ratio) || 0.8) ? "warn" : "ok";
}

/* ---------------------------------------------------------- the chip and the warnings */
function openCosts() { bus.emit("settings:open", { section: "couts" }); }

function renderChip(tot, lv) {
  const chip = $("costChip");
  if (!chip) return;
  if (!server) { chip.replaceChildren(); chip.hidden = true; return; }
  const cap = num(server.budget);
  let text = U.chip(money(tot.total));
  if (lv === "cap") text += U.chipCapped;
  else if (lv === "warn") text += U.chipWarn(Math.floor(tot.total / cap * 100));
  let b = chip.querySelector("button");
  if (!b) {
    b = document.createElement("button");
    b.type = "button";
    b.className = "ctl chip-btn";
    b.addEventListener("click", openCosts);
    chip.replaceChildren(b);
  }
  b.textContent = text;
  // Read by screen readers as the button's description; a mouse shows it as a tooltip.
  b.title = U.tooltip(money(tot.voice), money(tot.claude)) + (tot.text ? S.costTooltip(money(tot.text)) : "")
    + (cap ? U.tooltipCap(money(cap)) : "");
  chip.className = `chip cost-chip${lv === "ok" ? "" : ` ${lv}`}`;
  chip.hidden = false;
}

/* Each level is announced once a day; the card's figures follow while it is on screen. */
function renderAlerts(tot, lv) {
  if (lv !== "warn") removeCard("usage-warn");
  if (lv !== "cap") removeCard("usage-cap");
  if (lv === "ok" || !server) return;
  const id = lv === "cap" ? "usage-cap" : "usage-warn";
  const key = `${(server.today && server.today.date) || ""}:${lv}`;
  const shown = !!document.getElementById(`card-${id}`);
  if (!shown && settings.get("usageAlert", "") === key) return;
  settings.set("usageAlert", key);
  const cap = num(server.budget);
  const text = lv === "cap" ? U.capText(money(tot.total), money(cap), tot.claude > 0 ? money(tot.claude) : "")
    : U.warnText(Math.floor(tot.total / cap * 100), money(tot.total), money(cap));
  addCard(lv === "cap" ? U.capTitle : U.warnTitle, text, "warning",
          { id, actions: [{ label: U.openSettings, onClick: openCosts }] });
}

function render() {
  const tot = totals(), lv = level(tot);
  renderChip(tot, lv);
  renderAlerts(tot, lv);
  return lv;
}

/* Today's figures into the 30 days already known (a post or a push brings only today). */
function withToday(today) {
  const days = Array.isArray(server && server.days) ? server.days.slice() : [];
  const last = days[days.length - 1];
  if (last && last.date === today.date) days[days.length - 1] = today;
  else if (last && last.date < today.date) { days.push(today); days.shift(); }  // the day turned
  return days;
}

/* What the server says: its figures, and the cap the wake word obeys (only
   when the server's answer changes: the page may know better meanwhile). */
function apply(data) {
  if (!data || typeof data !== "object" || !data.today || typeof data.today !== "object") return;
  const full = Array.isArray(data.days) && data.days.length > 1;
  const was = server ? !!server.capped : null;
  server = { ...(server || {}), ...data, days: full ? data.days : withToday(data.today) };
  if (was !== !!server.capped) state.config.usage_capped = !!server.capped;
  render();
  if (full && usageEl && usageEl.isConnected) drawCosts(usageEl);
}

export async function refresh() {
  try {
    apply(await api("/api/usage?days=30"));
  } catch (err) {
    console.warn(err);
  }
  return server;
}

/* ---------------------------------------------------------- the voice's usage */
function onUsage(d) {
  if (!d || !d.usage || typeof d.usage !== "object") return;
  const transcription = d.kind === "transcription" || typeof d.usage.type === "string";
  const type = d.usage.type === "duration" ? "duration" : "tokens";
  const key = transcription ? `tx:${type}` : `rt:${String(d.model || "")}`;
  const entry = pending.get(key) || { model: transcription ? "" : String(d.model || ""),
                                      usage: transcription ? { type } : {} };
  const before = level();
  addInto(entry.usage, d.usage);
  pending.set(key, entry);
  // Reaching 80 % or the cap: the server (and the wake word) know at once.
  if (render() !== before) flush();
}

/* Posts what the voice used; kept for the next try if the server can't take it. */
export function flush() {
  if (posting || !pending.size) return posting || Promise.resolve();
  const batch = [...pending.entries()];
  pending.clear();
  batch.forEach(([, entry]) => inflight.add(entry));  // still in the chip while on its way
  posting = (async () => {
    for (const [key, entry] of batch) {
      try {
        const data = await api("/api/usage", { method: "POST", body: { usage: entry.usage, model: entry.model } });
        inflight.delete(entry);
        apply(data);
      } catch (err) {
        inflight.delete(entry);
        if (err && err.status === 400) { console.warn(err); continue; }  // refused: never resent
        const now = pending.get(key);  // used meanwhile: added to what goes next time
        if (now) addInto(entry.usage, now.usage);
        pending.set(key, entry);
      }
    }
  })().finally(() => { posting = null; render(); });
  return posting;
}

/* The page goes away: what is left leaves with it (fetch keepalive survives the unload). */
function flushOnExit() {
  for (const entry of pending.values()) {
    const body = JSON.stringify({ usage: entry.usage, model: entry.model });
    if (body.length > KEEPALIVE_BYTES) continue;
    try {
      fetch("/api/usage", { method: "POST", keepalive: true, body,
                            headers: { "X-Jarvis-Token": TOKEN, "Content-Type": "application/json" } });
    } catch { /* the page is closing anyway */ }
  }
  pending.clear();
}

/* ---------------------------------------------------------- past the cap */
function buildDialog() {
  dialog = document.createElement("dialog");
  dialog.id = "capDialog";
  dialog.className = "cap-dialog";
  dialog.setAttribute("aria-labelledby", "capTitle");
  dialog.setAttribute("aria-describedby", "capText");
  const form = document.createElement("form");
  form.method = "dialog";
  const h = document.createElement("h2");
  h.id = "capTitle";
  h.textContent = U.askTitle;
  const p = document.createElement("p");
  p.id = "capText";
  const hint = document.createElement("p");
  hint.className = "cap-hint";
  hint.textContent = U.askHint;
  const actions = document.createElement("div");
  actions.className = "cap-actions";
  for (const [value, text, cls] of [["cancel", U.askCancel, "ctl"], ["ok", U.askOk, "ctl primary"]]) {
    const b = document.createElement("button");
    b.type = "submit";
    b.value = value;
    b.className = cls;
    b.textContent = text;
    actions.append(b);
  }
  form.append(h, p, hint, actions);
  dialog.append(form);
  document.body.append(dialog);
  return dialog;
}

/* true: open the paid conversation anyway; false: leave it closed. */
export function askOverCap() {
  if (asking && dialog.open) return asking;
  // Closed but not settled yet (the hotkey closes every dialog, then opens a
  // conversation): this new request is asked anew.
  if (asking) return asking.then(() => askOverCap());
  const d = dialog || buildDialog();
  const tot = totals();
  d.querySelector("#capText").textContent = server && num(server.budget)
    ? U.askText(money(tot.total), money(server.budget)) : U.askGeneric;
  d.returnValue = "";
  asking = new Promise((resolve) => {
    d.addEventListener("close", () => { asking = null; resolve(d.returnValue === "ok"); }, { once: true });
    d.showModal();
    d.querySelector('button[value="cancel"]').focus();  // a paid action is never one Entrée away
  });
  return asking;
}

function onConnectIntercept(ev) {
  if (state.config.usage_capped && ev && typeof ev.hold === "function") ev.hold(askOverCap());
}

/* ---------------------------------------------------------- Réglages › Coûts */
const dayFormat = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
/* '2026-10-10' -> '10 oct.' (anything else comes back as it was, as text). */
export function dayLabel(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso ?? ""));
  return m ? dayFormat.format(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))) : String(iso ?? "");
}

function el(tag, cls = "", text = "") {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

function destroyChart() {
  if (!chart) return;
  try { chart.destroy(); } catch { /* already gone */ }
  chart = null;
}

function reducedMotion() {
  try {
    return matchMedia("(prefers-reduced-motion: reduce)").matches || settings.get("motion") === "reduced";
  } catch { return false; }
}

function cssVar(name, fallback) {
  try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback; } catch { return fallback; }
}

/* The options of the cost chart (exported for the tests). */
export function chartOptions(days) {
  const fmt = (v) => (typeof v === "number" ? money(v) : "");
  const categories = days.map(d => neutral(dayLabel(d.date)));
  // 30 dates side by side don't fit: one in five is written (today's included), the tooltip says each.
  const written = new Set(categories.filter((_, i) => (categories.length - 1 - i) % 5 === 0));
  return {
    chart: {
      type: "bar", stacked: true, height: 220, background: "transparent", toolbar: { show: false },
      foreColor: cssVar("--muted", "#7aa7c2"), fontFamily: "Rajdhani, sans-serif",
      animations: { enabled: !reducedMotion() },
      // An image named in French; the figures are in the table below it.
      accessibility: { description: neutral(U.chartLabel), keyboard: { enabled: false } },
    },
    theme: { mode: "dark" },
    colors: COLORS,
    plotOptions: { bar: { columnWidth: "70%" } },
    grid: { borderColor: "rgba(64,220,255,.12)", strokeDashArray: 3 },
    dataLabels: { enabled: false },
    legend: { show: true, labels: { colors: cssVar("--text", "#cfeaff") } },
    tooltip: { theme: "dark", shared: true, intersect: false, x: { formatter: (v) => String(v ?? "") },
               y: { formatter: fmt } },
    series: [{ name: neutral(U.voice), data: days.map(d => num(d.realtime_usd)) },
             { name: neutral(U.claude), data: days.map(d => num(d.claude_usd)) },
             ...(siriSpent(days) ? [{ name: neutral(S.costSeries), data: days.map(d => num(d.text_usd)) }] : [])],
    xaxis: { categories, labels: { rotate: 0, formatter: (v) => (written.has(v) ? v : "") },
             axisTicks: { show: false } },
    yaxis: { labels: { formatter: fmt } },
  };
}

function costTable(days) {
  const spent = days.filter(d => num(d.total_usd) > 0).reverse();
  const details = el("details", "usage-details");
  details.append(el("summary", "", U.table));
  if (!spent.length) {
    details.append(el("p", "set-note", U.nothing));
    return details;
  }
  const table = el("table", "usage-table");
  const head = el("tr");
  const siri = siriSpent(spent);
  for (const c of [U.colDate, U.colVoice, U.colClaude, ...(siri ? [S.costColumn] : []), U.colTotal]) {
    const th = el("th", "", c);
    th.scope = "col";
    head.append(th);
  }
  table.append(el("thead"), el("tbody"));
  table.tHead.append(head);
  for (const d of spent) {
    const tr = el("tr");
    const th = el("th", "", dayLabel(d.date));
    th.scope = "row";
    tr.append(th, el("td", "num", money(d.realtime_usd)), el("td", "num", money(d.claude_usd)));
    if (siri) tr.append(el("td", "num", money(d.text_usd)));
    tr.append(el("td", "num", money(d.total_usd)));
    table.tBodies[0].append(tr);
  }
  details.append(table);
  return details;
}

async function drawCosts(host) {
  const seq = ++chartSeq;
  destroyChart();
  const days = Array.isArray(server && server.days) ? server.days : [];
  const tot = totals();
  const cap = num(server && server.budget);
  const sum = days.reduce((n, d) => n + num(d.total_usd), 0);
  const box = el("div", "usage-chart");
  box.id = "usageChart";
  host.replaceChildren(
    el("h4", "usage-title", U.title),
    el("p", "usage-today", U.today(money(tot.total), money(tot.voice), money(tot.claude))
      + (tot.text ? S.costToday(money(tot.text)) : "")),
    el("p", "usage-period", U.period(money(sum), money(days.length ? sum / days.length : 0))),
    el("p", "usage-cap", cap ? U.capSet(money(cap)) : U.capNone),
    box, costTable(days), el("p", "set-note usage-note", U.note));
  if (!days.length) return;
  box.dataset.state = "loading";
  box.setAttribute("aria-busy", "true");
  box.append(el("p", "usage-chart-note", U.chartLoading));
  const Apex = await loadApexCharts();
  if (seq !== chartSeq || !box.isConnected) return;
  box.setAttribute("aria-busy", "false");
  if (!Apex) {
    box.dataset.state = "unavailable";
    box.replaceChildren(el("p", "usage-chart-note", U.chartUnavailable));
    return;
  }
  box.replaceChildren();
  try {
    chart = new Apex(box, chartOptions(days));
    await chart.render();
    if (seq === chartSeq) box.dataset.state = "ready";
  } catch (err) {
    console.warn(err);
    destroyChart();
    box.dataset.state = "unavailable";
    box.replaceChildren(el("p", "usage-chart-note", U.chartUnavailable));
  }
}

/* Fresh figures each time the section opens (apply() draws them). */
async function showCosts(host) {
  usageEl = host;
  const before = server;
  await refresh();
  if (usageEl !== host || !host.isConnected) return;
  if (!server) host.replaceChildren(el("p", "set-status err", U.loadFailed));
  else if (server === before) drawCosts(host);  // not reachable just now: what is known
}

/* ---------------------------------------------------------- start */
export function init() {
  bus.on("usage", onUsage);
  bus.on("connect:intercept", onConnectIntercept);
  bus.on("server:usage", (ev) => {
    if (!ev || !ev.today) return;
    apply({ today: ev.today, budget: ev.budget, capped: ev.capped });
  });
  bus.on("server:config", refresh);  // the cap changed in Réglages
  bus.on("sse:open", refresh);       // back after a disconnection
  bus.on("settings:section", ({ id, el: host } = {}) => {
    if (id === "couts" && host) showCosts(host);
  });
  bus.on("ui:close", (name) => {
    if (name === "settings") { usageEl = null; chartSeq++; destroyChart(); }
  });
  // The conversation ended: its usage goes now, not in 30 s.
  bus.on("mode", ({ mode } = {}) => { if (mode === "standby" || mode === "off") flush(); });
  window.addEventListener("pagehide", flushOnExit);
  setInterval(flush, FLUSH_MS);
  setInterval(refresh, REFRESH_MS);
  refresh();  // not awaited: the page never waits for its figures
}
