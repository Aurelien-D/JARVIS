/* The orb (design spec §4, §7): an arc-reactor HUD drawn on a canvas. Each
   state has its own look, computed by orbParams() (a pure function of the
   state and the time, so 60 Hz and 144 Hz screens animate alike), and the
   loop is frugal: about 20 fps outside a conversation, nothing while the
   page is hidden, and one still frame once nothing moves. Every glow is
   drawn here: a CSS drop-shadow on the canvas cost more than half a core. */
import { $, bus, settings, state, touch } from "./core.js";
import { analyserIn, analyserOut } from "./voice.js";

const TAU = Math.PI * 2;
const C = {
  off: [120, 140, 160],        // --off
  offCore: [52, 64, 78],
  standby: [30, 126, 163],     // --orb-standby
  cyan: [64, 220, 255],        // --cyan
  amber: [255, 179, 71],       // --amber
  speak: [232, 246, 255],      // --speak
  err: [255, 107, 129],        // --err
};
const BASE_TURNS = 0.04;       // the coil ring while live, in turns per second
const FAST_TURNS = 1 / 1.2;    // connecting ring and thinking arcs: one turn per 1.2 s
const BREATH_HZ = 0.25;        // standby breathing (±4 % radius)
const ERROR_S = 2;             // error overlay: a 1 Hz red pulse, twice
const FLASH_S = 0.15;          // capture flash

const pal = (ring, core = ring, accent = ring) => ({ ring, core, accent });

/* Everything the frame shows, from the state alone:
   orbParams(mode, phase, {muted, errorAt, captureAt, tasks, countdown}, tSeconds,
             inLevel, outLevel, reduced)
   -> {name, palette, rotation, radius, glow, arcs, segments, waveform, fill,
       sweep, satellites, countdown, mutedBar, alpha, error, flash, animated, transient}
   errorAt and captureAt are on the same clock as tSeconds. radius is a
   fraction of the canvas size; angles are in radians. */
export function orbParams(mode, phase, overlays = {}, t = 0, inLevel = 0, outLevel = 0, reduced = false) {
  const o = overlays || {};
  const name = mode === "live" ? phase || "listening" : mode || "off";
  const spin = (turns) => (reduced ? 0 : (t * turns * TAU) % TAU);
  const inL = clamp01(inLevel), outL = clamp01(outLevel);
  const breathe = reduced ? 0 : Math.sin(TAU * BREATH_HZ * t);
  const p = {
    name, palette: pal(C.cyan), rotation: 0, radius: 0.2, glow: 0.5, arcs: [], segments: null,
    waveform: null, fill: 0, sweep: null, satellites: [], countdown: null, mutedBar: false,
    alpha: 1, error: 0, flash: 0, animated: !reduced, transient: false,
  };
  switch (name) {
    case "off":  // grey and still
      p.palette = pal(C.off, C.offCore, C.off); p.glow = 0; p.animated = false;
      break;
    case "standby":  // slow breathing, barely turning
      p.palette = pal(C.standby, C.standby, C.standby);
      p.radius = 0.2 * (1 + 0.04 * breathe);
      p.glow = 0.3 + 0.05 * breathe;
      p.rotation = spin(BASE_TURNS * 0.1);
      break;
    case "connecting":  // a segmented ring, one turn per 1.2 s
      p.rotation = spin(FAST_TURNS);
      p.segments = { n: 12, duty: 0.45 };
      p.glow = 0.45;
      break;
    case "user":  // monsieur speaks: the core fills with his voice
      p.fill = 0.25 + 0.75 * inL;
      // fall through
    case "listening":  // the ring follows the microphone only
      p.radius = 0.2 * (1 + 0.14 * inL);
      p.glow = 0.5 + 0.5 * inL;
      p.rotation = spin(BASE_TURNS);
      break;
    case "tool":  // amber arcs plus a sweep
      p.sweep = spin(0.5);
      // fall through
    case "thinking":
      p.palette = pal(C.amber, C.amber, C.amber);
      p.rotation = spin(BASE_TURNS);
      p.glow = 0.5;
      p.arcs = [0, 1, 2].map(i => ({
        k: 1.18 + 0.13 * i, len: TAU * (0.16 + 0.1 * i), width: 2.4,
        start: spin(FAST_TURNS) * (i % 2 ? -1 : 1) + i * 2.1,
      }));
      break;
    case "speaking":  // a white waveform from JARVIS's voice only
      p.palette = pal(C.speak, C.speak, C.cyan);
      p.radius = 0.2 * (1 + 0.05 * outL);
      p.glow = 0.5 + 0.5 * outL;
      p.rotation = spin(BASE_TURNS);
      p.waveform = reduced ? null : { amp: 0.12 * outL, lobes: 9, phase: (t * 6) % TAU };
      break;
    case "confirm":  // a steady amber ring: waiting for monsieur
      p.palette = pal(C.amber, C.amber, C.amber);
      p.glow = 0.55;
      p.animated = false;
      break;
    default:
      break;
  }
  // overlays
  if (o.muted) { p.alpha = 0.5; p.mutedBar = true; }
  const errAge = Number.isFinite(o.errorAt) ? t - o.errorAt : -1;
  if (errAge >= 0 && errAge < ERROR_S) {
    p.error = reduced ? 1 : 0.5 - 0.5 * Math.cos(TAU * errAge);  // 1 Hz: well under 3 flashes/s
    p.transient = true;
  }
  const flashAge = Number.isFinite(o.captureAt) ? t - o.captureAt : -1;
  if (flashAge >= 0 && flashAge < FLASH_S) {
    p.flash = reduced ? 0 : 1 - flashAge / FLASH_S;
    p.transient = !reduced || p.transient;
  }
  const n = Math.max(0, Math.min(3, Math.floor(o.tasks || 0)));
  for (let i = 0; i < n; i++) {
    p.satellites.push({ angle: (reduced ? -Math.PI / 2 : spin(0.12)) + (i * TAU) / n });
  }
  if (n && !reduced) p.animated = true;
  if (Number.isFinite(o.countdown) && o.countdown !== null) p.countdown = clamp01(o.countdown);
  return p;
}

function clamp01(x) { return Math.max(0, Math.min(1, Number(x) || 0)); }
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
const mix = (a, b, k) => a.map((v, i) => Math.round(v + (b[i] - v) * k));

/* ---------------------------------------------------------- audio levels */
// Preallocated: the analysers' 128 bins, one buffer each (no garbage per frame).
const buffers = [new Uint8Array(128), new Uint8Array(128)];

export function level(analyser, slot = 1) {
  if (!analyser) return 0;
  const n = analyser.frequencyBinCount;
  if (buffers[slot].length !== n) buffers[slot] = new Uint8Array(n);
  const buf = buffers[slot];
  analyser.getByteFrequencyData(buf);
  let s = 0;
  for (let i = 0; i < n; i++) s += buf[i];
  return n ? s / n / 255 : 0;
}

/* ---------------------------------------------------------- rendering */
let canvas = null, ctx = null;

function render(p) {
  const w = canvas.width, h = canvas.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = 1;
  ctx.clearRect(0, 0, w, h);
  if (!w || !h) return;
  const S = Math.min(w, h), u = S / 440, R = p.radius * S;
  const ring = p.error ? mix(p.palette.ring, C.err, p.error) : p.palette.ring;
  const core = p.error ? mix(p.palette.core, C.err, p.error * 0.6) : p.palette.core;
  const accent = p.error ? ring : p.palette.accent;
  ctx.translate(w / 2, h / 2);
  ctx.globalAlpha = p.alpha;

  // glow (in the canvas, not a CSS filter)
  const glow = Math.max(p.glow, p.error * 0.8);
  if (glow > 0.01) {
    const g = ctx.createRadialGradient(0, 0, R * 0.3, 0, 0, S * 0.5);
    g.addColorStop(0, rgba(ring, 0.3 * glow));
    g.addColorStop(1, rgba(ring, 0));
    ctx.fillStyle = g;
    ctx.fillRect(-w / 2, -h / 2, w, h);
  }

  // coil ring: 12 segments, one stroke
  const segs = 12, duty = p.segments ? p.segments.duty : 0.62, rc = R * 1.55;
  ctx.beginPath();
  for (let i = 0; i < segs; i++) {
    const a0 = p.rotation + (i / segs) * TAU, a1 = a0 + (TAU / segs) * duty;
    ctx.moveTo(rc * Math.cos(a0), rc * Math.sin(a0));
    ctx.arc(0, 0, rc, a0, a1);
  }
  ctx.strokeStyle = rgba(ring, p.name === "off" ? 0.55 : 0.4 + 0.5 * Math.min(1, glow + (p.segments ? 0.3 : 0)));
  ctx.lineWidth = 7 * u;
  ctx.stroke();

  // graduations: small and big ticks, two strokes
  const tickRot = -p.rotation * 0.4;
  for (const big of [false, true]) {
    ctx.beginPath();
    for (let i = 0; i < 60; i++) {
      if ((i % 5 === 0) !== big) continue;
      const a = tickRot + (i / 60) * TAU, r0 = R * 1.78, r1 = R * (big ? 1.9 : 1.84);
      ctx.moveTo(r0 * Math.cos(a), r0 * Math.sin(a));
      ctx.lineTo(r1 * Math.cos(a), r1 * Math.sin(a));
    }
    ctx.strokeStyle = rgba(big ? accent : ring, big ? 0.8 : 0.35);
    ctx.lineWidth = (big ? 2 : 1) * u * 1.4;
    ctx.stroke();
  }

  // thinking/tool arcs and the tool sweep
  for (const a of p.arcs) {
    ctx.beginPath();
    ctx.arc(0, 0, R * a.k, a.start, a.start + a.len);
    ctx.strokeStyle = rgba(ring, 0.85);
    ctx.lineWidth = a.width * u * 1.4;
    ctx.stroke();
  }
  if (p.sweep !== null) {
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.arc(0, 0, R * 1.5, p.sweep, p.sweep + 0.6);
    ctx.closePath();
    ctx.fillStyle = rgba(ring, 0.16);
    ctx.fill();
  }

  // core
  const g = ctx.createRadialGradient(0, 0, R * 0.05, 0, 0, R * 0.92);
  if (p.name === "off") {
    g.addColorStop(0, rgba(C.off, 0.55));
    g.addColorStop(1, rgba(core, 0.9));
  } else {
    g.addColorStop(0, "rgba(235,252,255,.96)");
    g.addColorStop(0.42, rgba(core, 0.82 + 0.18 * glow));
    g.addColorStop(1, "rgba(8,30,60,.95)");
  }
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(0, 0, R * 0.92, 0, TAU);
  ctx.fill();

  // monsieur speaking: the core fills with his voice
  if (p.fill > 0) {
    ctx.beginPath();
    ctx.arc(0, 0, R * 0.92 * p.fill, 0, TAU);
    ctx.fillStyle = rgba(C.speak, 0.35);
    ctx.fill();
  }

  // JARVIS speaking: a waveform around the core
  if (p.waveform && p.waveform.amp > 0.002) {
    const { amp, lobes, phase } = p.waveform;
    ctx.beginPath();
    for (let i = 0; i <= 96; i++) {
      const a = (i / 96) * TAU, rr = R * (1.02 + amp * Math.sin(a * lobes + phase));
      if (i) ctx.lineTo(rr * Math.cos(a), rr * Math.sin(a)); else ctx.moveTo(rr, 0);
    }
    ctx.strokeStyle = rgba(C.speak, 0.55 + 0.4 * Math.min(1, amp * 8));
    ctx.lineWidth = 2 * u * 1.4;
    ctx.stroke();
  }

  // running tasks: up to three amber satellites
  for (const s of p.satellites) {
    ctx.beginPath();
    ctx.arc(S * 0.43 * Math.cos(s.angle), S * 0.43 * Math.sin(s.angle), 6 * u, 0, TAU);
    ctx.fillStyle = rgba(C.amber, 0.95);
    ctx.fill();
  }

  // idle countdown: the time left before standby
  if (p.countdown !== null) {
    ctx.beginPath();
    ctx.arc(0, 0, S * 0.465, -Math.PI / 2, -Math.PI / 2 + TAU * p.countdown);
    ctx.strokeStyle = rgba(C.cyan, 0.8);
    ctx.lineWidth = 2.5 * u;
    ctx.stroke();
  }

  ctx.globalAlpha = 1;
  // muted: a red diagonal bar across the orb
  if (p.mutedBar) {
    ctx.beginPath();
    ctx.moveTo(-S * 0.3, S * 0.3);
    ctx.lineTo(S * 0.3, -S * 0.3);
    ctx.strokeStyle = rgba(C.err, 0.95);
    ctx.lineWidth = 8 * u;
    ctx.lineCap = "round";
    ctx.stroke();
    ctx.lineCap = "butt";
  }
  // capture: a short white flash
  if (p.flash > 0) {
    ctx.beginPath();
    ctx.arc(0, 0, S * 0.5, 0, TAU);
    ctx.fillStyle = rgba(C.speak, 0.55 * p.flash);
    ctx.fill();
  }
}

/* ---------------------------------------------------------- the loop */
export const stats = { draws: 0, running: false };
export const tuning = { unfocusedStopMs: 60e3 };  // standby without focus: stop after 60 s

const reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
export function reducedMotion() {
  return reduceQuery.matches || settings.get("motion") === "reduced"
    || document.body.classList.contains("reduce-motion");
}

let raf = 0, lastDraw = 0, forceNext = true, unfocusedSince = 0;
let lvIn = 0, lvOut = 0, lastPhase = null, errorAt = NaN, captureAt = NaN;

function currentPhase() {
  return state.mode === "live" ? state.phase || lastPhase || null : null;
}

function runningTasks() {
  let n = 0;
  for (const tk of state.tasks?.values?.() || []) if (tk && tk.status === "running") n++;
  return n;
}

/* Fraction of the last 15 s before the idle timeout (null outside it). */
function countdown(tasks) {
  const phase = currentPhase();
  if (state.mode !== "live" || (phase && phase !== "listening") || state.responseActive || tasks) return null;
  const limit = (Number(state.config?.idle_minutes) || 0) * 60e3;
  if (limit <= 0) return null;
  const left = limit - (Date.now() - (state.lastActivity || Date.now()));
  return left <= 15e3 ? Math.max(0, left / 15e3) : null;
}

/* Wake the loop: on a state change, a resize, focus or visibility. */
export function kick() {
  forceNext = true;
  if (!raf && ctx) {
    stats.running = true;
    raf = requestAnimationFrame(draw);
  }
}

export function draw(now = performance.now()) {
  raf = 0;
  if (!ctx) return;
  if (document.hidden) { stats.running = false; return; }  // visibilitychange restarts it
  const live = state.mode === "live";
  const reduced = reducedMotion();
  const gap = reduced ? 100 : live ? 0 : 50;  // live: every frame; 20 fps otherwise; 10 fps reduced
  if (!forceNext && now - lastDraw < gap - 1) {
    raf = requestAnimationFrame(draw);
    return;
  }
  forceNext = false;
  const dt = lastDraw ? Math.min(0.25, Math.max(0, now - lastDraw) / 1000) : 0;
  lastDraw = now;

  // audio levels, smoothed over time (not per frame)
  const rawIn = live ? level(analyserIn, 0) : 0, rawOut = live ? level(analyserOut, 1) : 0;
  const k = dt ? 1 - Math.exp(-dt / 0.06) : 1;
  lvIn += (rawIn - lvIn) * k;
  lvOut += (rawOut - lvOut) * k;
  if (live && rawOut > 0.02) touch();  // JARVIS speaking counts as activity

  // standby without focus for a minute: one still frame, then nothing
  if (document.hasFocus()) unfocusedSince = 0;
  else if (!unfocusedSince) unfocusedSince = now;
  const dozing = state.mode === "standby" && unfocusedSince && now - unfocusedSince >= tuning.unfocusedStopMs;

  // the frame's time: the clock errorAt and captureAt were taken on (a rAF
  // timestamp is the frame's start, a little before them)
  const t = performance.now() / 1000;
  const tasks = runningTasks();
  const p = orbParams(state.mode, currentPhase(), {
    muted: !!state.muted && (live || state.mode === "connecting"),
    errorAt, captureAt, tasks, countdown: countdown(tasks),
  }, t, lvIn, lvOut, reduced || dozing);
  render(p);
  stats.draws++;

  if (dozing || (!live && !p.animated && !p.transient)) {
    stats.running = false;  // nothing moves: the last frame stays on screen
    return;
  }
  raf = requestAnimationFrame(draw);
}

/* ---------------------------------------------------------- sizing */
/* The canvas matches the screen's pixels, also after moving to a monitor
   with another scaling (device-pixel-content-box), with a fallback. */
export function sizeOrb() {
  if (!canvas) return;
  const r = canvas.getBoundingClientRect();
  resize(Math.round(r.width * devicePixelRatio), Math.round(r.height * devicePixelRatio));
}

function resize(w, h) {
  if (!w || !h || (canvas.width === w && canvas.height === h)) return;
  canvas.width = w;
  canvas.height = h;
  kick();
}

function observeSize() {
  if (!("ResizeObserver" in window)) {
    window.addEventListener("resize", sizeOrb);
    sizeOrb();
    return;
  }
  const ro = new ResizeObserver((entries) => {
    const e = entries[entries.length - 1];
    const box = e.devicePixelContentBoxSize && e.devicePixelContentBoxSize[0];
    if (box) resize(box.inlineSize, box.blockSize);
    else resize(Math.round(e.contentRect.width * devicePixelRatio), Math.round(e.contentRect.height * devicePixelRatio));
  });
  try {
    ro.observe(canvas, { box: "device-pixel-content-box" });
  } catch {
    ro.observe(canvas);  // older engines: CSS pixels times the ratio
  }
}

export function init() {
  canvas = $("orb");
  ctx = canvas.getContext("2d");
  observeSize();
  bus.on("mode", () => { lastPhase = null; kick(); });
  bus.on("phase", ({ phase = null } = {}) => { lastPhase = phase; kick(); });
  bus.on("muted", kick);
  bus.on("server:task", kick);
  bus.on("error", () => { errorAt = performance.now() / 1000; kick(); });
  bus.on("tool:start", ({ name = "" } = {}) => {
    if (name !== "look_at_screen" && name !== "look_at_camera") return;
    captureAt = performance.now() / 1000;
    if (reducedMotion()) {  // no flash: a steady border for a moment instead
      const btn = $("orbBtn");
      btn.classList.add("capture");
      setTimeout(() => btn.classList.remove("capture"), 600);
    }
    kick();
  });
  document.addEventListener("visibilitychange", () => { unfocusedSince = 0; kick(); });
  window.addEventListener("focus", () => { unfocusedSince = 0; kick(); });
  reduceQuery.addEventListener?.("change", kick);
  // hud.js writes the state and the Animations setting on <body>
  new MutationObserver(kick).observe(document.body, { attributes: true, attributeFilter: ["class", "data-state"] });
  kick();
}
