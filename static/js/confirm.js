/* Confirmation cards for risky actions waiting for monsieur's "oui" (design
   spec §3). The server holds the pending actions (jarvis/confirm.py): a card
   appears when a tool answers needs_confirmation or the server pushes
   'pending', and [Lancer] / [Annuler] post /api/pending/{id}/decide; closing
   the card (✕) answers « non ». Locking the PC goes through a 3-second
   countdown card first, cancelled by Échap (keys.js calls cancelLock), its
   button, its ✕, « Interrompre », or monsieur starting to speak. */
import { api, bus, state } from "./core.js";
import * as fx from "./audio-fx.js";
import * as hud from "./hud.js";
import * as strings from "./strings-fr.js";
import { isLive, sendNotice } from "./voice.js";

// What is shown comes from strings-fr.js (T.confirm); what is said to the
// voice model or answered for a tool stays here, in plain text.
const MODEL = {
  lockRefused: "Verrouillage annulé par monsieur.",
  noticeDone: (what, result) => `Monsieur a confirmé à l'écran : ${what} Résultat : ${result}.`,
  noticeCancelled: (what) => `Monsieur a annulé à l'écran : ${what} Rien n'a été fait.`,
};
const C = { ...strings.T.confirm, ...MODEL };
const CONFIRM_STATUS = strings.T.status.confirm;

const open = new Map();     // pending id -> { el, extra, detail, countdown, deadline, timer, summary }
const settled = new Set();  // ids already closed here: late echoes are ignored
let lock = null;            // the lock-screen countdown, while it runs

const cardId = (id) => `confirm-${id}`;

/* ---------------------------------------------------------- pending actions */
export function showPending(p) {
  if (!p || !p.id) return;
  if (p.state && p.state !== "pending") { settle(p); return; }
  const known = open.get(p.id);
  if (known) { setDetail(known, p.detail); return; }
  if (settled.has(p.id)) return;
  const el = hud.addCard(C.title, p.summary || "", "confirm", {
    id: cardId(p.id), sticky: true,
    actions: [{ label: C.run, primary: true, onClick: () => decide(p.id, "oui") },
              { label: C.cancel, onClick: () => decide(p.id, "non") }],
  });
  el.dataset.pending = p.id;
  el.dataset.state = "pending";
  // Closing the card answers "non": a request never stays pending without its
  // card (hud.js removes it first, decide() then shows the outcome).
  const x = el.querySelector(".x");
  x.setAttribute("aria-label", C.dismiss);
  x.addEventListener("click", () => decide(p.id, "non"));
  // What exactly will run (the prompt, the link...), as plain text, and the countdown.
  const extra = document.createElement("div");
  extra.className = "confirm-extra";
  const detail = document.createElement("p");
  detail.className = "confirm-detail";
  const countdown = document.createElement("p");
  countdown.className = "confirm-countdown";
  extra.append(detail, countdown);
  placeExtra(el, extra);
  const entry = { el, extra, detail, countdown, summary: p.summary || "",
                  deadline: Date.now() + 1000 * (Number.isFinite(p.expires_in) ? p.expires_in : 90) };
  setDetail(entry, p.detail);
  open.set(p.id, entry);
  tick(p.id);
  entry.timer = setInterval(() => tick(p.id), 1000);
  hud.announce(`${C.title} : ${entry.summary}`, { urgent: true });
  // Live, the 'confirm' phase plays the alert (audio-fx.js); otherwise it is ours.
  if (state.mode !== "live") fx.earcon?.("alert");
  updatePhase();
}

function placeExtra(el, extra) {
  const bar = el.querySelector(".actions");
  if (bar) bar.before(extra); else el.append(extra);
}

function setDetail(entry, text) {
  if (!text || entry.detail.textContent) return;
  entry.detail.textContent = text;
}

function tick(id) {
  const entry = open.get(id);
  if (!entry) return;
  const left = Math.ceil((entry.deadline - Date.now()) / 1000);
  if (left <= 0) settle({ id, state: "expired" });
  else entry.countdown.textContent = C.expiresIn(left);
}

async function decide(id, decision) {
  const entry = open.get(id);
  if (!entry || entry.busy) return;
  entry.busy = true;
  entry.el.querySelectorAll(".actions button").forEach((b) => { b.disabled = true; });
  let out;
  try {
    out = await api(`/api/pending/${encodeURIComponent(id)}/decide`,
                    { method: "POST", body: { decision } });
  } catch (err) {
    out = { ok: false, error: err.message };
  }
  const outcome = out.state || (out.ok ? "done" : "error");
  settle({ id, state: outcome, result: out.result, error: out.error });
  // The voice model asked the question: tell it the answer came from the screen.
  if (!isLive()) return;
  if (outcome === "done") sendNotice(C.noticeDone(entry.summary, resultText(out.result)));
  else if (outcome === "cancelled") sendNotice(C.noticeCancelled(entry.summary));
  else if (outcome === "error") {
    sendNotice(C.noticeDone(entry.summary, resultText({ ok: false, error: out.error || out.result?.error })));
  }
}

function resultText(result = {}) {
  if (result.task_id) return `tâche lancée (${result.task_id})`;
  if (result.ok === false) return `échec, ${result.error || "erreur"}`;
  if (result.scheduled) return `programmé, ${result.scheduled}`;
  return "fait";
}

function settle(p) {
  const entry = open.get(p.id);
  if (!entry) return;
  open.delete(p.id);
  settled.add(p.id);
  if (settled.size > 200) settled.delete(settled.values().next().value);
  clearInterval(entry.timer);
  entry.extra.remove();
  const reason = p.error || p.result?.error || "erreur";
  const text = { done: C.done, cancelled: C.cancelled, expired: C.expired }[p.state] || C.failed(reason);
  // The outcome first, then what it was about.
  const el = hud.addCard(C.title, `**${text}**\n\n${entry.summary}`, p.state === "done" ? "result" : "info",
                         { id: cardId(p.id), ttlMs: 8000 });
  el.dataset.state = p.state || "error";
  updatePhase();
}

/* While live, a question waiting on screen is the 'confirm' phase; voice.js
   comes back to it after each turn (state.confirming). */
function updatePhase() {
  state.confirming = [...open.keys()];
  if (open.size && state.mode === "live") {
    if (state.phase === "confirm") return;
    state.phase = "confirm";
    bus.emit("phase", { phase: "confirm", label: CONFIRM_STATUS });
  } else if (state.phase === "confirm") {
    state.phase = state.mode === "live" ? "listening" : null;
    bus.emit("phase", { phase: state.phase, label: "" });
  }
}

/* ---------------------------------------------------------- lock screen */
/* Resolves to null to go ahead, or to the tool's answer when monsieur
   cancelled (Échap, the button, the card's ✕, « Interrompre », his voice). */
export function lockCountdown(seconds = 3) {
  if (lock) return lock.promise;
  let left = seconds;
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  const id = "lock-countdown";
  const me = { promise, cancel: () => finish(true), timer: null };
  const el = hud.addCard(C.lockTitle, C.lockBody, "confirm", {
    id, sticky: true, actions: [{ label: C.cancel, onClick: () => finish(true) }],
  });
  el.dataset.state = "pending";
  const line = document.createElement("p");
  line.className = "confirm-countdown";
  line.textContent = C.lock(left);
  placeExtra(el, line);
  // Closing the card cancels too: the PC never locks behind a dismissed card.
  const x = el.querySelector(".x");
  x.setAttribute("aria-label", C.lockCancel);
  x.addEventListener("click", () => finish(true));
  // Only this countdown: the card element may be reused by the next one, and
  // a stale listener must never settle (or orphan) a newer lock.
  function finish(cancelled) {
    if (lock !== me) return;
    clearInterval(me.timer);
    lock = null;
    line.remove();
    if (cancelled) {
      hud.addCard(C.lockTitle, C.lockCancelled, "info", { id, ttlMs: 4000 }).dataset.state = "cancelled";
      resolve({ ok: false, cancelled: true, error: C.lockRefused });
    } else {
      hud.removeCard(id);
      resolve(null);
    }
  }
  me.timer = setInterval(() => {
    left -= 1;
    if (left <= 0) finish(false);
    else line.textContent = C.lock(left);
  }, 1000);
  lock = me;
  hud.announce(C.lock(left), { urgent: true });
  return promise;
}

/* Échap during the countdown (keys.js asks first): true when it cancelled one. */
export function cancelLock() {
  if (!lock) return false;
  lock.cancel();
  return true;
}

export function init() {
  bus.on("tool:result", ({ result } = {}) => {
    if (result?.status === "needs_confirmation" && result.pending_id) {
      showPending({ id: result.pending_id, summary: result.summary, expires_in: result.expires_in });
    }
  });
  bus.on("server:pending", (ev) => showPending(ev?.pending));
  bus.on("mode", updatePhase);
  // « Interrompre » (voice.interrupt) and monsieur speaking during the
  // countdown (« non, attends ! ») stop the lock, like Échap.
  bus.on("interrupt", cancelLock);
  bus.on("phase", (p) => { if (p && p.phase === "user") cancelLock(); });
  // voice.runTool asks before running a tool: locking waits for the countdown.
  bus.on("tool:intercept", ({ name, args, hold } = {}) => {
    if (name === "system_control" && args?.action === "lock_screen") hold?.(lockCountdown());
  });
  // Requests still open (page reloaded, or asked while this page was closed).
  api("/api/pending").then((items) => items.forEach(showPending)).catch((err) => console.warn(err));
}
