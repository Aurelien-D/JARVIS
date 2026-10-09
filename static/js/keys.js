/* Keyboard shortcuts (design spec §8; the same table is shown in Aide).
   In-page shortcuts are ignored while the focus is in a text field, except
   Échap and Ctrl+M. */
import { $, bus, settings, state } from "./core.js";
import { interrupt, pttDown, pttUp } from "./voice.js";
import { setSideOpen, toggleMute, uiPhase } from "./hud.js";
import { focusInput } from "./composer.js";

/* A field where the keys type text (a checkbox or a button is not one). */
function typing(el) {
  if (!el || el === document.body) return false;
  if (el.isContentEditable || el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
  return el.tagName === "INPUT"
    && !/^(button|checkbox|radio|range|submit|reset|color|file|image)$/i.test(el.type || "");
}

/* ---------------------------------------------------------- what Échap closes */
/* Dialogs, drawers and the off-canvas panel, in the order they opened: Échap
   closes the most recent one. */
const opened = new Map();
let order = 0;

function isOpen(el) {
  if (el.id === "side") return document.body.classList.contains("side-open");
  if (el.tagName === "DIALOG") return el.open;
  return !el.hidden;
}

function track(el) {
  if (isOpen(el)) { if (!opened.has(el)) opened.set(el, ++order); } else opened.delete(el);
}

function watchOpenings() {
  // dialogs ('open') and drawers ('hidden') anywhere; the side panel is a class on <body>
  new MutationObserver((records) => {
    for (const { target: el } of records) {
      if (el.tagName === "DIALOG" || el.classList?.contains("drawer")) track(el);
    }
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open", "hidden"] });
  new MutationObserver(() => track($("side")))
    .observe(document.body, { attributes: true, attributeFilter: ["class"] });
  document.querySelectorAll("dialog, .drawer").forEach(track);
}

function topmost() {
  for (const el of opened.keys()) if (!el.isConnected || !isOpen(el)) opened.delete(el);
  // anything open that the observer has not reported yet counts as newest
  document.querySelectorAll("dialog[open], .drawer:not([hidden])").forEach(track);
  let top = null, best = -1;
  for (const [el, n] of opened) if (n > best) { top = el; best = n; }
  return top;
}

function closeLayer(el) {
  if (el.id === "side") {
    setSideOpen(false);
  } else if (el.tagName === "DIALOG") {
    el.close();  // fires 'close' for its owner; the browser returns the focus
  } else {
    el.hidden = true;
    el.dispatchEvent(new Event("close"));
  }
  opened.delete(el);
  bus.emit("ui:close", el.id);
}

/* Échap: 1) close the topmost dialog or drawer; 2) interrupt JARVIS while
   it speaks or runs a tool; 3) otherwise nothing. */
function onEscape(e) {
  let modal = null;
  try { modal = document.querySelector("dialog:modal"); } catch { /* :modal unsupported */ }
  if (modal) return;  // a modal dialog closes itself on Échap (and fires 'cancel')
  const top = topmost();
  if (top) {
    e.preventDefault();
    closeLayer(top);
    return;
  }
  const phase = uiPhase();
  if (phase === "speaking" || phase === "tool") {
    e.preventDefault();
    interrupt();
  } else if (window.speechSynthesis?.speaking) {
    speechSynthesis.cancel();  // a notice read aloud while asleep
  }
}

/* ---------------------------------------------------------- the map */
let pttHeld = false;
const pttWanted = () => settings.get("ptt", false) === true && state.mode === "live";

function focusComposer(e) {
  if (!$("askInput")) return;
  e.preventDefault();  // Ctrl+J: before Chrome's Downloads (UNVERIFIED in --app windows; '/' is the fallback)
  focusInput();  // composer.js: the caret goes after what is already typed
}

function onKeyDown(e) {
  if (e.isComposing || e.defaultPrevented) return;
  const key = e.key || "";
  const lower = key.toLowerCase();
  const ctrl = e.ctrlKey || e.metaKey;
  const inField = typing(e.target) || typing(document.activeElement);

  if (key === "Escape" || key === "Esc") return onEscape(e);

  if (ctrl && !e.altKey && !e.shiftKey && lower === "m") {
    if (state.mode !== "live" && state.mode !== "connecting") return;
    e.preventDefault();
    toggleMute();
    return;
  }
  // Ctrl+J in the composer itself must not open Chrome's Downloads either.
  if (ctrl && !e.altKey && !e.shiftKey && lower === "j"
      && (!inField || e.target === $("askInput"))) return focusComposer(e);
  if (inField) return;

  if (key === "/" && !ctrl && !e.altKey) return focusComposer(e);
  if (key === "?" && !ctrl && !e.altKey) {
    e.preventDefault();
    bus.emit("ui:open", "aide");
    return;
  }
  if (key === " " || e.code === "Space") {
    if (ctrl || e.altKey || e.shiftKey) return;
    const orbBtn = $("orbBtn");
    const onOrb = e.target === orbBtn;
    const onPage = !document.activeElement || document.activeElement === document.body;
    if (!onOrb && !onPage) return;  // Space keeps its meaning on every other control
    if (pttWanted()) {
      e.preventDefault();  // held Space talks; the orb button must not toggle on keyup
      if (!e.repeat && !pttHeld) { pttHeld = true; pttDown(); }
      return;
    }
    if (onOrb) return;  // the focused button toggles by itself (click on keyup)
    e.preventDefault();  // no page scroll
    if (!e.repeat) orbBtn.click();
  }
}

function onKeyUp(e) {
  if ((e.key === " " || e.code === "Space") && pttHeld) {
    e.preventDefault();
    pttHeld = false;
    pttUp();
  }
}

export function init() {
  // capture phase: seen before any field handler, and early enough for Ctrl+J
  window.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("keyup", onKeyUp, true);
  window.addEventListener("blur", () => { if (pttHeld) { pttHeld = false; pttUp(); } });
  watchOpenings();
}
