/* Sound: the one shared AudioContext and the earcons (design spec §4).
   Earcons follow the guidelines: a multi-harmonic timbre (fundamental, 2nd
   at −12 dB, 3rd at −18 dB), notes of at least 80 ms, 100 ms gaps, a
   distinct rhythm per earcon and a narrow loudness range. They play at the
   user's volume and keep quiet in quiet hours or Do Not Disturb, unless they
   answer something monsieur just did. */
import { bus, settings, state } from "./core.js";

let audioCtx = null, wave = null, noise = null;

export function audio() {
  // One shared context: browsers cap how many can exist, and reconnects add up.
  if (!audioCtx) audioCtx = new AudioContext();
  // 'interrupted': Safari's state after a call or Siri took the sound.
  if (audioCtx.state === "suspended" || audioCtx.state === "interrupted") audioCtx.resume().catch(() => {});
  return audioCtx;
}

/* Called inside a tap (hud.js, the orb): iOS starts an AudioContext only from
   a user's gesture, and one created later stays silent. Never throws. */
export function unlock() {
  try { audio(); } catch { /* no Web Audio: the earcons stay silent */ }
}

const GAP = 0.1;     // seconds between notes
const LEVEL = 0.09;  // peak gain at full volume (about −21 dBFS)

/* Notes: f in Hz, d in seconds (≥ 0.08), g = gain relative to LEVEL;
   noise = a band-passed noise burst instead of a tone. */
export const EARCONS = {
  wake: [{ f: 660, d: 0.09 }, { f: 880, d: 0.09 }],                        // two quick rising notes
  online: [{ f: 523, d: 0.1 }, { f: 659, d: 0.1 }, { f: 784, d: 0.14 }],    // three rising notes
  sleep: [{ f: 784, d: 0.22 }, { f: 523, d: 0.34 }],                       // two slow falling notes
  alert: [{ f: 880, d: 0.32 }, { f: 660, d: 0.1 }],                        // long, then short
  error: [{ f: 330, d: 0.18 }, { f: 220, d: 0.28 }],                       // low two-tone
  tick: [{ f: 1046, d: 0.08, g: 0.45 }],                                  // monsieur stopped talking
  working: [{ f: 1318, d: 0.08, g: 0.35 }],                               // a tool runs (−30 dB, 1 Hz)
  mute: [{ f: 392, d: 0.08, g: 0.7 }],
  unmute: [{ f: 587, d: 0.08, g: 0.7 }],
  capture: [{ noise: true, d: 0.08, g: 0.8 }, { noise: true, d: 0.08, g: 0.6 }],  // shutter
};

function harmonicWave(ac) {
  if (!wave || wave.ctx !== ac) {
    // [dc, fundamental, 2nd (−12 dB), 3rd (−18 dB)]
    const imag = new Float32Array([0, 1, 0.251, 0.126]);
    wave = { ctx: ac, wave: ac.createPeriodicWave(new Float32Array(4), imag) };
  }
  return wave.wave;
}

function noiseBuffer(ac) {
  if (!noise || noise.ctx !== ac) {
    const buf = ac.createBuffer(1, Math.ceil(ac.sampleRate * 0.1), ac.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    noise = { ctx: ac, buf };
  }
  return noise.buf;
}

/* ---------------------------------------------------------- volume and quiet hours */
/* settings.earconVolume, 0..1 (a 0..100 value is accepted too). */
export function volume() {
  const v = Number(settings.get("earconVolume", 1));
  if (!Number.isFinite(v)) return 1;
  return Math.max(0, Math.min(1, v > 1 ? v / 100 : v));
}

/* '22:30-07:30' (also '22h30-7h', '22-7'); a range may cross midnight. */
export function inQuietHours(spec, now = new Date()) {
  const m = /^\s*(\d{1,2})(?:[:h](\d{2})?)?\s*[-–]\s*(\d{1,2})(?:[:h](\d{2})?)?\s*$/i.exec(String(spec || ""));
  if (!m) return false;
  const from = Number(m[1]) * 60 + Number(m[2] || 0), to = Number(m[3]) * 60 + Number(m[4] || 0);
  const cur = now.getHours() * 60 + now.getMinutes();
  if (from === to) return false;
  return from < to ? cur >= from && cur < to : cur >= from || cur < to;
}

function dndUntil() {
  const raw = settings.get("dndUntil", 0);
  const t = typeof raw === "string" && !/^\d+$/.test(raw) ? Date.parse(raw) : Number(raw);
  if (!Number.isFinite(t) || t <= 0) return 0;
  return t < 1e12 ? t * 1000 : t;  // seconds or milliseconds
}

/* Quiet hours (server config) or Do Not Disturb (the state overlay or the setting). */
export function isQuiet(now = new Date()) {
  if (state.quiet === true) return true;
  if (dndUntil() > now.getTime()) return true;
  const cfg = state.config || {};
  return inQuietHours(cfg.quiet_hours ?? cfg.QUIET_HOURS, now);
}

/* During a conversation every earcon answers something monsieur is doing;
   outside one, only explicit feedback ({userInitiated: true}) is. */
function inConversation() {
  return state.mode === "live" || state.mode === "connecting";
}

/* ---------------------------------------------------------- the microphone */
/* Outside a conversation an earcon from the speakers could trigger the wake
   word or the voice detector: registered microphone tracks are muted while
   it plays. voice.js, wake.js or the onboarding can register theirs. */
const micTracks = new Set();

export function registerMic(source) {
  const tracks = source?.getAudioTracks ? source.getAudioTracks() : source ? [source] : [];
  for (const track of tracks) {
    micTracks.add(track);
    track.addEventListener?.("ended", () => micTracks.delete(track));
  }
  return () => tracks.forEach(track => micTracks.delete(track));
}

let duckTimer = 0;
const ducked = new Set();

function duckMic(ms) {
  if (state.mode === "live") return;
  for (const track of micTracks) {
    if (track.readyState === "ended") { micTracks.delete(track); continue; }
    if (track.enabled) { track.enabled = false; ducked.add(track); }
  }
  if (!ducked.size) return;
  clearTimeout(duckTimer);
  duckTimer = setTimeout(() => {
    for (const track of ducked) if (!state.muted && track.readyState !== "ended") track.enabled = true;
    ducked.clear();
  }, ms);
}

/* ---------------------------------------------------------- earcons */
/* earcon(kind, {userInitiated, soft}): true when it played. soft: half as
   loud (delivery.js: a reminder during quiet hours chimes discreetly). */
export function earcon(kind, { userInitiated, soft = false } = {}) {
  const notes = EARCONS[kind];
  if (!notes) return false;
  const user = userInitiated ?? inConversation();
  if (!user && isQuiet()) return false;
  const vol = volume() * (soft ? 0.5 : 1);
  if (vol <= 0) return false;
  try {
    const ac = audio();
    let at = ac.currentTime + 0.02;
    for (const n of notes) {
      const peak = LEVEL * vol * (n.g ?? 1);
      const env = ac.createGain();
      env.gain.setValueAtTime(0.0001, at);
      env.gain.exponentialRampToValueAtTime(peak, at + 0.012);
      env.gain.setValueAtTime(peak, at + n.d * 0.6);
      env.gain.exponentialRampToValueAtTime(0.0001, at + n.d);
      env.connect(ac.destination);
      let src;
      if (n.noise) {
        src = ac.createBufferSource();
        src.buffer = noiseBuffer(ac);
        const band = ac.createBiquadFilter();
        band.type = "bandpass"; band.frequency.value = 3000; band.Q.value = 0.8;
        src.connect(band).connect(env);
      } else {
        src = ac.createOscillator();
        src.setPeriodicWave(harmonicWave(ac));
        src.frequency.value = n.f;
        src.connect(env);
      }
      src.start(at);
      src.stop(at + n.d + 0.01);
      at += n.d + GAP;
    }
    const ms = Math.ceil((at - GAP - ac.currentTime) * 1000) + 40;
    duckMic(ms);
    bus.emit("earcon", { kind, ms });
    return true;
  } catch {
    return false;  // audio not available yet
  }
}

/* ---------------------------------------------------------- in step with the phases */
let phase = null, workingTimer = 0;
let alertedFor = "";  // the confirmations already announced (confirm.js: state.confirming)

function stopWorking() {
  clearInterval(workingTimer);
  workingTimer = 0;
}

function onPhase({ phase: next = null } = {}) {
  const prev = phase;
  phase = next;
  if (next === prev) return;
  if (next !== "tool") stopWorking();
  if (next === "thinking" && prev === "user") earcon("tick");      // speech_stopped
  else if (next === "confirm") {                                    // once per request
    const key = (state.confirming || []).join(",");
    if (!key || key !== alertedFor) earcon("alert");
    alertedFor = key;
  }
  else if (next === "tool") {
    // a soft tick each second, only if the tool takes more than a second
    workingTimer = setInterval(() => { if (phase === "tool") earcon("working"); else stopWorking(); }, 1000);
  }
}

export function init() {
  bus.on("phase", onPhase);
  bus.on("mode", () => { phase = null; stopWorking(); });
  let wasMuted = !!state.muted;
  bus.on("muted", ({ muted } = {}) => {
    if (!!muted === wasMuted) return;  // a repeated state is not a click
    wasMuted = !!muted;
    earcon(muted ? "mute" : "unmute", { userInitiated: true });
  });
  bus.on("tool:start", ({ name = "" } = {}) => {
    if (name === "look_at_screen" || name === "look_at_camera") earcon("capture");
  });
}
