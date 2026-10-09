/* The wake word: in standby, the browser's speech recognition listens for
   "Jarvis" and opens a session; words said after the name become the first
   request. */
import { $, settings, setMode, state } from "./core.js";
import { earcon } from "./audio-fx.js";
import { addCard } from "./hud.js";
import { connect, logExchange, requestResponse, sendSystem, sendUserText } from "./voice.js";

export const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
// "Jarvis", and the ways speech recognition tends to spell it.
const WAKE_RE = /\b(?:j\.?\s?a\.?\s?r\.?\s?v\.?\s?i\.?\s?s|jarvi[sz]?|javis|jervis|djarvis)\b/i;

let wakeRec = null, wakeRestartDelay = 300;

export function wakeWanted() { return !!SR && settings.get("wake", state.config.wake_word); }

export function goStandby() {
  setMode(wakeWanted() ? "standby" : "off");
  if (state.mode === "standby") startWake();
}

/* The on/off switch: remembered in this browser. */
export function toggleWake() {
  settings.set("wake", !wakeWanted());
  if (state.mode === "off" || state.mode === "standby") {
    stopWake();
    goStandby();
  }
}

export function startWake() {
  if (!wakeWanted() || wakeRec || state.mode === "live" || state.connecting) return;
  const rec = new SR();
  rec.lang = state.config.speech_lang;
  rec.continuous = true;
  rec.interimResults = true;
  rec.onresult = onWakeResult;
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      settings.set("wake", false);
      addCard("Mot d'éveil", "Le navigateur refuse le micro ou la reconnaissance vocale : mot d'éveil désactivé. Clique l'orbe pour me parler.", "warning");
      goStandby();
    } else if (e.error === "network") {
      wakeRestartDelay = Math.min(wakeRestartDelay * 2, 15000);
    }
  };
  rec.onend = () => {
    if (wakeRec !== rec) return;
    wakeRec = null;
    if (state.mode === "standby") setTimeout(startWake, wakeRestartDelay);
  };
  try { rec.start(); wakeRec = rec; } catch { /* already listening */ }
}

export function stopWake() {
  const rec = wakeRec;
  wakeRec = null;
  if (rec) { rec.onend = null; try { rec.abort(); } catch { /* already stopped */ } }
}

export function onWakeResult(e) {
  wakeRestartDelay = 300;
  for (let i = e.resultIndex; i < e.results.length; i++) {
    const text = e.results[i][0].transcript;
    const m = WAKE_RE.exec(text);
    if (!m) continue;
    if (!state.wake) {
      // Connect on the first hint of the name; the rest of the sentence follows.
      state.wake = { final: false, command: "" };
      earcon("wake");
      connect();
    }
    if (e.results[i].isFinal) {
      state.wake.command = text.slice(m.index + m[0].length).replace(/^[\s,.!?;:—-]+/, "").trim();
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
    $("you").textContent = `« ${w.command} »`;
    logExchange("monsieur", w.command);
    sendUserText(w.command);
  } else {
    sendSystem("Monsieur vient de t'appeler par ton nom : réponds très brièvement (par exemple « Oui, monsieur ? ») et attends sa demande.");
  }
  requestResponse();
}

/* Last module started: the page settles into standby (or off). */
export function init() {
  goStandby();
}
