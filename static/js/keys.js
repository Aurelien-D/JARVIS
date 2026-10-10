/* Keyboard shortcuts (design spec §8; the same table is shown in Aide).
   In-page shortcuts are ignored while the focus is in a text field, except
   Échap and Ctrl+M. The global hotkey (any app) arrives from the server as a
   'hotkey' event. */
import { $, bus, settings, state } from "./core.js";
import { connect, interrupt, pttDown, pttUp, sleep } from "./voice.js";
import { setSideOpen, toast, toggleMute, uiPhase } from "./hud.js";
import { focusInput } from "./composer.js";
import { cancelLock } from "./confirm.js";
import { isLeader } from "./delivery.js";
import { toggleWake, wakeWanted } from "./wake.js";
import { T } from "./strings-fr.js";

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

/* Échap: 0) cancel a lock-screen countdown; 1) close the topmost dialog or
   drawer; 2) interrupt JARVIS while it speaks or runs a tool; 3) otherwise nothing. */
function onEscape(e) {
  // The lock-screen countdown first: Échap there cancels the lock, nothing else.
  if (cancelLock()) {
    e.preventDefault();
    return;
  }
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

/* ---------------------------------------------------------- the global hotkey and the tray icon (WP13) */
/* The server registers Ctrl+Alt+Maj+J with Windows, brings this window to the
   front and sends 'hotkey': 'toggle' talks or goes back to standby (like the
   orb), 'talk' (the icon's Parler) only starts, 'wake' turns the wake word on
   or off. Only the page that speaks acts, and an event replayed after a
   reconnection (older than a few seconds) must not open the microphone. */
const HOTKEY_FRESH_S = 8;

/* A modal dialog (Réglages, a report, the Mise en route…) makes the rest of
   the page inert: the conversation would start behind it, the composer out
   of reach. The hotkey closes them first; a sensitive change waiting for its
   confirmation is cancelled, never confirmed. Resolves once their own 'close'
   handlers have run (Réglages hands the focus back to its button there). */
function closeModals() {
  let open = [];
  try { open = [...document.querySelectorAll("dialog:modal")]; } catch { open = [...document.querySelectorAll("dialog[open]")]; }
  if (!open.length) return null;
  const closed = open.map(d => new Promise(resolve => d.addEventListener("close", resolve, { once: true })));
  const pending = $("settingsConfirm");
  if (pending?.open) pending.close("cancel");
  for (const d of open.reverse()) if (d.open) d.close();
  return Promise.race([Promise.all(closed), new Promise(resolve => setTimeout(resolve, 500))]);
}

export function onShellAction(ev = {}) {
  const at = Number(ev.at);
  if (!Number.isFinite(at) || Math.abs(Date.now() / 1000 - at) > HOTKEY_FRESH_S) return;
  if (!isLeader()) return;
  if (ev.action === "wake") {
    toggleWake();
    toast(wakeWanted() ? T.controls.wakeOn : T.controls.wakeOff);
    return;
  }
  try { window.focus(); } catch { /* not allowed: the server already raised the window */ }
  const awake = state.mode === "live" || state.mode === "connecting";
  if (ev.action === "toggle" && awake) { sleep(); return; }
  if (ev.action !== "toggle" && ev.action !== "talk") return;
  const closing = closeModals();
  if (!awake) connect();
  // Voice-first, never voice-only: the window just came to the front, so the
  // keyboard is ready too. The composer takes it, so monsieur can speak or type
  // at once; with 'Maintenir Espace pour parler' the orb does (Espace talks).
  const focus = () => {
    if (settings.get("ptt", false)) $("orbBtn")?.focus({ preventScroll: true });
    else focusInput();
  };
  if (closing) closing.then(focus); else focus();
}

export function init() {
  bus.on("server:hotkey", onShellAction);
  // capture phase: seen before any field handler, and early enough for Ctrl+J
  window.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("keyup", onKeyUp, true);
  window.addEventListener("blur", () => { if (pttHeld) { pttHeld = false; pttUp(); } });
  watchOpenings();
}
