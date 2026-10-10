/* Owned by package B2 (JARVIS on iPhone): what the paired iPhone's page (and
   any page on iOS) needs beyond the PC's (spec 7).
   - The screen stays on while a conversation is live (Screen Wake Lock),
     released otherwise, asked again when the page comes back.
   - A locked screen or another app pauses the conversation: iOS takes the
     microphone away anyway, and a session left open would bill for nothing.
     Coming back resumes it; when the microphone is refused then (iOS asks
     again after a while) or the pause was long, [Reprendre] reopens it with
     a tap.
   - The microphone taken by a call or Siri (voice.js: mic:interrupted) is
     said in the same banner until iOS gives it back. */
import { $, bus, isIOS, state } from "./core.js";
import { unlock } from "./audio-fx.js";
import { clearError } from "./hud.js";
import { connect, primeAudio, sleep } from "./voice.js";
import { T } from "./strings-fr.js";

let lock = null;           // the WakeLockSentinel while live
let asking = null;         // a wake lock request on its way
let pausedAt = 0;          // when this page paused a live conversation (hidden)
let banner = null, bannerText = null, resumeBtn = null;

const applies = () => state.remote || isIOS();
const hidden = () => document.visibilityState === "hidden" || document.hidden === true;
const awake = () => state.mode === "live" || state.mode === "connecting";

/* ---------------------------------------------------------- the screen stays on */
async function holdScreen() {
  if (lock || asking || state.mode !== "live" || hidden() || !navigator.wakeLock?.request) return;
  try {
    asking = navigator.wakeLock.request("screen");
    const sentinel = await asking;
    if (state.mode !== "live" || hidden()) { sentinel.release?.()?.catch?.(() => {}); return; }  // ended meanwhile
    lock = sentinel;
    // iOS lets it go by itself when the page is hidden: asked again on 'visible'.
    sentinel.addEventListener?.("release", () => { if (lock === sentinel) lock = null; });
  } catch {
    /* Low Power Mode or refused: the screen may go to sleep, the conversation then pauses */
  } finally {
    asking = null;
  }
}

function releaseScreen() {
  const sentinel = lock;
  lock = null;
  if (sentinel) sentinel.release?.()?.catch?.(() => {});
}

/* ---------------------------------------------------------- the banner */
function ensureBanner() {
  if (banner) return;
  banner = document.createElement("div");
  banner.id = "iosBanner";
  banner.className = "ios-banner";
  banner.setAttribute("role", "status");
  banner.hidden = true;
  bannerText = document.createElement("p");
  bannerText.className = "ios-banner-text";
  resumeBtn = document.createElement("button");
  resumeBtn.type = "button";
  resumeBtn.className = "ctl primary";
  resumeBtn.textContent = T.ios.resume;
  resumeBtn.addEventListener("click", resumeByTap);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "x";
  close.setAttribute("aria-label", T.ios.dismiss);
  close.innerHTML = '<span aria-hidden="true">✕</span>';
  close.addEventListener("click", () => { pausedAt = 0; hideBanner(); });
  const actions = document.createElement("div");
  actions.className = "ios-banner-actions";
  actions.append(resumeBtn, close);
  banner.append(bannerText, actions);
  // Under the status line, in the stage's flow: never over the orb.
  const row = document.querySelector("#stage .status-row");
  if (row) row.after(banner); else $("stage")?.append(banner);
}

/* kind: "paused" (the conversation) or "mic" (iOS took the microphone). */
function showBanner(kind, text, { resume = false } = {}) {
  ensureBanner();
  banner.dataset.kind = kind;
  bannerText.textContent = text;
  resumeBtn.hidden = !resume;
  banner.hidden = false;
}

function hideBanner(kind) {
  if (!banner || (kind && banner.dataset.kind !== kind)) return;
  banner.hidden = true;
  delete banner.dataset.kind;
}

/* ---------------------------------------------------------- hidden, then back */
function onHidden() {
  releaseScreen();
  if (!awake()) return;
  pausedAt = Date.now();
  sleep();
  showBanner("paused", T.ios.paused, { resume: true });
}

async function onVisible() {
  holdScreen();
  if (!pausedAt) return;
  const away = Date.now() - pausedAt;
  pausedAt = 0;
  // Away longer than the idle timeout: nobody is waiting for an answer, so no
  // new paid session opens by itself; the banner offers it.
  const limit = (Number(state.config?.idle_minutes) || 0) * 60e3;
  if (limit > 0 && away > limit) { showBanner("paused", T.ios.paused, { resume: true }); return; }
  showBanner("paused", T.ios.paused);
  await connect({ reconnect: true });
  // On its way (or retrying): the banner goes once live. Refused (the
  // microphone, most often): a tap is what iOS wants to open it again.
  if (!state.wantLive && !awake()) showBanner("paused", T.ios.paused, { resume: true });
}

/* [Reprendre]: a tap, like the orb's (the sound unlocked inside it); the
   recent exchanges still carry over to the new session (voice.js). */
function resumeByTap() {
  pausedAt = 0;
  hideBanner();
  clearError();  // the refused reconnection's message: this tap retries it
  unlock();
  primeAudio();
  connect();
}

export function init() {
  if (!applies()) return;
  document.addEventListener("visibilitychange", () => { if (hidden()) onHidden(); else onVisible(); });
  bus.on("mode", ({ mode } = {}) => {
    if (mode === "live") {
      holdScreen();
      hideBanner("paused");
    } else {
      releaseScreen();
      if (mode === "connecting") return;
      hideBanner("mic");
      // The automatic resume gave up (its retries ran out): a tap may still do it.
      if (banner && !banner.hidden && banner.dataset.kind === "paused") resumeBtn.hidden = false;
    }
  });
  bus.on("mic:interrupted", ({ interrupted } = {}) => {
    if (interrupted && awake()) showBanner("mic", T.ios.micInterrupted);
    else hideBanner("mic");
  });
}
