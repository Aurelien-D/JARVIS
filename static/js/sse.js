/* Live events pushed by the server (Server-Sent Events): each one goes on
   the bus as "server:<type>" (task, reminder, schedules, memory, leader...).
   - The stream resumes where it dropped: the browser sends the last event id
     back on reconnect and the server replays what this page missed.
   - The page has its own id and tells the server when it gains or loses the
     focus or a voice session, so the server can pick the one page that speaks
     (delivery.isLeader).
   - While the server can't be reached, #serverChip says so (on the paired
     iPhone, a stopped JARVIS reads « JARVIS est fermé sur le PC »).
   - Every PING_SECONDS the server sends a 'ping' frame (no id): it arrives
     here as server:ping. A page back on screen (iOS froze it while locked or
     in the background) whose stream said nothing for STALE_MS opens a new
     one at once, instead of waiting for the browser to notice. */
import { $, TOKEN, api, bus, state } from "./core.js";
import { T } from "./strings-fr.js";

const SERVER_DOWN = T.status?.serverDown || "Serveur JARVIS déconnecté · reconnexion…";
const SERVER_CLOSED = T.status?.serverClosed || "JARVIS est fermé · relancez JARVIS.bat";
const PC_CLOSED = T.delivery?.pcClosed || "JARVIS est fermé sur le PC";
const ID_KEY = "jarvis.client";
const WAS_PAIRED = "jarvis.wasPaired";  // read once by pair.js
const STALE_MS = 30e3;  // two pings (15 s) missed: the stream is dead, whatever its readyState says

let es = null;
let panelsLoaded = false;
let lastEventId = "";
let reopenTimer = null, reopenDelay = 3000;
let lastPresence = "";
let opened = 0;
let closed = false;  // « Quitter JARVIS »: the server said it stops
let closedText = SERVER_CLOSED;
let lastFrameAt = Date.now();  // the stream's last sign of life (opened, an event, a ping)
let forgetting = false;  // « Oublier cet iPhone » under way: its 401 is no removal by the PC

/* This page's id. Kept across a reload (sessionStorage), but taken out of
   storage while the page lives and put back when it goes: a duplicated tab
   copies sessionStorage, and two pages must never share an id. */
const CLIENT = (() => {
  let id = null;
  try {
    id = sessionStorage.getItem(ID_KEY);
    sessionStorage.removeItem(ID_KEY);
  } catch { /* storage blocked: a fresh id per load */ }
  if (id && /^[\w-]{8,64}$/.test(id)) return id;
  if (crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
})();
export function clientId() { return CLIENT; }

export function listenEvents() {
  clearTimeout(reopenTimer);
  if (es) es.close();
  const params = new URLSearchParams({ token: TOKEN, client: CLIENT });
  // A stream re-created by hand doesn't send Last-Event-ID itself.
  if (lastEventId) params.set("last_event_id", lastEventId);
  const source = es = new EventSource(`/api/events?${params}`);
  lastFrameAt = Date.now();  // a fresh stream gets its own 30 s
  source.onopen = () => {
    lastFrameAt = Date.now();
    // JARVIS relaunched while this page stayed open (its window couldn't
    // close): a fresh page brings the wake word and the panels back.
    if (closed) { location.reload(); return; }
    reopenDelay = 3000;
    setServerChip(false);
    opened++;
    bus.emit("sse:open", { first: opened === 1 });
    lastPresence = "";  // the server may have restarted: tell it again
    presence();
    refreshPanels();
  };
  source.onmessage = (e) => {
    lastFrameAt = Date.now();  // every frame, the pings included
    if (e.lastEventId) lastEventId = e.lastEventId;
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    if (ev && typeof ev.type === "string") bus.emit(`server:${ev.type}`, ev);
  };
  source.onerror = () => {
    state.synced = false;
    setServerChip(true);
    // A restarted server has a new token, so this page's is dead: reload to get it.
    api("/api/config").catch(err => { if (err.status === 401) reloadRefused(); });
    // The browser gives up for good on an HTTP error: start a new stream later.
    if (source.readyState === EventSource.CLOSED && source === es) reopenLater();
  };
}

/* A 401: this page's token is dead (a restarted JARVIS) or, on the paired
   iPhone, its device was removed on the PC. That answer clears the device
   cookie, so the next load cannot tell which: the mark lets pair.js say
   « Appareil retiré » instead of offering pairing as if the phone were new
   (a reload that finds JARVIS again drops it, see init). */
function reloadRefused() {
  if (state.remote && !forgetting) {
    try { sessionStorage.setItem(WAS_PAIRED, "1"); } catch { /* storage blocked: the plain pairing page */ }
  }
  location.reload();
}

/* Back on screen, or back from the back/forward cache: a silent stream is reopened now. */
function reopenIfStale() {
  if (Date.now() - lastFrameAt > STALE_MS) listenEvents();
}

function reopenLater() {
  clearTimeout(reopenTimer);
  reopenTimer = setTimeout(listenEvents, reopenDelay);
  reopenDelay = Math.min(reopenDelay * 2, 30000);
}

function setServerChip(down) {
  const chip = $("serverChip");
  if (!chip) return;
  chip.textContent = down ? (closed ? closedText : SERVER_DOWN) : "";
  chip.className = down ? "chip err" : "chip";
  chip.hidden = !down;
}

/* « Quitter JARVIS »: the chip says so (not « reconnexion… »). The stream
   keeps trying, slowly: a relaunched JARVIS reloads this page (its new
   token answers 401 to the old one, see onerror). remote: the paired
   iPhone, where there is no JARVIS.bat to relaunch. */
export function serverClosed({ remote = false } = {}) {
  closed = true;
  closedText = remote ? PC_CLOSED : SERVER_CLOSED;
  reopenDelay = 30000;
  setServerChip(true);
}

/* Focus and voice session, for the server's choice of leader. claim: monsieur
   asked for this page ('Utiliser celle-ci'). */
export function presence({ claim = false } = {}) {
  if (state.remote) return Promise.resolve();  // the phone is in no election (the server ignores it too)
  const body = {
    client: CLIENT,
    focused: document.visibilityState === "visible" && document.hasFocus(),
    live: state.mode === "live" || state.mode === "connecting",
    claim,
  };
  const key = JSON.stringify(body);
  if (key === lastPresence) return Promise.resolve();
  lastPresence = key;
  return api("/api/presence", { method: "POST", body })
    .then(r => { if (r && "leader" in r) bus.emit("server:leader", { type: "leader", client: r.leader, live: !!r.live }); })
    .catch(() => { lastPresence = ""; });  // said again at the next change
}
export function claim() { return presence({ claim: true }); }

/* On (re)connection: the full state, as if each item had just been pushed. */
async function refreshPanels() {
  try {
    const [allTasks, items, facts] = await Promise.all([api("/api/tasks"), api("/api/schedules"), api("/api/memory")]);
    for (const tk of allTasks.slice().reverse()) {
      // History isn't news; a task that ended while we were disconnected is
      // (the inbox has it, see delivery.js).
      if (!panelsLoaded && tk.status !== "running") state.announced.add(tk.id);
      bus.emit("server:task", tk);
    }
    bus.emit("server:schedules", { items });
    bus.emit("server:memory", { facts });
    panelsLoaded = true;
    state.synced = true;
  } catch (err) {
    console.warn(err);
  }
}

export function init() {
  if (state.remote) {
    try { sessionStorage.removeItem(WAS_PAIRED); } catch { /* storage blocked */ }  // still paired
    bus.on("remote:forgetting", (on) => { forgetting = !!on; });
  }
  $("serverChip")?.setAttribute("role", "status");  // read out when the server drops
  setServerChip(false);
  listenEvents();
  const tell = () => presence();
  addEventListener("focus", tell);
  addEventListener("blur", tell);
  document.addEventListener("visibilitychange", tell);
  bus.on("mode", tell);
  addEventListener("pagehide", () => {
    try { sessionStorage.setItem(ID_KEY, CLIENT); } catch { /* storage blocked */ }
  });
  addEventListener("pageshow", (e) => {
    if (!e.persisted) return;  // back from the back/forward cache: still this page
    try { sessionStorage.removeItem(ID_KEY); } catch { /* storage blocked */ }
    reopenIfStale();
  });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") reopenIfStale(); });
  // Back from sleep or offline: a stream the browser gave up on starts again now.
  addEventListener("online", () => { if (!es || es.readyState === EventSource.CLOSED) listenEvents(); });
}
