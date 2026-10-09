/* Telling monsieur things: task results, reminders, briefings, warnings.
   - Only the leader page (picked by the server, see sse.js) speaks: two open
     pages never announce the same thing twice.
   - The server records each message in its inbox before pushing it. The
     leader acknowledges it once monsieur has really been told (said in a
     session, spoken aloud, or a reminder shown during quiet hours). What is
     left is told by the next page that opens: « Pendant votre absence : … ».
   - Quiet hours, « Ne pas déranger » and a busy screen (full screen,
     presentation) keep JARVIS silent: a reminder monsieur set still gets a
     silent notification and one soft chime; everything else waits behind the
     badge on the orb (#badge) until the next session.
   - In a session, a message waits for a pause in the conversation.
   Other modules ask through bus.emit("deliver", {text, kind, priority, spoken}). */
import { $, api, bus, settings, state, touch } from "./core.js";
import { earcon } from "./audio-fx.js";
import { addCard, announce, removeCard, toast } from "./hud.js";
import { T } from "./strings-fr.js";
import { claim, clientId } from "./sse.js";
import { connect, isLive, requestResponse, sendData, sendNotice } from "./voice.js";
import { muteWake } from "./wake.js";

const D = T.delivery || {};
const S = {
  badge: (n) => `${n} message${n > 1 ? "s" : ""} en attente · cliquez pour ${n > 1 ? "les écouter" : "l'écouter"}`,
  dnd: D.dnd || ((time) => `Ne pas déranger jusqu'à ${time}`),
  dndHour: "Ne pas déranger 1 h",
  dndEnd: "Arrêter « Ne pas déranger »",
  dndFailed: "« Ne pas déranger » n'a pas pu être enregistré : réessayez.",
  notifTitle: "Notifications",
  notifAsk: D.notifAsk || "Voulez-vous une notification Windows quand une tâche se termine ou qu'un rappel arrive ?",
  notifEnable: D.notifEnable || "Activer",
  notifLater: D.notifLater || "Plus tard",
  otherTitle: "Autre fenêtre",
  otherPage: D.otherPage || "JARVIS est actif dans une autre fenêtre.",
  useThisPage: D.useThisPage || "Utiliser celle-ci",
  warning: "Attention",
};
const FINAL = new Set(["done", "error", "interrupted"]);
const STATUS_WORD = { done: "terminée", error: "en échec", interrupted: "interrompue" };
const BUSY_PHASES = new Set(["user", "speaking", "thinking", "tool", "confirm"]);
const MAX_PENDING = 30;

let leaderId = null;      // the page that speaks; null until the server says
let leaderSeen = false;
const leaderWaiters = [];
const pending = [];       // nobody told yet: waits for the next session (the badge)
const liveQueue = [];     // in a session: waits for a pause in the conversation
const handled = new Set();  // inbox ids this page already took care of
let quietSpec = "";       // config.QUIET_HOURS, e.g. "22:30-07:30"
let dndUntil = 0;         // epoch seconds
let attention = "ok";     // desktop.attention_state() on the server
let stateLoaded = null;
let flushTimer = null;
let syncing = null;
let offered = false;
let voices = [];
let dndToggle = null;
let dndShown = null;  // the label on screen, so a refresh doesn't steal the focus

/* ---------------------------------------------------------- leader */
export function isLeader() { return !leaderId || leaderId === clientId(); }

/* Resolves once the server has said which page speaks (or after ms). */
export function leaderKnown(ms = 2000) {
  if (leaderSeen) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    leaderWaiters.push(() => { clearTimeout(timer); resolve(); });
  });
}

function onLeader(ev) {
  const before = isLeader(), first = !leaderSeen;
  leaderId = ev && ev.client ? String(ev.client) : null;
  leaderSeen = true;
  leaderWaiters.splice(0).forEach(fn => fn());
  const now = isLeader();
  if (!first && before === now) return;
  if (!now) handOver();
  renderOtherPage();
  renderBadge();
  bus.emit("delivery:leader", { leader: now, client: leaderId });
  if (now) syncInbox();
}

/* Another page speaks now: what the inbox holds is its job. */
function handOver() {
  for (const list of [pending, liveQueue]) {
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].inboxIds.length) {
        list[i].inboxIds.forEach(id => handled.delete(id));
        list.splice(i, 1);
      }
    }
  }
}

function renderOtherPage() {
  if (isLeader()) { removeCard("other-page"); return; }
  addCard(S.otherTitle, S.otherPage, "info", { id: "other-page", sticky: true,
    actions: [{ label: S.useThisPage, primary: true, onClick: () => claim() }] });
}

/* ---------------------------------------------------------- quiet hours, DND, attention */
function minutes(h, m) {
  h = Number(h); m = Number(m || 0);
  return h >= 0 && h <= 24 && m >= 0 && m <= 59 && h * 60 + m <= 1440 ? h * 60 + m : null;
}

/* Same reading as the server's inbox.quiet_hours: "22:30-07:30", "22h-7h"; empty = never. */
export function inQuietHours(spec, date = new Date()) {
  const s = String(spec || "").trim().toLowerCase();
  if (!s || ["0", "off", "non", "aucune", "none"].includes(s)) return false;
  const m = /^(\d{1,2})(?:\s*[:h]\s*(\d{2})?)?\s*(?:-|–|—|à)\s*(\d{1,2})(?:\s*[:h]\s*(\d{2})?)?$/.exec(s);
  const start = m && minutes(m[1], m[2]), end = m && minutes(m[3], m[4]);
  if (start === null || end === null || start === end) return false;
  const now = date.getHours() * 60 + date.getMinutes();
  return start < end ? now >= start && now < end : now >= start || now < end;
}

/* Nothing aloud: quiet hours, « Ne pas déranger », or a busy screen. */
export function quietNow(date = new Date()) {
  return inQuietHours(quietSpec, date) || dndUntil * 1000 > date.getTime() || attention !== "ok";
}

function updateQuiet() {
  const quiet = quietNow();
  if (state.quiet !== quiet) {
    state.quiet = quiet;
    bus.emit("quiet", { quiet });
  }
}

async function refreshState() {
  try {
    const d = await api("/api/delivery");
    quietSpec = d.quiet_hours || "";
    dndUntil = d.dnd_until || 0;
    attention = d.attention || "ok";
    settings.set("dndUntil", dndUntil * 1000);
  } catch (err) {
    console.warn(err);  // server unreachable: keep what we knew
  }
  updateQuiet();
  renderDnd();
}

function fmtTime(date) {
  const h = date.getHours(), m = date.getMinutes();
  return m ? `${h} h ${String(m).padStart(2, "0")}` : `${h} h`;
}

function renderDnd() {
  const active = dndUntil * 1000 > Date.now();
  const label = active ? S.dnd(fmtTime(new Date(dndUntil * 1000))) : "";
  if (label === dndShown) return;
  dndShown = label;
  const chip = $("dndChip");
  if (chip) {
    chip.replaceChildren();
    chip.hidden = !active;
    chip.className = active ? "chip quiet" : "chip";
    if (active) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ctl chip-btn";
      b.textContent = label;
      b.setAttribute("aria-label", `${label} · ${S.dndEnd}`);
      b.addEventListener("click", () => setDnd(false));
      chip.append(b);
    }
  }
  if (dndToggle) {
    dndToggle.textContent = active ? label : S.dndHour;
    dndToggle.setAttribute("aria-pressed", String(active));
    dndToggle.title = active ? S.dndEnd : "";
  }
}

export async function setDnd(on) {
  const until = on ? Math.round(Date.now() / 1000) + 3600 : null;
  try {
    const r = await api("/api/dnd", { method: "POST", body: { until } });
    dndUntil = r.until || 0;
  } catch (err) {
    console.warn(err);
    toast(S.dndFailed);
    return;
  }
  settings.set("dndUntil", dndUntil * 1000);
  renderDnd();
  updateQuiet();
}

function onDnd(ev) {
  dndUntil = (ev && ev.until) || 0;
  settings.set("dndUntil", dndUntil * 1000);
  renderDnd();
  updateQuiet();
}

/* ---------------------------------------------------------- delivering */
function message({ text = "", kind = "info", priority = "normal", spoken = "", notice = "", data, label = "",
                   inboxIds = [], count = 1 } = {}) {
  return { text, kind, priority, spoken, notice, data, label, inboxIds: [...inboxIds], count };
}

function ack(ids) {
  for (const id of ids || []) {
    handled.add(id);
    api(`/api/inbox/${encodeURIComponent(id)}/ack`, { method: "POST" }).catch(err => console.warn(err));
  }
}

function delivered(m, how) { bus.emit("delivered", { kind: m.kind, how, text: m.text, inboxIds: m.inboxIds }); }

function queue(m) {
  pending.push(m);
  while (pending.length > MAX_PENDING) pending.shift();
  renderBadge();
}

/* What the model receives: outside text (a task's output) is framed as
   untrusted data, only the instruction is the app's. */
function tell(m) {
  if (m.data !== undefined && m.data !== null) sendData(m.label || "message", String(m.data), m.text);
  else sendNotice(m.text);
}

/* kind: task | briefing | reminder | missed | info. Optional: data and label
   (outside text, see tell), notice (the notification's text), inboxIds. */
export function deliver(msg = {}) {
  const m = message(msg);
  if (!m.text && !m.data) return;
  if (!isLeader()) return;  // the leader page tells it
  if (isLive()) {
    liveQueue.push(m);
    flushLive();
    return;
  }
  if (state.mode === "connecting") {  // about to go live: told then
    queue(m);
    return;
  }
  if (quietNow()) {
    if (m.kind === "reminder") {  // monsieur set it himself: a discreet reminder anyway
      earcon("alert", { userInitiated: true, soft: true });
      notify(m.notice || m.spoken || m.text, "reminder", m.inboxIds[0]);
      ack(m.inboxIds);
      delivered(m, "quiet");
    } else {
      queue(m);
      delivered(m, "queued");
    }
    return;
  }
  earcon("alert");
  notify(m.notice || m.spoken || m.text, m.kind, m.inboxIds[0]);
  const keep = m.kind !== "reminder";  // the details wait for the next session
  if (keep) queue(m);
  delivered(m, m.spoken ? "spoken" : "queued");
  if (!m.spoken) {
    if (!keep) queue(m);
    return;
  }
  speak(m.spoken).then((said) => {
    if (said) {
      ack(m.inboxIds);  // he heard it; the full text may still wait in the queue
      m.inboxIds = [];
    } else if (!keep) {
      queue(m);  // blocked (no click yet) or no voice: told at the next session
    }
  });
}

/* In a session: only at a pause, neither of them speaking. */
function conversing() {
  return BUSY_PHASES.has(state.phase) || state.responseActive || state.pendingResponse
    || Date.now() - state.lastActivity < 1500;
}

function flushLive() {
  clearTimeout(flushTimer);
  flushTimer = null;
  if (!liveQueue.length) return;
  if (!isLive()) {
    if (state.mode === "live" || state.mode === "connecting") flushTimer = setTimeout(flushLive, 400);
    else { liveQueue.splice(0).forEach(queue); }  // the session ended first
    return;
  }
  if (conversing()) { flushTimer = setTimeout(flushLive, 400); return; }
  const batch = liveQueue.splice(0);
  batch.forEach(tell);
  requestResponse();
  touch();
  ack(batch.flatMap(m => m.inboxIds));
  batch.forEach(m => delivered(m, "live"));
}

export function pendingCount() { return pending.reduce((n, m) => n + (m.count || 1), 0); }

function renderBadge() {
  const n = pendingCount();
  state.pending = n;
  const badge = $("badge");
  if (!badge) return;
  const show = n > 0 && isLeader();
  badge.hidden = !show;
  badge.textContent = show ? String(n) : "";
  badge.title = show ? S.badge(n) : "";
  if (show) badge.setAttribute("aria-label", S.badge(n)); else badge.removeAttribute("aria-label");
}

/* ---------------------------------------------------------- speech & notifications */
/* French voices first, and the natural-sounding ones among them. */
export function pickVoice(list, lang = "fr-FR") {
  const base = String(lang).slice(0, 2).toLowerCase();
  const same = (list || []).filter(v => v && String(v.lang || "").toLowerCase().replace("_", "-").startsWith(base));
  return same.find(v => /Natural|Online|Google/i.test(v.name || ""))
    || same.find(v => String(v.lang).toLowerCase() === String(lang).toLowerCase())
    || same[0] || null;
}

function cacheVoices() {
  try { voices = speechSynthesis.getVoices() || []; } catch { voices = []; }
}

/* The browser's own voice, outside a session. Resolves true once said; false
   when it couldn't be (no click on the page yet, no voice installed...). */
export function speak(text) {
  return new Promise((resolve) => {
    if (!text || !("speechSynthesis" in window)) { resolve(false); return; }
    const lang = state.config.speech_lang || "fr-FR";
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang;
    if (!voices.length) cacheVoices();
    const voice = pickVoice(voices, lang);
    if (voice) { try { u.voice = voice; } catch { /* not a real voice object */ } }
    let started = false, settled = false, timer = null;
    const finish = (said) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (started) muteWake(false);
      resolve(said);
    };
    u.onstart = () => {  // the wake word must not hear JARVIS
      if (settled || started) return;
      started = true;
      muteWake(true);
    };
    u.onend = () => finish(true);
    u.onerror = () => finish(false);
    // The end event sometimes never comes: don't keep the wake word muted for
    // good. Never started by then: the engine is stuck, it won't say it late.
    timer = setTimeout(() => {
      if (!started) { try { speechSynthesis.cancel(); } catch { /* gone */ } }
      finish(started);
    }, 5000 + text.length * 90);
    try { speechSynthesis.speak(u); } catch { finish(false); }
  });
}

/* A notification only when monsieur isn't looking at JARVIS. Reminders stay
   on screen until dismissed; a click brings the window back. */
export function notify(text, kind = "info", tag = "") {
  if (!("Notification" in window) || Notification.permission !== "granted") return null;
  if (!document.hidden && document.hasFocus()) return null;
  try {
    const n = new Notification("J.A.R.V.I.S.", {
      body: text, silent: true, requireInteraction: kind === "reminder",
      tag: kind === "reminder" ? `jarvis-rappel-${tag || Date.now()}` : `jarvis-${kind}`,
    });
    n.onclick = () => { window.focus(); n.close(); };
    return n;
  } catch {
    return null;  // unsupported here
  }
}

/* Asked in context, the first time it would have been useful. */
function offerNotifications() {
  if (offered || !("Notification" in window) || Notification.permission !== "default" || !isLeader()) return;
  if (Date.now() < Number(settings.get("notifLaterUntil", 0))) return;
  offered = true;
  addCard(S.notifTitle, S.notifAsk, "info", { id: "notif-ask", sticky: true, actions: [
    { label: S.notifEnable, primary: true, onClick: () => {
      Promise.resolve(Notification.requestPermission()).catch(() => {}).then(() => removeCard("notif-ask"));
    } },
    { label: S.notifLater, onClick: () => {
      settings.set("notifLaterUntil", Date.now() + 7 * 86400e3);
      removeCard("notif-ask");
    } },
  ] });
}

/* ---------------------------------------------------------- what the server pushes */
function taskMessage(tk) {
  const title = tk.title || "sans titre";
  const word = STATUS_WORD[tk.status] || "en échec";
  return {
    kind: "task",
    text: `Résultat de la tâche "${title}" (${tk.status}) : le texte ci-dessous vient de Claude Code. `
      + "Résume-le oralement en une ou deux phrases. Si c'est une analyse de données (chiffres, stats, "
      + "comparatifs), affiche un tableau de bord avec display_report (kpis, chart, table). Pour un simple "
      + "résultat ponctuel, utilise display_card.",
    label: `Résultat de la tâche « ${title} »`,
    data: (tk.output || "").slice(0, 4000) || "(aucune sortie)",
    spoken: `Monsieur, la tâche « ${title} » est ${word}.`,
    notice: `La tâche « ${title} » est ${word}.`,
    inboxIds: tk.inbox_id ? [tk.inbox_id] : [],
  };
}

function briefingMessage(text, inboxId) {
  return {
    kind: "briefing",
    text: "Briefing du matin : présente-le à monsieur de façon vivante et concise, en 30 secondes maximum.",
    label: "Briefing du matin",
    data: String(text || "").slice(0, 4000),
    spoken: "Bonjour monsieur. Votre briefing du matin est prêt : appelez-moi quand vous voudrez l'entendre.",
    notice: "Votre briefing du matin est prêt.",
    inboxIds: inboxId ? [inboxId] : [],
  };
}

/* Each message once per page: replays and the inbox may bring it twice. */
function firstTime(inboxId) {
  if (!inboxId) return true;
  if (handled.has(inboxId)) return false;
  handled.add(inboxId);
  return true;
}

export function onTask(tk) {
  state.tasks.set(tk.id, tk);
  if (tk.status === "running") {
    if (!state.announced.has(tk.id)) offerNotifications();
    return;
  }
  if (!FINAL.has(tk.status) || !isLeader() || state.announced.has(tk.id)) return;
  state.announced.add(tk.id);
  if (!firstTime(tk.inbox_id)) return;
  if (tk.origin === "briefing") deliver(briefingMessage(tk.output, tk.inbox_id));
  else deliver(taskMessage(tk));
}

function lateText(r) { return r.late_minutes ? ` (en retard de ${r.late_minutes} min)` : ""; }

export function onReminder(r) {
  const late = lateText(r);
  const text = r.text || r.title || "";
  addCard(`Rappel${late}`, text, "warning", r.inbox_id ? { id: `rappel-${r.inbox_id}` } : {});
  if (!isLeader() || !firstTime(r.inbox_id)) return;
  announce(`Rappel : ${text}`, { urgent: true });
  deliver({ kind: "reminder",
            text: `Rappel programmé arrivé à échéance${late}, annonce-le à monsieur maintenant : « ${text} »`,
            spoken: `Monsieur, un rappel : ${text}`,
            notice: `Rappel : ${text}`,
            inboxIds: r.inbox_id ? [r.inbox_id] : [] });
}

function onBriefing(b) {
  if (!isLeader() || !firstTime(b.inbox_id)) return;
  deliver(briefingMessage(b.text || b.summary || b.output, b.inbox_id));
}

function showWarning(w, inboxId) {
  const text = (w && (w.text || w.message)) || "";
  if (text) addCard(S.warning, text, "warning", inboxId ? { id: `avert-${inboxId}` } : {});
}

function onWarning(w) {
  showWarning(w, w.inbox_id);
  if (w.inbox_id && isLeader()) ack([w.inbox_id]);
}

/* Another page acknowledged: nothing left to tell here. */
function onAcked(ev) {
  const acked = new Set((ev && ev.acked) || []);
  for (const list of [pending, liveQueue]) {
    for (let i = list.length - 1; i >= 0; i--) {
      const ids = list[i].inboxIds;
      if (ids.length && ids.every(id => acked.has(id))) list.splice(i, 1);
    }
  }
  acked.forEach(id => handled.add(id));
  renderBadge();
}

/* ---------------------------------------------------------- missed while no page was open */
function describe(item) {
  const p = item.payload || {};
  const at = fmtTime(new Date((item.created || Date.now() / 1000) * 1000));
  if (item.kind === "reminder") {
    const text = p.text || p.title || "";
    return { spoken: `rappel : ${text}`, full: `Rappel de ${at} : ${text}` };
  }
  if (item.kind === "task") {
    const title = p.title || "sans titre", word = STATUS_WORD[p.status] || "en échec";
    if (p.origin === "briefing") {
      return { spoken: "votre briefing du matin est prêt", full: `Briefing du matin (${at}) :\n${p.output || ""}` };
    }
    return { spoken: `la tâche « ${title} » est ${word}`,
             full: `Tâche « ${title} » (${p.status}, ${at}) :\n${(p.output || "").slice(0, 1500)}` };
  }
  if (item.kind === "briefing") {
    return { spoken: "votre briefing du matin est prêt",
             full: `Briefing du matin (${at}) :\n${p.text || p.summary || p.output || ""}` };
  }
  const text = p.text || p.title || "";
  return { spoken: text, full: `${at} : ${text}` };
}

function missedMessage(items) {
  const lines = items.map(describe);
  const n = items.length;
  const shown = lines.slice(0, 3).map(l => l.spoken).join(" ; ");
  const more = n > 3 ? ` ; et ${n - 3} autre${n - 3 > 1 ? "s" : ""}` : "";
  return {
    kind: items.every(it => it.kind === "reminder") ? "reminder" : "missed",
    text: "Pendant l'absence de monsieur, ces messages sont arrivés. Annonce-les brièvement, dans l'ordre, "
      + "en commençant par « Pendant votre absence ».",
    label: "Messages arrivés pendant votre absence",
    data: lines.map(l => l.full).join("\n\n"),
    spoken: `Pendant votre absence : ${shown}${more}.`,
    notice: `Pendant votre absence : ${n} message${n > 1 ? "s" : ""}.`,
    inboxIds: items.map(it => it.id),
    count: n,
  };
}

/* What no page has told yet (last 24 h), as one message. */
export function syncInbox() {
  if (syncing) return syncing;
  syncing = (async () => {
    try {
      await stateLoaded;
      const items = await api("/api/inbox");
      if (!isLeader() || !Array.isArray(items)) return;
      const fresh = items.filter(it => it && it.id && !handled.has(it.id));
      if (!fresh.length) return;
      fresh.forEach(it => handled.add(it.id));
      const warnings = fresh.filter(it => it.kind === "warning");
      warnings.forEach(it => showWarning(it.payload, it.id));
      ack(warnings.map(it => it.id));
      const told = fresh.filter(it => it.kind !== "warning");
      for (const it of told) {
        if (it.kind === "reminder") {
          const p = it.payload || {};
          addCard(`Rappel (${fmtTime(new Date(it.created * 1000))})`, p.text || p.title || "", "warning",
                  { id: `rappel-${it.id}` });
        }
      }
      if (told.length) deliver(missedMessage(told));
    } catch (err) {
      console.warn(err);
    } finally {
      syncing = null;
    }
  })();
  return syncing;
}

/* ---------------------------------------------------------- start */
function renderControls() {
  const host = $("controlsExtra");
  if (!host || dndToggle) return;
  dndToggle = document.createElement("button");
  dndToggle.type = "button";
  dndToggle.id = "dndToggle";
  dndToggle.className = "ctl";
  dndToggle.addEventListener("click", () => setDnd(!(dndUntil * 1000 > Date.now())));
  host.append(dndToggle);
}

export function init() {
  try { dndUntil = Number(settings.get("dndUntil", 0)) / 1000 || 0; } catch { dndUntil = 0; }
  renderControls();
  renderDnd();
  stateLoaded = refreshState();
  if ("speechSynthesis" in window) {
    cacheVoices();
    try { speechSynthesis.addEventListener("voiceschanged", cacheVoices); } catch { /* old engine */ }
  }
  $("badge")?.addEventListener("click", () => connect());  // the queue is told once live

  bus.on("server:task", onTask);
  bus.on("server:reminder", onReminder);
  bus.on("server:briefing", onBriefing);
  bus.on("server:warning", onWarning);
  bus.on("server:inbox", onAcked);
  bus.on("server:leader", onLeader);
  bus.on("server:dnd", onDnd);
  bus.on("deliver", deliver);
  bus.on("tool:result", (r) => { if (r && r.name === "schedule") offerNotifications(); });
  bus.on("mode", ({ mode }) => {
    if (mode === "live" && pending.length) {
      liveQueue.push(...pending.splice(0));
      renderBadge();
    }
    if (liveQueue.length) flushLive();
  });
  bus.on("phase", () => { if (liveQueue.length) flushLive(); });
  // A page that (re)connects catches up on what nobody was told.
  bus.on("sse:open", () => { leaderKnown().then(() => { if (isLeader()) syncInbox(); }); });

  setInterval(() => { updateQuiet(); renderDnd(); }, 30e3);
  setInterval(refreshState, 120e3);  // the screen may have gone full screen since
}
