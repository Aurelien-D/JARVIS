/* The orb: an arc-reactor HUD drawn on a canvas, driven by the mode and by
   the microphone and voice levels. */
import { $, state, touch } from "./core.js";
import { analyserIn, analyserOut } from "./voice.js";

let orb = null, ctx = null;

export function sizeOrb() {
  const r = orb.getBoundingClientRect();
  orb.width = r.width * devicePixelRatio; orb.height = r.height * devicePixelRatio;
}

export function level(analyser) {
  if (!analyser) return 0;
  const buf = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(buf);
  let s = 0; for (const v of buf) s += v;
  return s / buf.length / 255;
}

const TAU = Math.PI * 2;
let t = 0;
export function draw() {
  requestAnimationFrame(draw);
  t += 0.016;
  const w = orb.width, h = orb.height, cx = w / 2, cy = h / 2;
  const dpr = devicePixelRatio;
  ctx.clearRect(0, 0, w, h);
  const inL = level(analyserIn), outL = level(analyserOut);
  if (outL > 0.02) touch(); // JARVIS speaking counts as activity
  // standby breathes slowly; offline is dimmer
  const idle = state.mode === "standby" ? 0.05 + 0.04 * Math.sin(t * 1.4) : 0;
  const energy = Math.max(inL, outL * 1.3, idle);
  const glow = state.mode === "live" ? 1 : state.mode === "off" ? 0.55 : 0.8;
  const base = Math.min(w, h) * 0.20;
  const R = base * (1 + 0.14 * energy + 0.012 * Math.sin(t * 2.1));
  const CYAN = "64,220,255", AMBER = "255,179,71";

  // outer glow
  let g = ctx.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 2.6);
  g.addColorStop(0, `rgba(${CYAN},${(0.16 + energy * 0.3) * glow})`);
  g.addColorStop(1, "transparent");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 2.6, 0, TAU); ctx.fill();

  // segmented outer ring (arc reactor coils)
  const segs = 12;
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(t * (state.mode === "connecting" ? 1.2 : 0.25));
  for (let i = 0; i < segs; i++) {
    const a0 = (i / segs) * TAU, a1 = a0 + TAU / segs * 0.62;
    ctx.strokeStyle = `rgba(${CYAN},${(0.55 + 0.4 * Math.sin(t * 3 + i)) * glow})`;
    ctx.lineWidth = 7 * dpr;
    ctx.beginPath(); ctx.arc(0, 0, R * 1.55, a0, a1); ctx.stroke();
  }
  ctx.restore();

  // tick ring (graduations)
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(-t * 0.1);
  for (let i = 0; i < 60; i++) {
    const a = (i / 60) * TAU, big = i % 5 === 0;
    ctx.strokeStyle = `rgba(${big ? AMBER : CYAN},${big ? .8 : .3})`;
    ctx.lineWidth = (big ? 2 : 1) * dpr;
    const r0 = R * 1.78, r1 = R * (big ? 1.88 : 1.83);
    ctx.beginPath();
    ctx.moveTo(r0 * Math.cos(a), r0 * Math.sin(a));
    ctx.lineTo(r1 * Math.cos(a), r1 * Math.sin(a));
    ctx.stroke();
  }
  ctx.restore();

  // orbiting reticle arcs
  for (let i = 0; i < 3; i++) {
    ctx.save(); ctx.translate(cx, cy);
    ctx.rotate(t * (0.5 + i * 0.3) * (i % 2 ? -1 : 1));
    ctx.strokeStyle = `rgba(${i === 1 ? AMBER : CYAN},${0.5 + energy * 0.4})`;
    ctx.lineWidth = 2.2 * dpr;
    ctx.beginPath(); ctx.arc(0, 0, R * (1.18 + i * 0.13), 0, TAU * (0.16 + 0.1 * i)); ctx.stroke();
    ctx.restore();
  }

  // triangular core frame (arc reactor MK)
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(-t * 0.06);
  ctx.strokeStyle = `rgba(${CYAN},.45)`; ctx.lineWidth = 1.5 * dpr;
  ctx.beginPath();
  for (let i = 0; i <= 3; i++) {
    const a = (i / 3) * TAU - Math.PI / 2;
    const x = R * 1.02 * Math.cos(a), y = R * 1.02 * Math.sin(a);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke(); ctx.restore();

  // core
  g = ctx.createRadialGradient(cx, cy, R * 0.05, cx, cy, R);
  g.addColorStop(0, "rgba(235,252,255,.98)");
  g.addColorStop(0.4, `rgba(${CYAN},${0.9 + energy * 0.1})`);
  g.addColorStop(1, "rgba(8,30,60,.95)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 0.92, 0, TAU); ctx.fill();

  // waveform ring when speaking
  if (Math.max(inL, outL) > 0.02) {
    ctx.strokeStyle = `rgba(255,255,255,${0.3 + energy * 0.5})`;
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    for (let a = 0; a <= TAU; a += 0.05) {
      const rr = R * (1.0 + 0.07 * energy * Math.sin(a * 9 + t * 6));
      const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a);
      a === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath(); ctx.stroke();
  }
}

export function init() {
  orb = $("orb");
  ctx = orb.getContext("2d");
  window.addEventListener("resize", sizeOrb); sizeOrb();
  draw();
}
