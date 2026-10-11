/* Journal (WP14): the conversation, kept on this PC.
   - Every finished turn (bus 'turn': transcription completed, answer done,
     typed text) goes to the server in small batches, 2 s after the last one.
     When the page goes away, what is left leaves with fetch(keepalive), which
     outlives the page and, unlike sendBeacon, carries the token header.
   - The 'Journal' drawer (top bar) shows a day, or searches every day kept
     (case and accents ignored). It is a non-modal drawer: Échap closes it
     (keys.js) and the focus goes back to the button that opened it.
   - On load, the last half hour of today's journal refills state.history, so
     a reload doesn't lose the thread of the conversation (voice.js passes it
     to the next session).
   - A fact forgotten by voice can be brought back from a toast for 7 days
     on the server (POST /api/undo/{id}).
   Everything shown is text (textContent): nothing in the journal is markup. */
import { $, TOKEN, api, bus, state } from "./core.js";
import { T, fmtTime } from "./strings-fr.js";
import { announce, toast } from "./hud.js";
import { isLive, sendNotice } from "./voice.js";

const J = T.journal;  // strings-fr.js

const DEBOUNCE_MS = 2000;
const MAX_WAIT_MS = 10000;        // a long conversation still reaches the disk regularly
const RECENT_MS = 30 * 60e3;      // what recentContext carries into a new session (voice.js)
const KEEPALIVE_BYTES = 60000;    // keepalive bodies are capped at 64 KiB per page
const SEARCH_MS = 250;
const DAYS = J.days, MONTHS = J.months;  // strings-fr.js

let drawer, logEl, listEl, daySel, searchEl, statusEl, footEl, keepEl;
let pending = [];                 // turns not on the server yet
let flushTimer = null, firstPending = 0, retryDelay = 0;
let opener = null;
let view = { day: "", q: "" };
let info = { enabled: true, keep_days: 30, days: [] };
let searchTimer = null, reloadTimer = null, loadSeq = 0;

/* ---------------------------------------------------------- dates */
function isoDay(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function dayLabel(iso) {
  const today = isoDay();
  if (iso === today) return J.today;
  const y = new Date();
  y.setDate(y.getDate() - 1);
  if (iso === isoDay(y)) return J.yesterday;
  const [yy, mm, dd] = iso.split("-").map(Number);
  const d = new Date(yy, mm - 1, dd);
  return `${DAYS[d.getDay()]} ${dd} ${MONTHS[mm - 1]}` + (yy !== new Date().getFullYear() ? ` ${yy}` : "");
}

/* ---------------------------------------------------------- sending turns */
const encoder = new TextEncoder();

function queueTurn({ role, text, at, source } = {}) {
  text = String(text || "").trim().slice(0, 4000);  // what the server keeps
  if (!text || !["user", "jarvis"].includes(role)) return;
  const entry = { ts: Number(at) || Date.now(), role, text, source: source || "voice" };
  pending.push(entry);
  if (pending.length > 500) pending.splice(0, pending.length - 500);
  if (!firstPending) firstPending = Date.now();
  if (!retryDelay) {
    clearTimeout(flushTimer);
    const wait = Math.max(0, Math.min(DEBOUNCE_MS, firstPending + MAX_WAIT_MS - Date.now()));
    flushTimer = setTimeout(flush, wait);
  }
  if (isOpen() && !view.q && view.day === isoDay()) appendRow(entry, true);
}

/* The oldest pending turns that fit in one keepalive request (64 KiB a page). */
function take() {
  let size = 20, n = 0;
  for (const e of pending) {
    const one = encoder.encode(JSON.stringify(e)).length + 1;
    if (n && size + one > KEEPALIVE_BYTES) break;
    size += one;
    n++;
  }
  return pending.splice(0, n);
}

/* keepalive on every batch: one still on its way when the page goes is not cut off. */
function post(entries) {
  return fetch("/api/journal", {
    method: "POST", keepalive: true,
    headers: { "X-Jarvis-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ entries }),
  }).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); });
}

async function flush() {
  clearTimeout(flushTimer);
  firstPending = 0;
  if (!pending.length) return;
  const batch = take();
  try {
    await post(batch);
    retryDelay = 0;
  } catch (err) {
    pending.unshift(...batch);  // tried again later, or on leaving
    retryDelay = Math.min((retryDelay || DEBOUNCE_MS) * 2, 60000);  // the server is away: no hammering
    console.warn(err);
  }
  if (pending.length) flushTimer = setTimeout(flush, retryDelay || DEBOUNCE_MS);
}

/* The page goes (reload, close): keepalive lets the request finish after it. */
function flushOnLeave() {
  if (!pending.length) return;
  clearTimeout(flushTimer);
  try {
    post(take()).catch(() => {});
  } catch { /* the page is going anyway */ }
}

/* ---------------------------------------------------------- state.history after a reload */
function refill(entries) {
  const since = Date.now() - RECENT_MS;
  const firstAt = state.history.length ? Math.min(...state.history.map(h => h.at || Date.now())) : Infinity;
  const older = entries
    .filter(e => (e.role === "user" || e.role === "jarvis") && e.ts * 1000 >= since && e.ts * 1000 < firstAt)
    .map(e => ({ role: e.role === "user" ? "monsieur" : "JARVIS", text: String(e.text).slice(0, 400), at: e.ts * 1000 }));
  if (!older.length) return;
  state.history.unshift(...older);
  if (state.history.length > 24) state.history.splice(0, state.history.length - 24);
}

/* ---------------------------------------------------------- the drawer */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function button(label, onClick, cls = "ctl") {
  const b = el("button", cls, label);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function build() {
  drawer.setAttribute("aria-labelledby", "journalTitle");
  drawer.removeAttribute("aria-label");
  const head = el("header", "jr-head");
  const h2 = el("h2", "", J.title);
  h2.id = "journalTitle";
  const x = button("✕", () => close(), "x");
  x.setAttribute("aria-label", J.close);
  head.append(h2, x);

  const tools = el("div", "jr-tools");
  const dayLabelEl = el("label", "visually-hidden", J.day);
  dayLabelEl.htmlFor = "journalDay";
  daySel = el("select", "jr-day");
  daySel.id = "journalDay";
  daySel.addEventListener("change", () => { view.day = daySel.value; view.q = ""; searchEl.value = ""; load(); });
  searchEl = el("input", "jr-search");
  searchEl.id = "journalSearch";
  searchEl.type = "search";
  searchEl.placeholder = J.searchHint;
  searchEl.autocomplete = "off";
  searchEl.setAttribute("aria-label", J.search);
  searchEl.setAttribute("aria-controls", "journalLog");
  searchEl.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { view.q = searchEl.value.trim(); load(); }, SEARCH_MS);
  });
  searchEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(searchTimer); view.q = searchEl.value.trim(); load(); }
  });
  tools.append(dayLabelEl, daySel, searchEl);

  statusEl = el("p", "jr-status");
  statusEl.id = "journalStatus";
  statusEl.setAttribute("role", "status");

  // Spec §9: a polite log, read out as lines are added (finished turns only).
  // The role sits on a wrapper: on the <ol> itself it would hide the list.
  logEl = el("div", "jr-log");
  logEl.id = "journalLog";
  listEl = el("ol", "jr-list");
  logEl.append(listEl);
  logEl.setAttribute("role", "log");
  logEl.setAttribute("aria-live", "polite");
  logEl.setAttribute("aria-relevant", "additions");
  logEl.setAttribute("aria-label", J.log);
  logEl.tabIndex = 0;  // a scrolling region must be reachable by keyboard

  footEl = el("footer", "jr-foot");
  keepEl = el("span", "meta jr-keep");
  // Emptying the journal is the PC's (DELETE /api/journal is PC-only): never offered on the phone.
  footEl.append(keepEl, ...(state.remote ? [] : [button(J.clear, askClear, "ctl jr-clear")]));
  drawer.replaceChildren(head, tools, statusEl, logEl, footEl);
  drawer.addEventListener("close", onClosed);  // keys.js: Échap
}

function isOpen() { return !!drawer && !drawer.hidden; }

function journalButton() { return document.querySelector('#topActions [aria-controls="journalDrawer"]'); }

export function open() {
  if (!drawer) return;
  opener = document.activeElement instanceof HTMLElement && document.activeElement !== document.body
    ? document.activeElement : journalButton();
  view = { day: isoDay(), q: "" };
  searchEl.value = "";
  drawer.hidden = false;
  journalButton()?.setAttribute("aria-expanded", "true");
  load();
  searchEl.focus({ preventScroll: true });
}

export function close() {
  if (!isOpen()) return;
  drawer.hidden = true;
  drawer.dispatchEvent(new Event("close"));
}

function onClosed() {
  journalButton()?.setAttribute("aria-expanded", "false");
  resetClear();
  const target = opener && opener.isConnected ? opener : journalButton();
  if (target && (drawer.contains(document.activeElement) || document.activeElement === document.body)) {
    target.focus({ preventScroll: true });
  }
}

function renderDays() {
  const today = isoDay();
  const list = [today, ...info.days.filter(d => d !== today)];
  if (view.day && !list.includes(view.day)) list.push(view.day);
  daySel.replaceChildren(...list.map(d => {
    const o = el("option", "", dayLabel(d));
    o.value = d;
    return o;
  }));
  daySel.value = view.day || today;
}

/* Searched words highlighted, case and accents ignored, still as text nodes. */
function fold(s) {
  return [...s].map(c => (c.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()[0] || c)).join("");
}
function highlighted(text, q) {
  const words = fold(q).split(/[^\p{L}\p{N}]+/u).filter(w => w.length > 1);
  if (!words.length) return [document.createTextNode(text)];
  const folded = fold(text);
  const marks = new Array(text.length).fill(false);
  for (const w of words) {
    for (let i = folded.indexOf(w); i >= 0; i = folded.indexOf(w, i + 1)) marks.fill(true, i, i + w.length);
  }
  const out = [];
  let i = 0;
  while (i < text.length) {
    let j = i;
    while (j < text.length && marks[j] === marks[i]) j++;
    out.push(marks[i] ? el("mark", "", text.slice(i, j)) : document.createTextNode(text.slice(i, j)));
    i = j;
  }
  return out;
}

function row(entry) {
  const li = el("li", `jr-row ${entry.role}`);
  const when = el("time", "jr-time", fmtTime(new Date(entry.ts > 1e11 ? entry.ts : entry.ts * 1000)));
  when.dateTime = new Date(entry.ts > 1e11 ? entry.ts : entry.ts * 1000).toISOString();
  const who = el("span", "jr-who", entry.role === "user" ? J.you : entry.role === "jarvis" ? J.jarvis : J.system);
  const txt = el("p", "jr-txt");
  txt.append(...highlighted(String(entry.text || ""), view.q));
  li.append(when, who, txt);
  return li;
}

function appendRow(entry, live = false) {
  listEl.querySelector(".jr-empty")?.remove();
  listEl.append(row(entry));
  statusEl.textContent = "";
  if (live) logEl.scrollTop = logEl.scrollHeight;
}

function empty(text) {
  listEl.replaceChildren(el("li", "jr-empty none", text));
}

async function load() {
  const seq = ++loadSeq;
  const params = new URLSearchParams();
  if (view.q) params.set("q", view.q);
  else params.set("date", view.day || isoDay());
  let data;
  try {
    data = await api(`/api/journal?${params}`);
  } catch (err) {
    if (seq !== loadSeq) return;
    console.warn(err);
    empty(J.failed);
    return;
  }
  if (seq !== loadSeq) return;  // a newer search or day was asked meanwhile
  info = { enabled: data.enabled !== false, keep_days: data.keep_days ?? 30, days: data.days || [] };
  renderDays();
  keepEl.textContent = info.enabled ? J.keep(info.keep_days) : "";
  const entries = (data.entries || []).slice();
  if (!info.enabled) {
    empty(J.disabled);
    statusEl.textContent = "";
    return;
  }
  if (view.q) {
    // newest first from the server: shown day by day, in the order things were said
    statusEl.textContent = entries.length ? J.matches(entries.length, view.q) : "";
    if (!entries.length) { empty(J.noMatch(view.q)); return; }
    const byDay = new Map();
    for (const e of entries) {
      if (!byDay.has(e.date)) byDay.set(e.date, []);
      byDay.get(e.date).unshift(e);
    }
    const items = [];
    for (const [day, list] of byDay) {
      const head = el("li", "jr-dayhead");
      head.append(el("h3", "", dayLabel(day)));
      items.push(head, ...list.map(row));
    }
    listEl.replaceChildren(...items);
    logEl.scrollTop = 0;
    return;
  }
  statusEl.textContent = "";
  const unsent = view.day === isoDay() ? pending : [];  // turns still on their way
  const all = [...entries, ...unsent.map(e => ({ ...e, ts: e.ts / 1000 }))];
  if (!all.length) { empty(view.day === isoDay() ? T.empty.journal : J.emptyDay); return; }
  listEl.replaceChildren(...all.map(row));
  logEl.scrollTop = logEl.scrollHeight;
}

/* While the drawer shows today, the server's own lines (task over, reminder)
   come in with the same live events. */
function reloadSoon() {
  if (!isOpen() || view.q || view.day !== isoDay()) return;
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(load, 800);
}

/* ---------------------------------------------------------- 'Effacer l'historique' */
function askClear() {
  const ask = el("div", "jr-confirm");
  ask.setAttribute("role", "group");
  ask.setAttribute("aria-label", J.clear);
  const text = el("p", "", J.clearAsk);
  const no = button(J.cancel, () => resetClear(true), "ctl");
  const yes = button(J.clearYes, doClear, "ctl danger");
  ask.append(text, yes, no);
  footEl.replaceChildren(ask);
  no.focus();  // the safe choice under the keyboard
}

function resetClear(focus = false) {
  if (!footEl || state.remote || footEl.querySelector(".jr-clear")) return;
  const again = button(J.clear, askClear, "ctl jr-clear");
  footEl.replaceChildren(keepEl, again);
  if (focus) again.focus();
}

async function doClear() {
  try {
    await api("/api/journal", { method: "DELETE" });
  } catch (err) {
    toast(J.clearFailed(err.message));
    resetClear(true);
    return;
  }
  purged();
  toast(J.cleared, { ms: 3000 });
  announce(J.cleared);
  resetClear(true);
}

/* Emptied here, from Réglages › Données or another page. */
function purged() {
  pending = [];
  clearTimeout(flushTimer);
  firstPending = 0;
  state.history.splice(0);  // monsieur asked to forget the conversation: the next session too
  info.days = [];
  if (isOpen()) { view = { day: isoDay(), q: "" }; searchEl.value = ""; renderDays(); empty(T.empty.journal); }
}

/* ---------------------------------------------------------- 'Annuler' after a voice forget */
function onForgotten({ name, result } = {}) {
  if (name !== "forget" || !result || result.ok !== true || !Array.isArray(result.ids) || !result.ids.length) return;
  const ids = result.ids.slice(0, 10);
  const label = ids.length === 1 ? J.forgotten(String(result.forgotten?.[0] ?? "")) : J.forgottenMany(ids.length);
  toast(label, {
    actionLabel: J.undo,
    ms: 10000,
    onAction: async () => {
      try {
        for (const id of ids) await api(`/api/undo/${encodeURIComponent(id)}`, { method: "POST" });
        toast(J.restored, { ms: 3000 });
        if (isLive()) sendNotice(J.undoNotice);  // the model thinks it is gone: tell it
      } catch (err) {
        toast(J.undoFailed(err.message));
      }
    },
  });
}

/* ---------------------------------------------------------- init */
export function init() {
  drawer = $("journalDrawer");
  bus.on("turn", queueTurn);
  bus.on("tool:result", onForgotten);
  addEventListener("pagehide", flushOnLeave);
  if (!drawer) return;
  build();
  bus.on("ui:open", (name) => {
    if (name !== "journal") return;
    if (isOpen()) close(); else open();
  });
  bus.on("journal:purged", purged);                   // settings.js
  bus.on("server:journal", (ev) => { if (ev?.purged) purged(); });
  bus.on("server:task", (tk) => { if (tk && !["running", "en_file"].includes(tk.status)) reloadSoon(); });
  bus.on("server:reminder", reloadSoon);
  bus.on("server:ares", (ev) => { if (ev?.action) reloadSoon(); });
  // Today's last exchanges, for the next session's context; not awaited:
  // the other modules don't wait for the journal to start.
  api(`/api/journal?date=${isoDay()}`).then((data) => {
    info = { enabled: data.enabled !== false, keep_days: data.keep_days ?? 30, days: data.days || [] };
    refill(data.entries || []);
  }).catch((err) => console.warn(err));
  journalButton()?.setAttribute("aria-expanded", "false");
  bus.emit("ui:ready", "journal");  // hud.js shows the 'Journal' button now that it works
}
