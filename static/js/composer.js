/* Writing to JARVIS and finding out what it can do: the text field (Ctrl+J
   or /, see keys.js), the suggestion chips, the rotating "Essayez" hint, the "Ce que je
   sais faire" help card (? or the Aide button) and JARVIS introducing itself
   on the very first session (design spec §3, §8, §11). */
import { $, api, bus, state } from "./core.js";
import { addCard, removeCard, toast } from "./hud.js";
import * as strings from "./strings-fr.js";
import * as voice from "./voice.js";

const { T } = strings;
const L = T.composer;  // every word on screen comes from strings-fr.js

// Said to the model (not shown): plain spaces.
const SUMMARIZE = "Résume ce texte :";
const INTRO = "Première session : présente-toi en deux phrases et propose trois exemples de demandes.";
const LONG_PASTE = 2000;
const HISTORY_KEY = "jarvis.composer.history", HISTORY_MAX = 20;
const ONBOARDED_KEY = "jarvis.onboarded.voice";
const HINT_MS = 8000;
const RESULT_CHIPS_MS = 10 * 60e3;

/* The help card: every example is a button that asks it for real. */
const CATEGORIES = () => T.help.categories || [];

let input = null;
let history = [], historyPos = 0, draft = "";
let hintIndex = 0;
let resultChips = null;      // { title, until } after a task result
let standbyExamples = [];    // the 3 examples shown in standby, kept until the next standby
let chipsKey = "";
let introSent = false;
const resultsSeen = new Set(); // a finished task offers its follow-ups once

const examples = () => (T.help && T.help.examples) || CATEGORIES().flatMap(c => c.examples);

/* ---------------------------------------------------------- sending */
/* What monsieur typed (or clicked): to the live session, or it opens one. */
export function submit(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return false;
  remember(text);
  clearResultChips();
  const task = /^\/t[âa]che(?:\s+|$)/i.exec(text);
  if (task) { runTask(text.slice(task[0].length).trim()); return true; }
  // A long paste is something to read, not a question.
  const said = text.length > LONG_PASTE ? `${SUMMARIZE}\n${text}` : text;
  if (voice.isLive()) voice.sendText(said);
  else voice.connect({ pendingText: said });
  return true;
}

/* "/tâche …": straight to Claude Code (read-only), no voice session, no OpenAI cost. */
async function runTask(prompt) {
  if (!prompt) { toast(L.taskEmpty); return; }
  try {
    await api("/api/tasks", { method: "POST", body: { prompt, profile: "lecture" } });
    toast(L.taskSent(prompt.length > 60 ? `${prompt.slice(0, 57)}…` : prompt));
  } catch (err) {
    toast(L.taskFailed(err.message || String(err)));
  }
}

/* Put text in the field (e.g. "Suite de « … » : ") for monsieur to finish, or send it. */
export function compose(text, submitNow = false) {
  if (!input) return;
  if (submitNow) { submit(text); return; }
  input.value = text || "";
  focusInput();
}

export function focusInput() {
  if (!input) return;
  input.focus();
  const end = input.value.length;
  try { input.setSelectionRange(end, end); } catch { /* not a text field */ }
}

/* ---------------------------------------------------------- history (↑ / ↓) */
function loadHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    if (Array.isArray(saved)) history = saved.filter(h => typeof h === "string").slice(-HISTORY_MAX);
  } catch { history = []; }
  historyPos = history.length;
}

function remember(text) {
  if (history[history.length - 1] !== text) history.push(text);
  if (history.length > HISTORY_MAX) history.splice(0, history.length - HISTORY_MAX);
  historyPos = history.length;
  draft = "";
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch { /* private mode */ }
}

function browseHistory(step) {
  if (!history.length) return false;
  const next = historyPos + step;
  if (next < 0 || next > history.length) return true;
  if (historyPos === history.length) draft = input.value;
  historyPos = next;
  input.value = next === history.length ? draft : history[next];
  focusInput();
  return true;
}

/* ---------------------------------------------------------- the field */
function renderComposer() {
  const host = $("composer");
  if (!host) return;
  const form = document.createElement("form");
  form.id = "ask";
  form.setAttribute("autocomplete", "off");
  input = document.createElement("input");
  input.id = "askInput";
  input.type = "text";
  input.placeholder = T.controls.composerPlaceholder;
  input.setAttribute("aria-label", T.controls.composerLabel);
  input.setAttribute("autocomplete", "off");
  input.setAttribute("enterkeyhint", "send");
  input.spellcheck = true;
  const send = document.createElement("button");
  send.type = "submit";
  send.setAttribute("aria-label", T.controls.send);
  send.title = T.controls.send;
  send.textContent = "↵";
  form.append(input, send);
  host.replaceChildren(form);

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (submit(input.value)) input.value = "";
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") input.blur(); // and keys.js may still interrupt JARVIS
    else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !e.altKey && !e.ctrlKey) {
      if (browseHistory(e.key === "ArrowUp" ? -1 : 1)) e.preventDefault();
    }
  });
}

/* ---------------------------------------------------------- help */
function buildHelp() {
  const wrap = document.createElement("div");
  wrap.className = "aide";
  const grid = document.createElement("div");
  grid.className = "aide-grid";
  // A.R.E.S switched off in Réglages: its examples would only get a refusal.
  const shown = CATEGORIES().filter(c => !(c.ares && state.ares && state.ares.mode === "off"));
  shown.forEach(({ title, examples: list }, i) => {
    const section = document.createElement("section");
    section.className = "aide-cat";
    const h = document.createElement("h4");
    h.id = `aide-cat-${i}`;
    h.textContent = title;
    section.setAttribute("aria-labelledby", h.id);
    const ul = document.createElement("ul");
    for (const example of list) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "aide-ex";
      b.textContent = example;
      b.addEventListener("click", () => submit(example));
      li.append(b);
      ul.append(li);
    }
    section.append(h, ul);
    grid.append(section);
  });
  // One shortcut per item, so a line break never splits "Échap : interrompre".
  const keys = document.createElement("ul");
  keys.className = "aide-keys";
  keys.setAttribute("aria-label", L.shortcuts);
  // The global hotkey as set in Réglages › Système; none shown when Windows
  // refused it, nor on the iPhone (it is the PC's keyboard).
  const hk = state.config && state.config.hotkey;
  const parts = T.help.shortcuts.split(" · ");
  if (hk && hk.combo && hk.active !== false && !state.remote) parts.push(T.help.globalKey(hk.combo));
  for (const part of parts) {
    const li = document.createElement("li");
    li.textContent = part;
    keys.append(li);
  }
  wrap.append(grid, keys);
  return wrap;
}

/* "Ce que je sais faire": a card; the same id updates it in place. */
export function openHelp({ focus = false } = {}) {
  const card = addCard(L.helpTitle, "", "info", { id: "aide" });
  if (!card) return null;
  const body = card.querySelector(".body") || card;
  body.replaceChildren(buildHelp());
  if (focus) body.querySelector("button.aide-ex")?.focus();
  return card;
}

export function closeHelp() { removeCard("aide"); }

/* The topbar's Aide button belongs to the HUD; a HUD without one gets this one. */
function renderHelpButton() {
  const nav = $("topActions");
  if (!nav || nav.children.length) return;
  const b = document.createElement("button");
  b.type = "button";
  b.id = "helpBtn";
  b.className = "ctl";
  b.textContent = L.helpButton;
  b.title = L.helpButtonTitle;
  b.addEventListener("click", () => openHelp({ focus: true }));
  nav.append(b);
}

function wantsHelp(detail) {
  const what = typeof detail === "string" ? detail
    : detail && (detail.panel || detail.id || detail.name || detail.what || detail.target);
  return ["aide", "help", "?"].includes(String(what || "").toLowerCase());
}

/* ---------------------------------------------------------- chips */
function shuffled(list) {
  const out = list.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function nothingShown() {
  const cards = $("cards");  // the HUD keeps its own bar and list in there: count the cards
  return (!cards || !cards.querySelector(".card")) && !document.querySelector("dialog[open]");
}

function clearResultChips() {
  if (!resultChips) return;
  resultChips = null;
  renderChips();
}

function chipList() {
  if (resultChips && Date.now() < resultChips.until) {
    const { title } = resultChips;
    return [
      { label: L.continueTask, run: () => bus.emit("ui:compose", { text: `Suite de « ${title} » : ` }) },
      { label: L.asTable, run: () => voice.sendText(`Affiche le résultat de « ${title} » en tableau.`) },
      // In standby there is nothing to end: no session is opened just to say goodbye.
      { label: L.thanks, run: () => { if (voice.isLive()) voice.sendText("Merci, c'est tout."); } },
    ];
  }
  resultChips = null;
  if ((state.mode === "standby" || state.mode === "off") && nothingShown()) {
    if (!standbyExamples.length) standbyExamples = shuffled(examples()).slice(0, 3);
    return standbyExamples.map(text => ({ label: text, example: true, run: () => submit(text) }));
  }
  return [];
}

function renderChips() {
  const box = $("chips");
  if (!box) return;
  const chips = chipList().slice(0, 4);
  const key = chips.map(c => c.label).join("|");
  if (key === chipsKey) return; // unchanged: keep focus and avoid flicker
  chipsKey = key;
  box.replaceChildren(...chips.map(chip => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = chip.example ? "chip example" : "chip";
    b.textContent = chip.label;
    b.addEventListener("click", () => {
      resultChips = null;
      chip.run();
      renderChips();
    });
    return b;
  }));
}

/* A task that just finished (not the history replayed on load). */
function onTask(tk) {
  if (!tk || tk.status !== "done" || !state.synced || resultsSeen.has(tk.id)) return;
  resultsSeen.add(tk.id);
  resultChips = { title: tk.title || "la tâche", until: Date.now() + RESULT_CHIPS_MS };
  renderChips();
}

/* ---------------------------------------------------------- hint */
function showHint() {
  const el = $("hint");
  const list = examples();
  if (!el || !list.length) return;
  el.textContent = T.help.tryThis(list[hintIndex % list.length]);
}

/* Rotates in standby only: while live, monsieur is busy talking. Never with
   the Animations setting off: it is auto-updating content (WCAG 2.2.2). */
function rotateHint() {
  if (state.mode !== "standby" && state.mode !== "off") return;
  if (document.body.classList.contains("reduce-motion")) return;
  hintIndex++;
  showHint();
}

/* ---------------------------------------------------------- first session */
function onboarded() {
  if (introSent) return true;
  try { return !!localStorage.getItem(ONBOARDED_KEY); } catch { return false; }
}

/* Session ready (voice.js switches to live on session.created): the first one
   ever starts with JARVIS introducing itself. Typed text, a wake-word request
   or news waiting for this session ask for the response themselves; the
   notice then simply comes before them. */
function introduceOnce() {
  if (onboarded()) return;
  if (!voice.sendNotice(INTRO)) return;
  introSent = true;
  try { localStorage.setItem(ONBOARDED_KEY, "1"); } catch { /* sent once per page, then */ }
  if (!state.pendingText && !state.wake && !state.queue.length) voice.requestResponse();
}

export function init() {
  loadHistory();
  renderComposer();
  renderHelpButton();
  const chipsBox = $("chips");
  if (chipsBox) {
    chipsBox.setAttribute("role", "group");
    chipsBox.setAttribute("aria-label", L.chips);
  }
  hintIndex = Math.floor(Math.random() * Math.max(1, examples().length));
  showHint();
  setInterval(rotateHint, HINT_MS);

  bus.on("mode", (d) => {
    const mode = d && d.mode;
    if (mode === "live") introduceOnce();
    if (mode === "live" || mode === "connecting") standbyExamples = [];
    renderChips();
  });
  bus.on("server:task", onTask);
  bus.on("ui:compose", (d) => compose(typeof d === "string" ? d : d && d.text, !!(d && d.submit)));
  bus.on("ui:open", (d) => { if (wantsHelp(d)) openHelp({ focus: true }); });
  // Cards come and go (help, results): the standby examples only show on an empty screen.
  const cards = $("cards");
  if (cards) new MutationObserver(renderChips).observe(cards, { childList: true, subtree: true });
  setInterval(renderChips, 30e3); // result chips expire
  renderChips();
}
