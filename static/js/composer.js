/* Writing to JARVIS and finding out what it can do: the text field (Ctrl+J
   or /), the suggestion chips, the rotating "Essayez" hint, the "Ce que je
   sais faire" help card (? or the Aide button) and JARVIS introducing itself
   on the very first session (design spec §3, §8, §11). */
import { $, api, bus, state } from "./core.js";
import { addCard, removeCard, toast } from "./hud.js";
import * as strings from "./strings-fr.js";
import * as voice from "./voice.js";

const { T } = strings;
const NNBSP = "\u202f"; // French typography: before : ? ! and inside « »

// Wording not (yet) in strings-fr.js.
const L = {
  helpTitle: "Ce que je sais faire",
  helpButton: "? Aide",
  helpButtonTitle: "Ce que je sais faire (touche ?)",
  chips: "Suggestions",
  continueTask: "Continuer la tâche",
  asTable: "Afficher en tableau",
  thanks: "Merci, c'est tout",
  taskSent: (text) => `Tâche confiée à Claude${NNBSP}: «${NNBSP}${text}${NNBSP}»`,
  taskEmpty: `Écrivez la tâche après /tâche, par exemple${NNBSP}: /tâche compare trois aspirateurs robots`,
  taskFailed: (why) => `Tâche non lancée${NNBSP}: ${why}`,
};

// Said to the model (not shown): plain spaces.
const SUMMARIZE = "Résume ce texte :";
const INTRO = "Première session : présente-toi en deux phrases et propose trois exemples de demandes.";
const LONG_PASTE = 2000;
const HISTORY_KEY = "jarvis.composer.history", HISTORY_MAX = 20;
const ONBOARDED_KEY = "jarvis.onboarded.voice";
const HINT_MS = 8000;
const RESULT_CHIPS_MS = 10 * 60e3;

/* Non-breaking narrow spaces where French typography wants them, so "?" never
   starts a line on its own. */
const fr = (s) => s.replace(/ ([?!:;%€»])/g, `${NNBSP}$1`).replace(/« /g, `«${NNBSP}`);

/* The help card: every example is a button that asks it for real. */
const CATEGORIES = [
  ["Applications et PC", ["Jarvis, ouvre Spotify sur l'écran de gauche", "Baisse le volume à 30 %"]],
  ["Rappels et routines", ["Rappelle-moi dans 20 minutes de sortir le pain",
                           "Tous les matins à 8 h, fais-moi un point météo"]],
  ["Recherche et fichiers", ["Cherche les meilleurs aspirateurs robots sous 400 €",
                             "Analyse le fichier ventes.xlsx et fais-moi un tableau de bord",
                             "Quel temps fera-t-il demain à Laon ?"]],
  ["Vision", ["Regarde mon écran : tu vois l'erreur ?", "Regarde-moi avec la caméra : je suis bien coiffé ?"]],
  ["Mémoire et journal", ["Retiens que je préfère le thé", "De quoi on a parlé hier ?"]],
  ["Agenda A.R.E.S", ["Qu'est-ce que j'ai aujourd'hui ?", "Note que je dois rappeler le garage"]],
].map(([title, list]) => [title, list.map(fr)]);

let input = null;
let history = [], historyPos = 0, draft = "";
let hintIndex = 0;
let resultChips = null;      // { title, until } after a task result
let standbyExamples = [];    // the 3 examples shown in standby, kept until the next standby
let chipsKey = "";
let introSent = false;
const resultsSeen = new Set(); // a finished task offers its follow-ups once

const examples = () => (T.help && T.help.examples) || CATEGORIES.flatMap(([, list]) => list);

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
  CATEGORIES.forEach(([title, list], i) => {
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
  keys.setAttribute("aria-label", "Raccourcis clavier");
  for (const part of T.help.shortcuts.split(" · ")) {
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
  const cards = $("cards");
  return (!cards || !cards.children.length) && !document.querySelector("dialog[open]");
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

/* Rotates in standby only: while live, monsieur is busy talking. */
function rotateHint() {
  if (state.mode !== "standby" && state.mode !== "off") return;
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

/* ---------------------------------------------------------- keys */
function isTypingTarget(el) {
  if (!el || el === document.body) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  return tag === "INPUT" && !["button", "checkbox", "radio", "range", "submit", "reset", "color", "file"]
    .includes((el.type || "").toLowerCase());
}

/* Ctrl+J, / and ? (keys.js owns the keyboard map; when it already handled the
   key, preventDefault says so and this stays out of the way). */
function onKey(e) {
  if (e.defaultPrevented || e.altKey || e.metaKey || e.isComposing) return;
  const typing = isTypingTarget(e.target);
  if (e.ctrlKey && !e.shiftKey && (e.code === "KeyJ" || (e.key || "").toLowerCase() === "j")) {
    if (typing && e.target !== input) return;
    e.preventDefault(); // Chrome's Downloads page otherwise
    focusInput();
    return;
  }
  if (e.ctrlKey || typing) return;
  if (e.key === "/") { e.preventDefault(); focusInput(); }
  else if (e.key === "?") { e.preventDefault(); openHelp({ focus: true }); }
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
  window.addEventListener("keydown", onKey);
  // Cards come and go (help, results): the standby examples only show on an empty screen.
  const cards = $("cards");
  if (cards) new MutationObserver(renderChips).observe(cards, { childList: true });
  setInterval(renderChips, 30e3); // result chips expire
  renderChips();
}
