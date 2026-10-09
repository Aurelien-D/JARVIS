/* Sound: the one shared AudioContext and the short earcons that mark
   JARVIS's state changes (wake, online, standby, alert, error). */

let audioCtx = null;

export function audio() {
  // One shared context: browsers cap how many can exist, and reconnects add up.
  if (!audioCtx) audioCtx = new AudioContext();
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  return audioCtx;
}

export const EARCONS = { wake: [660, 880], online: [523, 659, 784], sleep: [784, 523], alert: [880, 660, 880], error: [330, 220] };

export function earcon(kind) {
  try {
    const ac = audio();
    let at = ac.currentTime + 0.02;
    for (const f of EARCONS[kind] || []) {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(0.09, at + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, at + 0.13);
      o.connect(g).connect(ac.destination);
      o.start(at); o.stop(at + 0.14);
      at += 0.11;
    }
  } catch { /* audio not available yet */ }
}

export function init() {}
