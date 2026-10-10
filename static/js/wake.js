/* The wake word: in standby, the browser's speech recognition listens for
   "Jarvis" and opens a session; words said after the name become the first
   request.
   - Chrome's on-device French recognition is used when its pack is there
     (nothing leaves the PC); otherwise Google's, and the page says which
     ('écoute locale' / 'écoute via Google'). Any sign that on-device
     recognition doesn't work here falls back to Google on its own.
   - Only the leader page listens (delivery.isLeader): two open pages never
     both answer.
   - The name must start the sentence ("Jarvis, …", "dis Jarvis…"): talking
     about JARVIS doesn't wake it. A wake followed by silence goes back to
     sleep after 8 s instead of keeping a paid session open.
   - A refused microphone pauses the wake word; only a real 'denied' turns it
     off for good (a one-time permission that expired must not). */
import { $, api, bus, settings, setMode, state } from "./core.js";
import { earcon } from "./audio-fx.js";
import { addCard, removeCard, toast } from "./hud.js";
import { T } from "./strings-fr.js";
import { connect, requestResponse, sendNotice, sendText, sleep } from "./voice.js";
import { isLeader, leaderKnown } from "./delivery.js";

export const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
// "Jarvis", and the ways speech recognition tends to spell it.
const WAKE_RE = /\b(?:j\.?\s?a\.?\s?r\.?\s?v\.?\s?i\.?\s?s|jarvi[sz]?|javis|jervis|djarvis)\b/i;
// What may come before the name: nothing, or a call ("dis Jarvis", "OK Jarvis").
const CALL_RE = /^(?:ok|okay|hey|h[eé]|eh|dis|bonjour)\W*$/i;
// On-device recognition can't run here (no pack, no biasing, refused): Google's instead.
const LOCAL_ERRORS = new Set(["language-not-supported", "phrases-not-supported", "service-not-allowed"]);
const GUARD_MS = 8000;
// Every word on screen comes from strings-fr.js.
const S = { ...T.wake, wakeOn: T.controls.wakeOn, wakeOff: T.controls.wakeOff, refused: T.error.wakeRefused };

let wakeRec = null, restartTimer = null, restartDelay = 300;
let startedAt = 0, heard = false, trouble = false, quickEnds = 0;
let localSR = false;       // on-device recognition in use
let localStatus = null;    // available() answer: available | downloadable | downloading | unavailable | unsupported
let installing = false;
let paused = false;        // microphone refused, not for good: waits to be allowed again
let closed = false;        // « Quitter JARVIS »: never listens again in this page
let muted = 0;             // JARVIS speaking through the browser's voice: don't hear it
let guardTimer = null;
let cappedToastAt = 0, configAt = 0;
let ui = null;

export function wakeWanted() { return !!SR && !!settings.get("wake", state.config.wake_word); }

/* local | cloud (null without speech recognition): for the status line and the health check. */
export function wakeEngine() { return SR ? (localSR ? "local" : "cloud") : null; }

/* The status line says which engine listens (hud.js: ' · écoute locale'),
   or that the other window does (state.wakeHere false). */
function publishEngine() {
  state.wakeEngine = wakeEngine();
  state.wakeHere = isLeader();
  bus.emit("wake:engine", { engine: state.wakeEngine, here: state.wakeHere, local: localStatus,
                            listening: !!wakeRec, paused });
  renderToggle();
}

export function goStandby() {
  const on = wakeWanted() && !paused && !closed;
  setMode(on ? "standby" : "off");
  if (on) startWake(); else stopWake();
  renderToggle();
}

/* The on/off switch: remembered in this browser. */
export function toggleWake() {
  settings.set("wake", !wakeWanted());
  paused = false;
  removeCard("wake-refused");
  if (state.mode === "off" || state.mode === "standby") {
    stopWake();
    goStandby();
  }
  renderToggle();
}

/* ---------------------------------------------------------- on-device recognition */
/* Is Chrome's on-device French recognition usable? With install, download the
   pack when it can be (this needs a click on the page first). */
export async function localSpeech(install = false) {
  if (!SR || typeof SR.available !== "function") { localStatus = "unsupported"; return false; }
  const options = { langs: [state.config.speech_lang || "fr-FR"], processLocally: true };
  try {
    localStatus = await SR.available(options);
    if (localStatus === "available") return true;
    if (install && (localStatus === "downloadable" || localStatus === "downloading")
        && typeof SR.install === "function") {
      if (await SR.install(options)) {
        localStatus = "available";
        return true;
      }
    }
  } catch (err) {
    console.warn("Reconnaissance locale :", err);
  }
  return false;
}

function setLocal(ok) {
  const changed = ok !== localSR;
  localSR = ok;
  quickEnds = 0;
  publishEngine();
  if (changed && wakeRec) {  // move the running recognizer over
    stopWake();
    startWake();
  }
}

/* A click is the activation the download needs: the first orb click tries. */
function tryInstall() {
  if (localSR || installing || !(localStatus === "downloadable" || localStatus === "downloading")) return;
  installing = true;
  localSpeech(true).then((ok) => {
    if (!ok) return;
    addCard(S.title, S.installed, "info", { id: "wake-local" });
    setLocal(true);
  }).finally(() => { installing = false; });
}

/* ---------------------------------------------------------- listening */
function scheduleRestart(delay = restartDelay) {
  clearTimeout(restartTimer);
  restartTimer = null;
  if (state.mode !== "standby") return;
  restartTimer = setTimeout(() => { restartTimer = null; startWake(); }, delay);
}

export function startWake() {
  clearTimeout(restartTimer);
  restartTimer = null;
  if (closed || !wakeWanted() || paused || muted || wakeRec || state.mode !== "standby" || state.connecting) return;
  if (!isLeader()) { renderToggle(); return; }  // the leader page listens for both
  let rec;
  try { rec = new SR(); } catch (err) { console.warn(err); return; }
  rec.lang = state.config.speech_lang || "fr-FR";
  rec.continuous = true;
  rec.interimResults = true;
  const local = localSR;
  if (local) {
    try { rec.processLocally = true; } catch { /* older engine */ }
    // Biasing towards the name: on-device only ('phrases-not-supported' otherwise).
    if (window.SpeechRecognitionPhrase) {
      try { rec.phrases = [new window.SpeechRecognitionPhrase("Jarvis", 8.0)]; } catch { /* not here */ }
    }
  }
  rec.onresult = (e) => { if (wakeRec === rec) onWakeResult(e); };
  rec.onerror = (e) => { if (wakeRec === rec) onWakeError(local, e && e.error); };
  rec.onend = () => {
    if (wakeRec !== rec) return;
    wakeRec = null;
    // No network, no microphone, or ending at once again and again: slow
    // down (and on-device, give up on it: Google's recognition may still work).
    const quick = !heard && Date.now() - startedAt < 1000;
    if (quick || trouble) {
      restartDelay = Math.min(restartDelay * 2, 30000);
      if (quick) quickEnds++;
      if (local && quickEnds >= 3) {
        setLocal(false);
        scheduleRestart(300);
        return;
      }
    } else {
      quickEnds = 0;
      restartDelay = 300;
    }
    scheduleRestart();
  };
  try { rec.start(); } catch (err) { console.warn(err); return; }
  wakeRec = rec;
  startedAt = Date.now();
  heard = trouble = false;
  publishEngine();
}

/* JARVIS is quitting: the microphone is let go for good in this page. */
export function shutDown() {
  closed = true;
  stopWake();
}

export function stopWake() {
  clearTimeout(restartTimer);
  restartTimer = null;
  const rec = wakeRec;
  wakeRec = null;
  if (rec) {
    rec.onend = null;
    try { rec.abort(); } catch { /* already stopped */ }
  }
}

/* The browser's own voice is speaking (delivery.speak): don't listen meanwhile. */
export function muteWake(on) {
  muted = Math.max(0, muted + (on ? 1 : -1));
  if (muted) stopWake();
  else if (state.mode === "standby") startWake();
}

function onWakeError(local, error) {
  if (local && LOCAL_ERRORS.has(error)) {
    localStatus = error;
    stopWake();
    setLocal(false);
    scheduleRestart(300);
    return;
  }
  if (error === "not-allowed" || error === "service-not-allowed") {
    refused();
    return;
  }
  if (error === "network" || error === "audio-capture") trouble = true;  // onend backs off
}

async function micPermission() {
  try { return await navigator.permissions.query({ name: "microphone" }); } catch { return null; }
}

/* Paused at once; turned off for good only if the browser says 'denied'. */
function refused() {
  stopWake();
  paused = true;
  addCard(S.title, S.refused, "warning", { id: "wake-refused" });
  goStandby();
  micPermission().then((p) => {
    if (!p) return;
    if (p.state === "denied") {
      settings.set("wake", false);
      paused = false;
      addCard(S.title, S.denied, "warning", { id: "wake-refused" });
      renderToggle();
      return;
    }
    p.onchange = () => {
      if (p.state !== "granted" || !paused) return;
      paused = false;
      removeCard("wake-refused");
      if (state.mode === "off" || state.mode === "standby") goStandby();
    };
  });
}

/* ---------------------------------------------------------- hearing the name */
/* The name, only at the start of what was said (after a call word at most). */
export function findWake(text) {
  const m = WAKE_RE.exec(text || "");
  if (!m) return null;
  const before = text.slice(0, m.index).trim();
  if (before && !CALL_RE.test(before)) return null;
  return { index: m.index, end: m.index + m[0].length };
}

function acceptWake() {
  if (!isLeader()) return false;
  if (state.config.usage_capped) {  // no paid session without a click
    if (Date.now() - cappedToastAt > 30000) {
      cappedToastAt = Date.now();
      toast(S.capped);
    }
    return false;
  }
  return true;
}

export function onWakeResult(e) {
  // In a session the Realtime model hears monsieur itself: a recognizer still
  // running (a wake settled meanwhile) must not send his words a second time.
  if (state.mode === "live" && !state.wake) { stopWake(); return; }
  restartDelay = 300;
  heard = true;
  quickEnds = 0;
  for (let i = e.resultIndex; i < e.results.length; i++) {
    const text = e.results[i][0].transcript;
    const m = findWake(text);
    if (!m) continue;
    const command = text.slice(m.end).replace(/^[\s,.!?;:—-]+/, "").trim();
    if (!state.wake) {
      if (!acceptWake()) return;
      // Connect on the first hint of the name; the rest of the sentence follows.
      state.wake = { final: false, command: "" };
      earcon("wake", { userInitiated: true });
      connect();
    }
    // What is being captured, shown dimmed while monsieur speaks.
    if (command) bus.emit("caption:user", { itemId: "wake", text: command, final: !!e.results[i].isFinal, dim: true });
    if (e.results[i].isFinal) {
      state.wake.command = command;
      state.wake.final = true;
      stopWake();
      deliverWake();
    }
  }
}

export function deliverWake() {
  const w = state.wake;
  if (!w || !w.final || state.mode !== "live") return;
  state.wake = null;
  if (w.command) {
    sendText(w.command);
    return;
  }
  sendNotice("Monsieur vient de t'appeler par ton nom : réponds très brièvement (par exemple « Oui, monsieur ? ») et attends sa demande.");
  requestResponse();
  armGuard();
}

/* The name and then nothing: a false wake (TV, a conversation), most likely. */
function armGuard() {
  clearTimeout(guardTimer);
  guardTimer = setTimeout(falseWake, GUARD_MS);
}
function disarmGuard() {
  clearTimeout(guardTimer);
  guardTimer = null;
}
// JARVIS answering ('Oui, monsieur ?', then a result or the briefing that
// waited for this session) is not silence: the guard waits for it to end.
const BUSY_PHASES = new Set(["thinking", "speaking", "tool", "confirm"]);
function falseWake() {
  guardTimer = null;
  if (state.mode !== "live") return;
  if (state.responseActive || state.pendingResponse || BUSY_PHASES.has(state.phase)) {
    armGuard();
    return;
  }
  settings.set("falseWakes", (Number(settings.get("falseWakes", 0)) || 0) + 1);
  sleep();
}

/* ---------------------------------------------------------- the switch in #controlsExtra */
/* 'Mot d'éveil : activé/désactivé' (#wakeBtn) and which engine listens (#wakeEngine). */
function renderToggle() {
  const host = $("controlsExtra");
  if (!host) return;
  if (!ui) {
    const box = document.createElement("span");
    box.id = "wakeExtra";
    // The page's own #wakeBtn when it has one (index.html), so there is one switch.
    let toggle = $("wakeBtn");
    if (!toggle) {
      toggle = document.createElement("button");
      toggle.type = "button";
      toggle.id = "wakeBtn";
      toggle.className = "ctl";
    }
    toggle.hidden = false;  // the box shows or hides the pair
    toggle.addEventListener("click", () => { tryInstall(); toggleWake(); });
    const engine = document.createElement("span");
    engine.id = "wakeEngine";
    engine.className = "meta";
    host.prepend(box);
    box.append(toggle, " ", engine);
    ui = { box, toggle, engine };
  }
  const on = wakeWanted();
  ui.box.hidden = !SR || !(state.mode === "off" || state.mode === "standby");
  ui.toggle.textContent = on ? S.wakeOn : S.wakeOff;
  ui.toggle.dataset.on = String(on);  // the label says the state (no aria-pressed)
  // Which engine listens is in the status line; here only what it doesn't say.
  ui.engine.textContent = !on ? "" : paused ? S.paused : !isLeader() ? S.elsewhere : "";
}

/* After sleep or a lost network, the recognizer may be dead without saying so. */
function revive() {
  if (state.mode !== "standby" || !wakeWanted() || paused || !isLeader()) return;
  stopWake();
  restartDelay = 300;
  startWake();
}

/* The daily spending cap (usage_capped) may change while the page is open. */
function refreshConfig() {
  if (Date.now() - configAt < 10000) return;
  configAt = Date.now();
  api("/api/config").then(cfg => Object.assign(state.config, cfg)).catch(() => {});
}

/* Last module started: the page settles into standby (or off). */
export async function init() {
  bus.on("mode", ({ mode }) => {
    if (mode === "live" && paused) {  // the session got the microphone: try again afterwards
      paused = false;
      removeCard("wake-refused");
    }
    if ((mode === "live" || mode === "connecting") && !state.wake) stopWake();
    if (mode !== "live") disarmGuard();
    renderToggle();
  });
  bus.on("delivery:leader", ({ leader }) => {
    if (!leader) stopWake();
    else if (state.mode === "standby") startWake();
    publishEngine();
  });
  // Monsieur did speak (or type) after all: not a false wake.
  bus.on("phase", (p) => { if (p && p.phase === "user") disarmGuard(); });
  bus.on("turn", (t) => { if (t && t.role === "user") disarmGuard(); });
  bus.on("caption:user", (c) => { if (c && c.itemId !== "wake") disarmGuard(); });
  bus.on("ui:compose", disarmGuard);
  bus.on("tool:start", disarmGuard);
  // A message that waited for this session is being told: he called for it.
  bus.on("delivered", (d) => { if (d && d.how === "live") disarmGuard(); });
  bus.on("server:config", refreshConfig);
  bus.on("server:usage", refreshConfig);
  $("orbBtn")?.addEventListener("click", tryInstall);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") revive(); });
  addEventListener("online", revive);
  setInterval(() => {  // a recognizer that vanished without an end event
    if (state.mode === "standby" && !wakeRec && !restartTimer) startWake();
  }, 20000);

  // Local or Google, and which page listens, before the first recognizer
  // starts (the cloud one would send the room's audio meanwhile).
  const probe = localSpeech().then(setLocal);
  await Promise.race([Promise.all([probe, leaderKnown(2000)]), new Promise(r => setTimeout(r, 2500))]);
  if (state.mode === "off" && !state.connecting) goStandby();  // unless monsieur already clicked the orb
  else renderToggle();
}
