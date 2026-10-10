/* The HUD around the orb (design spec §3-4): status pill, caption, live
   controls, top actions, clock, display cards, toasts and screen-reader
   announcements. Mode and phase are always written as text here, never
   left to the orb's colour alone. */
import { $, bus, isIOS, md, settings, state, touchUI, usesMarked } from "./core.js";
import { unlock } from "./audio-fx.js";
import { connect, interrupt, primeAudio, setMuted, sleep } from "./voice.js";
import { wakeEngine, wakeWanted } from "./wake.js";
import { T, explainError, fmtElapsed, fmtRelative, fmtTime, fr } from "./strings-fr.js";

const MAX_CARDS = 8;               // beyond, the oldest evictable card goes
const FADE_MS = 10 * 60e3;         // info and result cards fade after 10 minutes
const COUNTDOWN_MS = 15e3;         // 'Veille dans 15 s'
const TOOL_ELAPSED_AFTER = 10;     // seconds before a running tool shows its time

let statusEl, statusActions, cardsEl, listEl, clearBtn, moreBtn, orbBtn, panelBtn;
let micBtn, interruptBtn, sleepBtn, controlsEl;
let lastStatusKey = "", lastStatusHtml = "";

/* What the bus told us: voice.js owns the phase machine (WP02); state.phase
   wins when it sets it, the last 'phase' event otherwise. */
const ui = { phase: null, label: "", phaseAt: 0, error: null, wasSynced: false };

export function uiPhase() {
  return state.mode === "live" ? state.phase || ui.phase || null : null;
}

/* ---------------------------------------------------------- status pill */
function runningTasks() {
  return [...(state.tasks?.values?.() || [])].filter(tk => tk && tk.status === "running");
}

/* Seconds before the idle timeout sends JARVIS to standby, or null when no
   countdown applies (not live, busy, a task running, or more than 15 s left). */
export function idleCountdown(now = Date.now()) {
  const phase = uiPhase();
  if (state.mode !== "live" || (phase && phase !== "listening") || state.responseActive) return null;
  const limit = (Number(state.config?.idle_minutes) || 0) * 60e3;
  if (limit <= 0 || runningTasks().length) return null;
  const left = limit - (now - (state.lastActivity || now));
  return left <= COUNTDOWN_MS ? Math.max(0, Math.ceil(left / 1000)) : null;
}

function wakeEngineSuffix() {
  if (state.wakeHere === false) return T.status.standbyElsewhere;  // the leader page listens (wake.js)
  const engine = String(state.wakeEngine || state.config?.wake_engine || "").toLowerCase();
  if (/local|device|ondevice/.test(engine)) return T.status.standbyLocal;
  if (/cloud|google|remote|server/.test(engine)) return T.status.standbyCloud;
  return "";
}

/* The words of the status line: on a touch screen (touchUI) the T.ios
   variants, which name the orb and the buttons instead of Espace, Échap or
   Ctrl+M. The iPhone (remote page or iOS) has no wake word to mention. */
function words() {
  if (!touchUI()) return T.status;
  const noWake = state.remote || isIOS();
  return { ...T.status, off: noWake ? T.ios.standby : T.ios.off, standby: T.ios.standbyWake,
           wakeOff: noWake ? T.ios.standby : T.ios.wakeOff, speaking: T.ios.speaking, muted: T.ios.muted };
}

/* {key, text, tick, err}: key changes only when the status really changes, so
   a ticking clock (task time, countdown) is not re-announced every second. */
function statusModel(now = Date.now()) {
  const phase = uiPhase();
  const W = words();
  let key, text, tick = "";
  if (ui.error) return { key: `error:${ui.error.text}`, text: `${T.status.error} · ${ui.error.text}`, tick, err: true };
  // (live, the conversation goes on without the server: the chip says it)
  if (ui.wasSynced && state.synced === false && state.mode !== "live") {
    key = "server"; text = T.status.serverDown;
  } else if (state.mode === "off") {
    // The wake word switched off is a choice, not an outage (design spec §11).
    key = "off"; text = wakeEngine() && !wakeWanted() ? W.wakeOff : W.off;
  } else if (state.mode === "standby") {
    key = "standby";
    text = wakeWanted() ? W.standby + wakeEngineSuffix() : W.wakeOff;
  } else if (state.mode === "connecting") {
    key = `connecting:${state.retries}`;
    text = state.retries ? T.status.reconnecting(state.retries) : T.status.connecting;
  } else {
    const countdown = idleCountdown(now);
    key = `live:${phase}:${state.muted}`;
    if (phase === "user") text = T.status.user;
    else if (phase === "thinking") text = T.status.thinking;
    else if (phase === "speaking") text = W.speaking;
    else if (phase === "confirm") text = T.status.confirm;
    else if (phase === "tool") {
      text = ui.label || T.status.thinking;
      key += `:${text}`;
      const secs = Math.floor((now - ui.phaseAt) / 1000);
      if (secs >= TOOL_ELAPSED_AFTER) tick = T.status.toolElapsed(secs);
    } else if (state.muted) text = W.muted;
    else if (countdown !== null) { key += ":countdown"; text = T.status.countdown(countdown); }
    else text = T.status.listening;
  }
  const tasks = runningTasks();
  if (tasks.length) {
    key += `:tasks${tasks.length}`;
    const oldest = Math.min(...tasks.map(tk => Number(tk.started) || now / 1000));
    const elapsed = fmtElapsed(now / 1000 - oldest);
    // the elapsed time ticks; the count is what gets announced
    if (tasks.length === 1) tick += T.status.tasks(1, elapsed);
    else text += T.status.tasks(tasks.length);
  }
  return { key, text, tick, err: false };
}

const escHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/* Only the leading keyword is bold: the part before the first ' · '. */
function pillHtml(text, tick) {
  const cut = text.indexOf(" · ");
  const head = cut < 0 ? text : text.slice(0, cut), rest = cut < 0 ? "" : text.slice(cut);
  return `<b>${escHtml(head)}</b>${escHtml(rest)}${tick ? `<span class="tick">${escHtml(tick)}</span>` : ""}`;
}

/* Writes only what changed: the 1 s tick must not churn the DOM (observers,
   screen readers). */
function setAttr(el, name, value) {
  if (el.getAttribute(name) !== value) el.setAttribute(name, value);
}
function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

export function renderStatus() {
  if (!statusEl) return;
  const phase = uiPhase(), mode = state.mode;
  setAttr(document.body, "data-state", `${mode}-${phase || "idle"}`);
  document.body.classList.toggle("muted", !!state.muted && (mode === "live" || mode === "connecting"));

  const m = statusModel();
  const html = pillHtml(m.text, m.tick);
  if (html !== lastStatusHtml) {
    // A tick of the same status updates the text silently (aria-live off);
    // a new status is announced (role=status, polite). An error is not: #srAlert
    // already says it.
    statusEl.setAttribute("aria-live", m.err || m.key === lastStatusKey ? "off" : "polite");
    statusEl.innerHTML = html;
    lastStatusHtml = html;
  }
  lastStatusKey = m.key;
  statusEl.classList.toggle("err", m.err);
  renderStatusActions();

  // The orb button says what a click does; its label shows in forced colours.
  const awake = mode === "live" || mode === "connecting";
  setAttr(orbBtn, "aria-label", awake ? T.hud.orbSleep : T.hud.orbTalk);
  const cut = m.text.indexOf(" · ");
  setAttr(orbBtn, "data-label", cut < 0 ? m.text : m.text.slice(0, cut));

  renderControls(phase);
}

function renderStatusActions() {
  const err = ui.error;
  const want = err ? `${err.text}|${!!err.retry}` : "";
  if (statusActions.dataset.for === want) return;
  statusActions.dataset.for = want;
  statusActions.replaceChildren();
  if (!err) return;
  if (err.retry) {
    statusActions.append(button(T.error.retry, () => { const retry = err.retry; clearError(); retry(); }, "ctl"));
  }
  const x = button("✕", clearError, "x");
  x.setAttribute("aria-label", T.hud.dismissError);
  statusActions.append(x);
}

/* The error variant: shown until the next success or until dismissed, and
   copied to #srAlert (role=alert). Accepts a message or a bus error. */
export function setStatusError(err) {
  const e = typeof err === "object" && err !== null ? err : { message: String(err ?? "") };
  const text = explainError(e);
  const retry = typeof e.retry === "function" ? e.retry
    : e.kind === "connect" ? () => connect() : null;
  ui.error = { text, retry, at: Date.now() };
  announce(text, { urgent: true });
  renderStatus();
}
export { setStatusError as showError };

export function clearError() {
  if (!ui.error) return;
  ui.error = null;
  renderStatus();
}

/* ---------------------------------------------------------- live controls */
function button(label, onClick, className = "ctl") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = label;
  if (onClick) b.addEventListener("click", onClick);
  return b;
}

function renderControls(phase) {
  const live = state.mode === "live";
  if (controlsEl.hidden === live) controlsEl.hidden = !live;
  setText(micBtn, state.muted ? T.controls.micOff : T.controls.micOn);
  // 'Micro : activé / coupé' says the state itself: no aria-pressed on a
  // label that changes (WAI-ARIA APG), the colour follows data-on.
  setAttr(micBtn, "data-on", String(!state.muted));
  const canInterrupt = phase === "speaking" || phase === "tool";
  if (interruptBtn.disabled === canInterrupt) interruptBtn.disabled = !canInterrupt;
  // The wake word switch (#wakeBtn) belongs to wake.js: it knows when it is paused,
  // which engine listens and which page.
}

export function toggleMute() {
  setMuted(!state.muted);
}

/* ---------------------------------------------------------- top actions and side panel */
const narrow = window.matchMedia("(max-width: 1100px)");

/* Below 1100 px the side panel is off-canvas; the 'Panneau' button opens it. */
export function setSideOpen(open, { returnFocus = true } = {}) {
  const side = $("side");
  const was = document.body.classList.contains("side-open");
  open = !!open && narrow.matches;
  document.body.classList.toggle("side-open", open);
  panelBtn.setAttribute("aria-expanded", String(open));
  // What the open panel covers can't take the focus (WCAG 2.4.11); the top
  // bar stays usable above it.
  for (const id of ["cards", "stage"]) $(id).inert = open;
  if (open && !was) {
    side.setAttribute("tabindex", "-1");
    side.focus({ preventScroll: true });
  } else if (!open && was && returnFocus && side.contains(document.activeElement)) {
    panelBtn.focus();
  }
}

function renderTopActions() {
  const nav = $("topActions");
  const open = (name) => () => bus.emit("ui:open", name);
  const help = button(T.controls.help, open("aide"), "top-btn");
  help.setAttribute("aria-keyshortcuts", "?");
  const journal = button(T.controls.journal, open("journal"), "top-btn");
  journal.setAttribute("aria-controls", "journalDrawer");
  panelBtn = button(T.controls.panel, () => setSideOpen(!document.body.classList.contains("side-open")), "top-btn");
  panelBtn.id = "panelBtn";
  panelBtn.setAttribute("aria-controls", "side");
  panelBtn.setAttribute("aria-expanded", "false");
  const prefs = button(T.controls.settings, open("settings"), "top-btn");
  prefs.setAttribute("aria-controls", "settingsDialog");
  // Shown once journal.js / settings.js really open them (bus 'ui:ready'):
  // never a button that does nothing.
  journal.hidden = prefs.hidden = true;
  bus.on("ui:ready", (name) => {
    if (name === "journal") journal.hidden = false;
    if (name === "settings") prefs.hidden = false;
  });
  nav.replaceChildren(help, journal, panelBtn, prefs);
}

/* ---------------------------------------------------------- caption */
const MAX_LINES = 4;

function onUserCaption({ text = "", dim = false } = {}) {
  const you = $("you");
  you.textContent = text.trim() ? T.hud.you(text.trim()) : "";
  you.classList.toggle("dim", !!dim);  // wake.js: what the wake word is still capturing
}

/* One line per assistant item; the text is cumulative per item (a delta is
   appended when it does not extend what we have). */
function onJarvisCaption({ itemId = "", text = "", final = false } = {}) {
  const box = $("transcript");
  delete box.dataset.cur;  // the caption is ours now, not the legacy single line
  let line = [...box.querySelectorAll(".line")].find(l => l.dataset.item === String(itemId));
  if (!line) {
    if (!box.querySelector(".line")) box.textContent = "";
    line = document.createElement("span");
    line.className = "line";
    line.dataset.item = String(itemId);
    box.append(line);
    const lines = box.querySelectorAll(".line");
    lines.forEach((l, i) => l.classList.toggle("old", i < lines.length - 1));
    for (let i = 0; i < lines.length - MAX_LINES; i++) lines[i].remove();
  }
  const prev = line.dataset.raw || "";
  const raw = final || !prev || text.startsWith(prev) ? text : prev + text;
  line.dataset.raw = raw;
  line.textContent = fr(raw);  // '… des prix ?': the '?' never wraps alone
  box.scrollTop = box.scrollHeight;  // the newest words in view; older ones fade out above
  box.classList.toggle("clipped", box.scrollHeight > box.clientHeight + 1);
}

function clearCaption() {
  $("you").textContent = "";
  const box = $("transcript");
  box.textContent = "";
  box.classList.remove("clipped");
  box.dataset.cur = "";
}

/* ---------------------------------------------------------- display cards */
const cardId = (id) => `card-${id}`;
const cards = () => [...listEl.querySelectorAll(":scope > .card")];
const evictable = (el) => !el.dataset.sticky && !el.classList.contains("warning") && !el.classList.contains("confirm");
const isConfirm = (el) => el.classList.contains("confirm");

/* Pending confirmations stay at the top of the list: the bottom sheet shows
   the first card only, and a question waiting for [Lancer] must never hide
   behind '+n'. A new confirmation goes first; any other card goes after the
   last confirmation; a card that changes kind in place (an answered
   confirmation) moves to match. */
function place(el, fresh) {
  const others = cards().filter(c => c !== el);
  const before = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  if (isConfirm(el)) {
    if (fresh || others.some(c => !isConfirm(c) && before(c, el))) listEl.prepend(el);
    return;
  }
  const lastConfirm = others.filter(isConfirm).pop();
  if (lastConfirm && (fresh || before(el, lastConfirm))) lastConfirm.after(el);
  else if (fresh) listEl.prepend(el);
}

/* A card with this id already on screen is updated in place (no new
   animation); warning, confirm and sticky cards are never evicted. */
export function makeCard(title, kind = "info", { id, sticky = false } = {}) {
  title = String(title ?? "");
  let el = id ? document.getElementById(cardId(id)) : null;
  const fresh = !el;
  if (el) {
    el.querySelector(".body").replaceChildren();
    el.querySelector(".actions")?.remove();
  } else {
    el = document.createElement("article");
    if (id) el.id = cardId(id);
    el.innerHTML = `<header class="card-head"><h3><span class="t"></span></h3>`
      + `<time class="meta"></time><button class="x" type="button"><span aria-hidden="true">✕</span></button></header>`
      + `<div class="body"></div>`;
    el.querySelector(".x").addEventListener("click", () => removeCard(el, { byUser: true }));
  }
  el.className = `card ${kind}`;
  place(el, fresh);
  el.querySelector("h3 .t").textContent = title;
  el.querySelector(".x").setAttribute("aria-label", T.hud.closeCard(title));
  el.dataset.born = String(Date.now());
  el.classList.remove("fading");
  stampTime(el);
  if (sticky || kind === "confirm") el.dataset.sticky = "1"; else delete el.dataset.sticky;
  // keep at most MAX_CARDS: the oldest evictable cards go first
  const all = cards();
  for (let i = all.length - 1, n = all.length; i >= 0 && n > MAX_CARDS; i--) {
    if (all[i] !== el && evictable(all[i])) { all[i].remove(); n--; }
  }
  if (fresh && kind !== "confirm") announce(T.hud.newCard(title));  // confirm.js alerts itself
  syncCards();
  return el;
}

/* addCard(title, content, kind, {id, actions:[{label, onClick, primary}], sticky, ttlMs})
   kind: info | result | warning | code | image | confirm. Returns the card element. */
export function addCard(title, content, kind = "info", { id, actions = [], sticky = false, ttlMs = 0 } = {}) {
  const el = makeCard(title, kind, { id, sticky });
  const body = el.querySelector(".body");
  content = String(content ?? "");
  const bar = [];
  if (kind === "code") {
    const code = content.replace(/^\s*```[\w+-]*\n?/, "").replace(/\n?```\s*$/, "");
    const pre = document.createElement("pre");
    pre.tabIndex = 0;  // a scroller must be reachable by keyboard
    pre.append(Object.assign(document.createElement("code"), { textContent: code }));
    body.replaceChildren(pre);
    body.classList.remove("md");
    const copy = button(T.controls.copy, () => copyText(code), "ctl");
    copy.setAttribute("aria-label", T.hud.copyCode(title));
    bar.push(copy);
  } else {
    body.innerHTML = md(content);
    body.classList.toggle("md", usesMarked());
  }
  for (const a of actions) {
    const b = button(a.label, () => a.onClick?.(el), a.primary ? "ctl primary" : "ctl");
    bar.push(b);
  }
  if (bar.length) {
    const div = document.createElement("div");
    div.className = "actions";
    div.append(...bar);
    el.append(div);
  }
  clearTimeout(el._ttl);
  if (ttlMs > 0) el._ttl = setTimeout(() => removeCard(el), ttlMs);
  return el;
}

export function addImageCard(title, src) {
  const el = makeCard(title, "image");
  const img = new Image();
  img.src = src; img.alt = String(title ?? "");
  el.querySelector(".body").append(img);
  return el;
}

/* Removing the focused card hands the focus to its neighbour, never to <body>. */
export function removeCard(idOrEl, { byUser = false } = {}) {
  const el = typeof idOrEl === "string" ? document.getElementById(cardId(idOrEl)) : idOrEl;
  if (!el) return;
  clearTimeout(el._ttl);
  const hadFocus = byUser || el.contains(document.activeElement);
  const next = el.nextElementSibling || el.previousElementSibling;
  el.remove();
  if (hadFocus) (next?.querySelector(".x") || orbBtn).focus({ preventScroll: true });
  syncCards();
}

/* 'Tout effacer': every card but the pending confirmations. */
export function clearCards() {
  const hadFocus = cardsEl.contains(document.activeElement);
  for (const el of cards()) if (!el.classList.contains("confirm")) { clearTimeout(el._ttl); el.remove(); }
  cardsEl.classList.remove("expanded");
  syncCards();
  if (hadFocus) orbBtn.focus();
}

/* Info and result cards fade out after 10 minutes. */
export function sweepCards(now = Date.now()) {
  for (const el of cards()) {
    if (el.dataset.sticky || el.classList.contains("fading")) continue;
    if (!(el.classList.contains("info") || el.classList.contains("result"))) continue;
    if (now - Number(el.dataset.born) < FADE_MS) continue;
    el.classList.add("fading");
    setTimeout(() => removeCard(el), reducedMotion() ? 0 : 600);
  }
}

function stampTime(el, now = Date.now()) {
  const time = el.querySelector("time.meta");
  const born = Number(el.dataset.born);
  if (!time || !born) return;
  time.dateTime = new Date(born).toISOString();
  time.textContent = fmtRelative(born, now);
}

/* The 'Tout effacer' and '+n' controls, and the sheet height that keeps the
   bottom sheet (below 900 px) off the orb and the composer. */
function syncCards() {
  const n = cards().length;
  cardsEl.classList.toggle("empty", n === 0);
  clearBtn.hidden = n < 2;
  syncMore();
  measureSheet();
}

/* '+n' in the bottom sheet: the other cards, or the rest of a newest card
   too long for the sheet (its body is cut at 4.4 lines). */
function syncMore() {
  const more = cards().length - 1;
  const top = listEl.firstElementChild;  // the newest card ('Aide' is never cut)
  const first = top && top.id !== "card-aide" ? top.querySelector(".body") : null;
  const long = sheetQuery.matches && !!first
    && first.scrollHeight > 4.4 * parseFloat(getComputedStyle(first).fontSize) + 1;
  if (more < 1 && !long) cardsEl.classList.remove("expanded");
  const expanded = cardsEl.classList.contains("expanded");
  moreBtn.hidden = more < 1 && !long;
  moreBtn.textContent = expanded ? "−" : more >= 1 ? T.hud.moreCards(more) : "+";
  moreBtn.setAttribute("aria-label", expanded ? T.hud.fewerCards
    : more >= 1 ? T.hud.moreCardsLabel(more) : T.hud.wholeCard);
  moreBtn.setAttribute("aria-expanded", String(expanded));
  // A cut body scrolls: the keyboard must reach it too.
  for (const b of listEl.querySelectorAll(".body[tabindex]")) if (b !== first || expanded) b.removeAttribute("tabindex");
  if (first && long && !expanded) first.tabIndex = 0;
}

const sheetQuery = window.matchMedia("(max-width: 900px)");
function measureSheet() {
  if (!cardsEl) return;
  const sheet = sheetQuery.matches && !cardsEl.classList.contains("empty");
  if (cardsEl.classList.contains("expanded")) return;  // an expanded sheet overlays, on purpose
  const h = sheet ? Math.ceil(cardsEl.getBoundingClientRect().height) : 0;
  document.body.style.setProperty("--sheet-h", `${h}px`);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(T.controls.copied, { ms: 2500 });
  } catch {
    toast(T.hud.copyFailed);
  }
}

/* ---------------------------------------------------------- toasts & announcements */
/* focus: put the keyboard on the action at once ('Rappel supprimé · Annuler'). */
export function toast(text, { actionLabel, onAction, ms = 6000, focus = false } = {}) {
  const el = document.createElement("div");
  el.className = "toast";
  const span = document.createElement("span");
  span.textContent = text;
  el.append(span);
  if (actionLabel) {
    el.append(button(actionLabel, () => { el.remove(); onAction?.(); }, "ctl"));
  }
  $("toasts").append(el);
  // Held while the pointer or the focus is on it (WCAG 2.2.1): its action
  // stays within reach; it goes 3 s after they leave.
  let timer = setTimeout(() => el.remove(), ms);
  const hold = () => clearTimeout(timer);
  const resume = () => { clearTimeout(timer); timer = setTimeout(() => el.remove(), 3000); };
  el.addEventListener("pointerenter", hold);
  el.addEventListener("pointerleave", resume);
  el.addEventListener("focusin", hold);
  el.addEventListener("focusout", resume);
  if (focus) el.querySelector("button")?.focus();
  return el;
}

/* Said by screen readers only (polite); urgent messages use #srAlert.
   The same text twice is still announced (cleared, then set again). */
export function announce(text, { urgent = false } = {}) {
  const el = $(urgent ? "srAlert" : "sr");
  if (!el) return;
  if (el.textContent === text) {
    el.textContent = "";
    setTimeout(() => { el.textContent = text; }, 60);
  } else {
    el.textContent = text;
  }
}

/* ---------------------------------------------------------- clock and the 1 s tick */
const reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
export function reducedMotion() {
  return reduceQuery.matches || settings.get("motion") === "reduced";
}

let lastMinute = -1, ticks = 0;
function tick() {
  const now = new Date();
  if (now.getMinutes() !== lastMinute) {
    lastMinute = now.getMinutes();
    const clock = $("clock");
    clock.textContent = fmtTime(now);
    clock.dateTime = now.toISOString();
  }
  // Animations setting (WCAG 2.2.2): body.reduce-motion stops them all.
  document.body.classList.toggle("reduce-motion", settings.get("motion") === "reduced");
  renderServerChip();
  renderStatus();  // task time, countdown, tool time; unchanged text writes nothing
  if (++ticks % 15 === 0) {
    for (const el of cards()) stampTime(el, now.getTime());
    sweepCards(now.getTime());
  }
}

/* #serverChip itself belongs to sse.js (it knows the stream); the status
   line only needs to know the server was reachable once. */
function renderServerChip() {
  if (state.synced) ui.wasSynced = true;
}

/* ---------------------------------------------------------- weather and headlines */
/* Say it short, show it full (design spec §0.4): JARVIS sums the 'info' tool
   up aloud; the card keeps the full forecast with its credit, or every
   headline with its link. Headlines are written by others: text only, and
   only web links. */
export function showInfo({ name, result } = {}) {
  if (name !== "info" || !result || typeof result !== "object" || result.ok !== true) return null;
  if (Array.isArray(result.headlines)) {
    const el = makeCard(T.info.newsTitle, "info", { id: "info-actus" });
    const ul = document.createElement("ul");
    ul.className = "info-news";
    for (const h of result.headlines.slice(0, 8)) {
      if (!h || typeof h !== "object") continue;
      const li = document.createElement("li");
      const link = String(h.link || "");
      const title = String(h.title || "").slice(0, 300);
      if (/^https?:\/\//i.test(link)) {
        const a = document.createElement("a");
        a.href = link;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = title;
        li.append(a);
      } else {
        li.append(title);
      }
      if (h.source) li.append(` · ${String(h.source).slice(0, 80)}`);
      ul.append(li);
    }
    const body = el.querySelector(".body");
    body.classList.add("md");
    body.replaceChildren(ul);
    return el;
  }
  if (typeof result.text === "string" && result.text) {
    const el = makeCard(T.info.weatherTitle(String(result.city || "").slice(0, 80)), "info", { id: "info-meteo" });
    const p = document.createElement("p");
    p.textContent = result.text;
    const credit = document.createElement("p");
    credit.className = "meta";
    credit.textContent = String(result.source || "");
    const body = el.querySelector(".body");
    body.classList.add("md");
    body.replaceChildren(p, credit);
    return el;
  }
  return null;
}

/* ---------------------------------------------------------- init */
export function init() {
  statusEl = $("statusPill"); statusActions = $("statusActions");
  cardsEl = $("cards"); orbBtn = $("orbBtn"); controlsEl = $("controls");

  // cards: a bar ('Tout effacer', '+n' for the bottom sheet) above the list
  const bar = document.createElement("div");
  bar.className = "cards-bar";
  moreBtn = button("", () => { cardsEl.classList.toggle("expanded"); syncCards(); }, "ctl more");
  moreBtn.setAttribute("aria-controls", "cardList");
  clearBtn = button(T.controls.clearAll, clearCards, "ctl clear");
  bar.append(moreBtn, clearBtn);
  listEl = document.createElement("div");
  listEl.className = "cards-list";
  listEl.id = "cardList";
  // The cards' heading, for screen readers (h1 brand > h2 Affichages > h3 cards).
  const heading = document.createElement("h2");
  heading.className = "visually-hidden";
  heading.textContent = T.hud.cards;
  cardsEl.replaceChildren(heading, bar, listEl);
  if ("ResizeObserver" in window) new ResizeObserver(() => { syncMore(); measureSheet(); }).observe(cardsEl);
  sheetQuery.addEventListener?.("change", measureSheet);
  syncCards();

  // live controls: [Micro] [Interrompre] [Veille]
  micBtn = button(T.controls.micOn, toggleMute, "ctl mic");
  micBtn.id = "micBtn";
  micBtn.setAttribute("aria-keyshortcuts", "Control+M");
  interruptBtn = button(T.controls.interrupt, () => interrupt(), "ctl");
  interruptBtn.id = "interruptBtn";
  interruptBtn.setAttribute("aria-keyshortcuts", "Escape");
  sleepBtn = button(T.controls.sleep, () => sleep(), "ctl");
  sleepBtn.id = "sleepBtn";
  controlsEl.replaceChildren(micBtn, interruptBtn, sleepBtn);
  controlsEl.setAttribute("role", "group");
  controlsEl.setAttribute("aria-label", T.hud.liveControls);

  renderTopActions();
  narrow.addEventListener?.("change", () => { if (!narrow.matches) setSideOpen(false, { returnFocus: false }); });
  // The off-canvas panel (≤ 1100 px): its own ✕, and a click beside it closes it.
  const closeSide = button("✕", () => setSideOpen(false), "x side-close");
  closeSide.setAttribute("aria-label", T.hud.closePanel);
  $("side").prepend(closeSide);
  document.addEventListener("pointerdown", (e) => {
    if (!document.body.classList.contains("side-open")) return;
    if ($("side").contains(e.target) || panelBtn.contains(e.target)) return;
    setSideOpen(false, { returnFocus: false });
  });
  // The panel and the toasts sit under the top bar, whatever its height (it wraps).
  const topbar = $("topbar");
  const topH = () => document.body.style.setProperty("--topbar-h", `${topbar.offsetHeight}px`);
  topH();
  if ("ResizeObserver" in window) new ResizeObserver(topH).observe(topbar);

  orbBtn.addEventListener("click", () => {
    if (state.mode === "live" || state.mode === "connecting") { sleep(); return; }
    // Inside the tap itself, before any await: iOS lets sound start only from
    // a user's gesture (the earcons' AudioContext, then JARVIS's voice).
    unlock();
    primeAudio();
    connect();
  });

  bus.on("mode", ({ mode } = {}) => {
    ui.phase = null; ui.label = "";
    if (mode === "connecting" || mode === "live") ui.error = null;  // retried or succeeded
    if (mode === "connecting") clearCaption();
    renderStatus();
  });
  bus.on("phase", ({ phase = null, label = "" } = {}) => {
    if (phase !== ui.phase || label !== ui.label) ui.phaseAt = Date.now();
    ui.phase = phase; ui.label = label || "";
    if (phase === "speaking" || phase === "user") ui.error = null;  // things work again
    renderStatus();
  });
  bus.on("tool:start", ({ label = "" } = {}) => { if (label) ui.label = label; renderStatus(); });
  bus.on("tool:result", showInfo);
  bus.on("muted", renderStatus);
  bus.on("wake:engine", () => renderStatus());  // ' · écoute locale' / ' · écoute via Google'
  bus.on("error", setStatusError);
  bus.on("caption:user", onUserCaption);
  bus.on("caption:jarvis", onJarvisCaption);
  // After every listener of the event: delivery.js keeps state.tasks up to date.
  for (const type of ["server:task", "server:config"]) bus.on(type, () => queueMicrotask(renderStatus));

  tick();
  setInterval(tick, 1000);
}
