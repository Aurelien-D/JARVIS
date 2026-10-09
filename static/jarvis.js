"use strict";
/* JARVIS Local, page side: the voice session (OpenAI Realtime over WebRTC),
   the wake word, tools, live events pushed by the server, side panels,
   display cards and reports. */

const $ = (id) => document.getElementById(id);
const TOKEN = document.querySelector('meta[name="jarvis-token"]').content;
const orb = $("orb");
const ctx = orb.getContext("2d");
const statusEl = $("status");
const transcriptEl = $("transcript");
const youEl = $("you");
const cardsEl = $("cards");
const tasksEl = $("tasks");
const schedulesEl = $("schedules");
const memoryEl = $("memory");
const wakeBtn = $("wakeBtn");
const notifBtn = $("notifBtn");

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
// "Jarvis", and the ways speech recognition tends to spell it.
const WAKE_RE = /\b(?:j\.?\s?a\.?\s?r\.?\s?v\.?\s?i\.?\s?s|jarvi[sz]?|javis|jervis|djarvis)\b/i;

let pc = null, dc = null, micStream = null;
let analyserIn = null, analyserOut = null, audioCtx = null;
const remoteAudio = new Audio();
let serverConfig = { wake_word: true, speech_lang: "fr-FR", idle_minutes: 3 };

const state = {
  mode: "off",            // off | standby (wake word) | connecting | live
  connecting: false,
  wantLive: false,        // a session is wanted: drives auto-reconnect
  retries: 0,
  lastActivity: Date.now(),
  responseActive: false,
  pendingResponse: false,
  endRequested: false,
  wake: null,             // { final, command } while a wake word is handled
  history: [],            // recent exchanges, replayed into a new session
  queue: [],              // [SYSTEM] messages waiting for the next session
  announced: new Set(),   // task ids already read out
};
const tasksById = new Map();
const settings = loadSettings();

/* ---------------------------------------------------------- helpers */
function loadSettings() {
  try { return JSON.parse(localStorage.getItem("jarvis.settings") || "{}"); } catch { return {}; }
}
function saveSettings() {
  try { localStorage.setItem("jarvis.settings", JSON.stringify(settings)); } catch { /* private mode */ }
}
function wakeWanted() { return !!SR && (settings.wake ?? serverConfig.wake_word); }
function touch() { state.lastActivity = Date.now(); }

async function api(path, { method = "GET", body } = {}) {
  const headers = { "X-Jarvis-Token": TOKEN };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.detail || `HTTP ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return data;
}

function audio() {
  // One shared context: browsers cap how many can exist, and reconnects add up.
  if (!audioCtx) audioCtx = new AudioContext();
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  return audioCtx;
}

const EARCONS = { wake: [660, 880], online: [523, 659, 784], sleep: [784, 523], alert: [880, 660, 880], error: [330, 220] };
function earcon(kind) {
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

/* clock */
setInterval(() => {
  $("clock").textContent = new Date().toLocaleTimeString("fr-FR");
}, 1000);

/* ---------------------------------------------------------- orb rendering (arc reactor HUD) */
function sizeOrb() {
  const r = orb.getBoundingClientRect();
  orb.width = r.width * devicePixelRatio; orb.height = r.height * devicePixelRatio;
}
window.addEventListener("resize", sizeOrb); sizeOrb();

function level(analyser) {
  if (!analyser) return 0;
  const buf = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(buf);
  let s = 0; for (const v of buf) s += v;
  return s / buf.length / 255;
}

const TAU = Math.PI * 2;
let t = 0;
function draw() {
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
draw();

/* ---------------------------------------------------------- status & controls */
function setMode(mode) { state.mode = mode; renderStatus(); }

function renderStatus() {
  statusEl.innerHTML = {
    off: "clique l'orbe pour <b>initialiser</b>",
    standby: "<b>en veille</b> · dites « Jarvis » ou cliquez l'orbe",
    connecting: state.retries ? "<b>reconnexion…</b>" : "<b>connexion…</b>",
    live: "<b>en ligne</b> · à votre service, monsieur",
  }[state.mode];
  wakeBtn.hidden = !SR;
  wakeBtn.innerHTML = `mot d'éveil · <b>${wakeWanted() ? "on" : "off"}</b>`;
  wakeBtn.classList.toggle("off", !wakeWanted());
}

function showError(msg) {
  statusEl.innerHTML = `<b style="color:var(--err)">erreur</b> · ${esc(String(msg))}`;
}

function updateNotifBtn() {
  notifBtn.hidden = !("Notification" in window) || Notification.permission !== "default";
}
function askNotifications() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().then(updateNotifBtn, updateNotifBtn);
  }
}

orb.addEventListener("click", () => {
  askNotifications();
  if (state.mode === "live" || state.mode === "connecting") sleep();
  else connect();
});
wakeBtn.addEventListener("click", () => {
  settings.wake = !wakeWanted();
  saveSettings();
  if (state.mode === "off" || state.mode === "standby") {
    stopWake();
    goStandby();
  }
  renderStatus();
});
notifBtn.addEventListener("click", askNotifications);

/* ---------------------------------------------------------- realtime session */
async function connect({ reconnect = false } = {}) {
  if (state.connecting || state.mode === "live") return;
  state.connecting = true;
  state.wantLive = true;
  state.endRequested = false;
  if (!state.wake) stopWake(); // a wake word keeps listening for the rest of the sentence
  setMode("connecting");
  try {
    const sess = await api("/api/session", { method: "POST", body: { recent: recentContext(reconnect) } });

    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ac = audio();
    analyserIn = ac.createAnalyser(); analyserIn.fftSize = 256;
    ac.createMediaStreamSource(micStream).connect(analyserIn);

    pc = new RTCPeerConnection();
    pc.onconnectionstatechange = onPeerState;
    micStream.getTracks().forEach(tr => pc.addTrack(tr, micStream));
    pc.ontrack = (e) => {
      remoteAudio.srcObject = e.streams[0];
      remoteAudio.play().catch(() => {});
      analyserOut = ac.createAnalyser(); analyserOut.fftSize = 256;
      ac.createMediaStreamSource(e.streams[0]).connect(analyserOut);
    };
    dc = pc.createDataChannel("oai-events");
    dc.onmessage = (e) => handleEvent(JSON.parse(e.data));
    dc.onopen = onChannelOpen;
    dc.onclose = () => connectionLost();

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const sdp = await fetch(`https://api.openai.com/v1/realtime/calls?model=${sess.model}`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${sess.client_secret}`, "Content-Type": "application/sdp" },
      body: offer.sdp,
    }).then(async r => {
      const body = await r.text();
      // On error OpenAI answers JSON, not SDP: surface it instead of feeding it to setRemoteDescription.
      if (!r.ok) throw new Error(`OpenAI ${r.status}: ${body.slice(0, 200)}`);
      return body;
    });
    await pc.setRemoteDescription({ type: "answer", sdp });
  } catch (err) {
    teardown();
    if (reconnect && state.wantLive) {
      retryLater();
    } else {
      state.wantLive = false;
      state.wake = null;
      earcon("error");
      goStandby();
      showError(err.message);
    }
  } finally {
    state.connecting = false;
  }
}

function onChannelOpen() {
  state.retries = 0;
  setMode("live");
  touch();
  earcon("online");
  // What happened while JARVIS was asleep: task results, briefing...
  const queued = state.queue.splice(0);
  queued.forEach(sendSystem);
  if (state.wake) {
    if (state.wake.final) deliverWake();
    else setTimeout(() => {
      if (!state.wake) return;
      state.wake.final = true;
      stopWake();
      deliverWake();
    }, 2500);
  } else if (queued.length) {
    requestResponse();
  }
}

function teardown() {
  clearTimeout(lostTimer);
  if (dc) { dc.onclose = null; dc.onmessage = null; dc.onopen = null; }
  if (pc) { pc.onconnectionstatechange = null; pc.close(); }
  pc = null; dc = null;
  if (micStream) micStream.getTracks().forEach(tr => tr.stop());
  micStream = null;
  analyserIn = analyserOut = null;
  remoteAudio.srcObject = null;
  state.responseActive = false;
  state.pendingResponse = false;
  transcriptEl.dataset.cur = "";
}

/* Back to standby: session closed, listening for the wake word again. */
function sleep() {
  state.wantLive = false;
  state.wake = null;
  state.retries = 0;
  teardown();
  earcon("sleep");
  goStandby();
}

function goStandby() {
  setMode(wakeWanted() ? "standby" : "off");
  if (state.mode === "standby") startWake();
}

/* ---------------------------------------------------------- reconnection */
let lostTimer = null;
function onPeerState() {
  const s = pc && pc.connectionState;
  clearTimeout(lostTimer);
  if (s === "failed") connectionLost();
  // "disconnected" often heals by itself within a few seconds
  else if (s === "disconnected") lostTimer = setTimeout(() => {
    if (pc && pc.connectionState !== "connected") connectionLost();
  }, 4000);
}

function connectionLost() {
  if (!state.wantLive || state.connecting) return;
  teardown();
  setMode("connecting");
  retryLater();
}

function retryLater() {
  if (state.retries >= 5) {
    state.wantLive = false;
    state.retries = 0;
    addCard("Connexion perdue", "Impossible de rétablir la session vocale. Vérifie la connexion internet, puis rappelle-moi.", "warning");
    earcon("error");
    goStandby();
    return;
  }
  const delay = Math.min(1000 * 2 ** state.retries, 15000);
  state.retries++;
  renderStatus();
  setTimeout(() => { if (state.wantLive) connect({ reconnect: true }); }, delay);
}

function logExchange(role, text) {
  text = (text || "").trim();
  if (!text) return;
  state.history.push({ role, text: text.slice(0, 400) });
  if (state.history.length > 24) state.history.splice(0, state.history.length - 24);
}

function recentContext(force) {
  // A recent conversation (or a dropped connection) carries over to the new session.
  if (!force && Date.now() - state.lastActivity > 30 * 60e3) return "";
  return state.history.slice(-12).map(h => `${h.role} : ${h.text}`).join("\n");
}

/* Standby after a few silent minutes: no stray responses, no forgotten session. */
setInterval(() => {
  if (state.mode !== "live" || state.endRequested) return;
  const limit = serverConfig.idle_minutes * 60e3;
  if (limit <= 0) return;
  const idle = Date.now() - state.lastActivity;
  // A running task keeps JARVIS awake to read the result out, up to 15 silent minutes.
  const busy = [...tasksById.values()].some(tk => tk.status === "running");
  if (idle > limit && (!busy || idle > Math.max(limit, 15 * 60e3))) sleep();
}, 5000);

/* ---------------------------------------------------------- wake word */
let wakeRec = null, wakeRestartDelay = 300;

function startWake() {
  if (!wakeWanted() || wakeRec || state.mode === "live" || state.connecting) return;
  const rec = new SR();
  rec.lang = serverConfig.speech_lang;
  rec.continuous = true;
  rec.interimResults = true;
  rec.onresult = onWakeResult;
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      settings.wake = false;
      saveSettings();
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

function stopWake() {
  const rec = wakeRec;
  wakeRec = null;
  if (rec) { rec.onend = null; try { rec.abort(); } catch { /* already stopped */ } }
}

function onWakeResult(e) {
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

function deliverWake() {
  const w = state.wake;
  if (!w || !w.final || state.mode !== "live") return;
  state.wake = null;
  if (w.command) {
    youEl.textContent = `« ${w.command} »`;
    logExchange("monsieur", w.command);
    sendUserText(w.command);
  } else {
    sendSystem("Monsieur vient de t'appeler par ton nom : réponds très brièvement (par exemple « Oui, monsieur ? ») et attends sa demande.");
  }
  requestResponse();
}

/* ---------------------------------------------------------- talking to the model */
function send(obj) { if (dc && dc.readyState === "open") dc.send(JSON.stringify(obj)); }

function sendUserText(text) {
  send({ type: "conversation.item.create", item: {
    type: "message", role: "user", content: [{ type: "input_text", text }],
  }});
}
function sendSystem(text) { sendUserText(`[SYSTEM] ${text}`); }

/* Only one response at a time: asking while one runs is queued until it ends. */
function requestResponse() {
  if (!dc || dc.readyState !== "open") return;
  if (state.responseActive) { state.pendingResponse = true; return; }
  state.responseActive = true;
  send({ type: "response.create" });
}

/* Something to tell monsieur: now if he's listening, otherwise a chime,
   a short spoken notice and the full message kept for the next session. */
function deliver(text, { spoken, queue }) {
  if (state.mode === "live" && dc && dc.readyState === "open") {
    sendSystem(text);
    requestResponse();
    touch();
    return;
  }
  if (state.mode === "connecting") { // about to go live: it will be said then
    state.queue.push(text);
    return;
  }
  earcon("alert");
  const said = spoken ? speak(spoken) : false;
  notify(spoken || text);
  if (queue || !said) {
    state.queue.push(text);
    if (state.queue.length > 10) state.queue.shift();
  }
}

function speak(text) {
  if (!("speechSynthesis" in window)) return false;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = serverConfig.speech_lang;
  const voice = speechSynthesis.getVoices().find(v => v.lang && v.lang.startsWith(serverConfig.speech_lang.slice(0, 2)));
  if (voice) u.voice = voice;
  u.onstart = stopWake; // the wake word must not hear JARVIS itself
  u.onend = u.onerror = () => { if (state.mode === "standby") startWake(); };
  speechSynthesis.speak(u);
  return true;
}

function notify(text) {
  if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
  try { new Notification("J.A.R.V.I.S.", { body: text, silent: true }); } catch { /* unsupported */ }
}

/* ---------------------------------------------------------- realtime events + tools */
function handleEvent(ev) {
  switch (ev.type) {
    case "response.created":
      state.responseActive = true;
      touch();
      break;
    // GA renamed these events; accept both spellings.
    case "response.output_audio_transcript.delta":
    case "response.audio_transcript.delta":
      transcriptEl.textContent = (transcriptEl.dataset.cur || "") + ev.delta;
      transcriptEl.dataset.cur = transcriptEl.textContent;
      touch();
      break;
    case "response.output_audio_transcript.done":
    case "response.audio_transcript.done":
      logExchange("JARVIS", ev.transcript);
      break;
    case "conversation.item.input_audio_transcription.completed":
      if ((ev.transcript || "").trim()) {
        youEl.textContent = `« ${ev.transcript.trim()} »`;
        logExchange("monsieur", ev.transcript);
      }
      break;
    case "input_audio_buffer.speech_started":
      touch();
      break;
    case "response.done":
      onResponseDone(ev.response || {});
      break;
    case "error":
      onRealtimeError(ev.error || {});
      break;
  }
}

function onRealtimeError(err) {
  console.warn("Realtime:", err);
  if (err.code === "conversation_already_has_active_response") {
    state.pendingResponse = true;
    return;
  }
  state.responseActive = false;
  if (state.pendingResponse) { state.pendingResponse = false; requestResponse(); }
}

/* Tool calls are run once their response is complete, then answered together. */
async function onResponseDone(resp) {
  state.responseActive = false;
  transcriptEl.dataset.cur = "";
  const calls = (resp.output || []).filter(o => o.type === "function_call");
  if (calls.length) {
    touch();
    const images = [];
    for (const call of calls) {
      let args = {};
      try { args = JSON.parse(call.arguments || "{}"); } catch { /* malformed: run with defaults */ }
      let out;
      try {
        out = await runTool(call.name, args);
      } catch (err) {
        out = { ok: false, error: String(err.message || err) };
      }
      if (out && out.image) {
        images.push(out.image);
        out = { ok: true, note: "Image jointe dans le message suivant." };
      }
      send({ type: "conversation.item.create", item: {
        type: "function_call_output", call_id: call.call_id, output: JSON.stringify(out ?? { ok: true }),
      }});
    }
    for (const image of images) {
      send({ type: "conversation.item.create", item: {
        type: "message", role: "user", content: [{ type: "input_image", image_url: image }],
      }});
    }
    state.pendingResponse = false;
    if (state.endRequested) return sleepWhenQuiet();
    requestResponse();
    return;
  }
  if (state.endRequested) return sleepWhenQuiet();
  if (state.pendingResponse) { state.pendingResponse = false; requestResponse(); }
}

/* end_conversation: let the goodbye finish playing, then go to standby. */
function sleepWhenQuiet() {
  const started = Date.now();
  let quietSince = 0;
  const timer = setInterval(() => {
    if (state.mode !== "live") return clearInterval(timer);
    quietSince = level(analyserOut) < 0.01 ? (quietSince || Date.now()) : 0;
    if ((quietSince && Date.now() - quietSince > 1200) || Date.now() - started > 10000) {
      clearInterval(timer);
      sleep();
    }
  }, 200);
}

// Tools that run in the page; every other tool runs on the server.
const LOCAL_TOOLS = {
  display_card: (a) => { addCard(a.title || "Info", a.content || "", a.kind || "info"); return { status: "displayed" }; },
  display_report: (a) => { showReport(a); return { status: "displayed" }; },
  look_at_camera: () => grabCamera(),
  end_conversation: () => { state.endRequested = true; return { ok: true }; },
};

async function runTool(name, args) {
  if (LOCAL_TOOLS[name]) return LOCAL_TOOLS[name](args);
  if (name === "open_app" || name === "open_url") {
    const label = args.name || args.url || "";
    addCard("Lancement", `Ouverture de **${label}**${args.monitor ? ` → écran **${args.monitor}**` : ""}…`, "info");
  }
  const res = await api("/api/tool", { method: "POST", body: { name, arguments: args } });
  if (name === "look_at_screen" && res.image) addImageCard("Écran", res.image);
  return res;
}

async function grabCamera() {
  const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } } });
  try {
    const video = document.createElement("video");
    video.muted = true; video.playsInline = true; video.srcObject = stream;
    await video.play();
    await new Promise(r => setTimeout(r, 700)); // let the exposure settle
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    const image = canvas.toDataURL("image/jpeg", 0.75);
    addImageCard("Caméra", image);
    return { ok: true, image };
  } finally {
    stream.getTracks().forEach(tr => tr.stop());
  }
}

/* ---------------------------------------------------------- live events from the server */
let panelsLoaded = false;

function listenEvents() {
  const es = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  es.onopen = refreshPanels;
  es.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    if (ev.type === "task") onTask(ev);
    else if (ev.type === "reminder") onReminder(ev);
    else if (ev.type === "schedules") renderSchedules(ev.items);
    else if (ev.type === "memory") renderMemory(ev.facts);
  };
  es.onerror = () => {
    // A restarted server has a new token, so this page's is dead: reload to get it.
    api("/api/config").catch(err => { if (err.status === 401) location.reload(); });
  };
}

async function refreshPanels() {
  try {
    const [allTasks, items, facts] = await Promise.all([api("/api/tasks"), api("/api/schedules"), api("/api/memory")]);
    for (const tk of allTasks.slice().reverse()) {
      // History isn't news; a task that ended while we were disconnected is.
      if (!panelsLoaded && tk.status !== "running") state.announced.add(tk.id);
      onTask(tk);
    }
    renderSchedules(items);
    renderMemory(facts);
    panelsLoaded = true;
  } catch (err) {
    console.warn(err);
  }
}

function onTask(tk) {
  tasksById.set(tk.id, tk);
  renderTask(tk);
  if (tk.status === "running" || state.announced.has(tk.id)) return;
  state.announced.add(tk.id);
  if (tk.status === "cancelled") return;
  const summary = (tk.output || "").slice(0, 4000);
  if (tk.origin === "briefing") {
    deliver(`Briefing du matin (${tk.status}) : ${summary}\nPrésente-le à monsieur de façon vivante et concise, en 30 secondes maximum.`,
            { spoken: "Bonjour monsieur. Votre briefing du matin est prêt, appelez-moi quand vous voudrez l'entendre.", queue: true });
    return;
  }
  deliver(`Résultat de la tâche "${tk.title}" (${tk.status}): ${summary}\nRésume oralement en une ou deux phrases. Si c'est une analyse de données (chiffres, stats, comparatifs), affiche un tableau de bord avec display_report (kpis, chart, table). Pour un simple résultat ponctuel, utilise display_card.`,
          { spoken: `Monsieur, la tâche « ${tk.title} » est ${tk.status === "done" ? "terminée" : "en échec"}.`, queue: true });
}

function onReminder(r) {
  const late = r.late_minutes ? ` (en retard de ${r.late_minutes} min)` : "";
  addCard(`Rappel${late}`, r.text, "warning");
  deliver(`Rappel programmé arrivé à échéance${late}, annonce-le à monsieur maintenant : « ${r.text} »`,
          { spoken: `Monsieur, un rappel : ${r.text}`, queue: false });
}

/* ---------------------------------------------------------- side panels */
const PROFILE_LABEL = { recherche: "web", lecture: "lecture seule", complet: "accès complet" };
const ORIGIN_LABEL = { routine: "routine", briefing: "briefing" };
const REPEAT_LABEL = { daily: "chaque jour", weekdays: "en semaine", weekly: "chaque semaine" };

function renderTask(tk) {
  let el = document.getElementById(`task-${tk.id}`);
  if (!el) {
    tasksEl.querySelector(".none")?.remove();
    el = document.createElement("div");
    el.className = "task"; el.id = `task-${tk.id}`;
    el.innerHTML = `<div class="t"></div><div class="meta"></div>
      <div class="s"><span class="dot"></span><span class="lbl">Claude Code travaille…</span> <span class="chrono"></span>
      <span class="cancel" title="annuler">✕</span></div>
      <div class="prog"></div><div class="out"></div>`;
    el.querySelector(".t").textContent = tk.title;
    el.querySelector(".cancel").addEventListener("click", () =>
      api(`/api/task/${tk.id}/cancel`, { method: "POST" }).catch(err => console.warn(err)));
    tasksEl.prepend(el);
    while (tasksEl.children.length > 20) tasksEl.lastChild.remove();
  }
  el.querySelector(".meta").textContent = [
    PROFILE_LABEL[tk.profile] || tk.profile, tk.model || "modèle par défaut",
    tk.resumed_from ? `suite de ${tk.resumed_from}` : "", ORIGIN_LABEL[tk.origin] || "",
  ].filter(Boolean).join(" · ");
  el._started = tk.started * 1000;
  if (tk.status === "running") {
    el.querySelector(".prog").textContent = tk.progress || "";
    if (!el._timer) el._timer = setInterval(() => tickChrono(el), 1000);
    tickChrono(el);
    return;
  }
  clearInterval(el._timer); el._timer = null;
  el.classList.add({ done: "done", cancelled: "cancelled" }[tk.status] || "error");
  el.querySelector(".lbl").textContent = { done: "Terminé", cancelled: "Annulée" }[tk.status] || "Erreur";
  el.querySelector(".chrono").textContent = tk.ended ? `${Math.round(tk.ended - tk.started)}s` : "";
  el.querySelector(".prog").textContent = "";
  el.querySelector(".out").textContent = (tk.output || "").slice(0, 1200);
  el.querySelector(".cancel")?.remove();
}

function tickChrono(el) {
  el.querySelector(".chrono").textContent = Math.round((Date.now() - el._started) / 1000) + "s";
}

function itemRow(when, text, onDelete) {
  const row = document.createElement("div");
  row.className = "item";
  row.innerHTML = `<span class="when"></span><span class="txt"></span><span class="x" title="supprimer">✕</span>`;
  row.querySelector(".when").textContent = when;
  row.querySelector(".txt").textContent = text;
  row.querySelector(".x").addEventListener("click", () => onDelete().catch(err => console.warn(err)));
  return row;
}

function fmtWhen(d) {
  const now = new Date(), tomorrow = new Date(now.getTime() + 86400e3);
  const hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return hm;
  if (d.toDateString() === tomorrow.toDateString()) return `demain ${hm}`;
  return `${d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" })} ${hm}`;
}

function renderSchedules(items) {
  schedulesEl.innerHTML = "";
  if (!items.length) {
    schedulesEl.innerHTML = `<div class="none">Aucun rappel. « Rappelle-moi dans dix minutes de… »</div>`;
    return;
  }
  for (const it of items) {
    const repeat = REPEAT_LABEL[it.repeat] ? ` · ${REPEAT_LABEL[it.repeat]}` : "";
    schedulesEl.append(itemRow(fmtWhen(new Date(it.due * 1000)) + repeat,
      (it.kind === "task" ? "⚙ " : "") + it.title,
      () => api(`/api/schedules/${it.id}`, { method: "DELETE" })));
  }
}

function renderMemory(facts) {
  memoryEl.innerHTML = "";
  if (!facts.length) {
    memoryEl.innerHTML = `<div class="none">Rien encore. « Retiens que je préfère… »</div>`;
    return;
  }
  for (const f of facts.slice().reverse()) {
    memoryEl.append(itemRow("•", f.text, () => api(`/api/memory/${f.id}`, { method: "DELETE" })));
  }
}

/* ---------------------------------------------------------- display cards */
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function mdLite(s) {
  // minimal markdown: **bold**, `code`, "- " bullets
  let html = esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const lines = html.split("\n");
  let out = "", inList = false;
  for (const l of lines) {
    if (l.startsWith("- ")) {
      if (!inList) { out += "<ul>"; inList = true; }
      out += `<li>${l.slice(2)}</li>`;
    } else {
      if (inList) { out += "</ul>"; inList = false; }
      out += l + "\n";
    }
  }
  if (inList) out += "</ul>";
  return out.trimEnd();
}

function usesMarked() { return !!(window.marked && window.DOMPurify); }

function mdRender(text) {
  if (usesMarked()) {
    return DOMPurify.sanitize(marked.parse(text));
  }
  return mdLite(text); // offline fallback
}

function makeCard(title, kind) {
  const el = document.createElement("div");
  el.className = `card ${kind}`;
  el.innerHTML = `<h3><span></span><span class="x" title="fermer">✕</span></h3><div class="body"></div>`;
  el.querySelector("h3 span").textContent = title;
  el.querySelector(".x").addEventListener("click", () => el.remove());
  cardsEl.prepend(el);
  // keep at most 6 cards
  while (cardsEl.children.length > 6) cardsEl.lastChild.remove();
  return el;
}

function addCard(title, content, kind) {
  const body = makeCard(title, kind).querySelector(".body");
  body.innerHTML = mdRender(content);
  body.classList.toggle("md", usesMarked());
}

function addImageCard(title, src) {
  const img = new Image();
  img.src = src; img.alt = title;
  makeCard(title, "info").querySelector(".body").append(img);
}

/* ---------------------------------------------------------- report dashboard */
// Series palette validated for dark surfaces (CVD-safe, dataviz checks pass).
const SERIES_COLORS = ["#1e93c4", "#c47b1e", "#1ea86a"];
let _apex = null, _grid = null;

function showReport(r) {
  const rep = $("report");
  $("rtitle").textContent = r.title || "Rapport";

  // KPI tiles
  const kpisEl = $("kpis");
  kpisEl.innerHTML = "";
  for (const k of (r.kpis || []).slice(0, 4)) {
    const d = k.delta ? `<div class="d ${String(k.delta).trim().startsWith("-") ? "neg" : ""}">${esc(String(k.delta))}</div>` : "";
    kpisEl.insertAdjacentHTML("beforeend",
      `<div class="kpi"><div class="l">${esc(String(k.label ?? ""))}</div><div class="v">${esc(String(k.value ?? ""))}</div>${d}</div>`);
  }

  // Chart (ApexCharts)
  const chartEl = $("rchart");
  if (_apex) { _apex.destroy(); _apex = null; }
  chartEl.innerHTML = "";
  const c = r.chart;
  if (c && window.ApexCharts && (c.series || []).length) {
    const donut = c.type === "donut";
    const nSeries = donut ? (c.categories || []).length : c.series.length;
    const opts = {
      chart: { type: c.type || "line", height: 280, background: "transparent",
               toolbar: { show: false }, foreColor: "#4d7c99",
               fontFamily: "Rajdhani, sans-serif" },
      theme: { mode: "dark" },
      colors: SERIES_COLORS,
      stroke: { width: 2, curve: "straight" },
      grid: { borderColor: "rgba(64,220,255,.12)", strokeDashArray: 3 },
      dataLabels: { enabled: false },
      legend: { show: nSeries > 1, labels: { colors: "#cfeaff" } },
      tooltip: { theme: "dark" },
      series: donut ? c.series[0].data : c.series,
    };
    if (donut) opts.labels = c.categories || [];
    else opts.xaxis = { categories: c.categories || [] };
    if (c.type === "area") opts.fill = { type: "gradient", gradient: { opacityFrom: .3, opacityTo: .02 } };
    _apex = new ApexCharts(chartEl, opts);
    _apex.render();
  }

  // Table (Grid.js)
  const tableEl = $("rtable");
  if (_grid) { _grid.destroy(); _grid = null; }
  tableEl.innerHTML = "";
  const tb = r.table;
  if (tb && window.gridjs && (tb.rows || []).length) {
    _grid = new gridjs.Grid({
      columns: tb.columns || [],
      data: tb.rows,
      sort: true,
      search: tb.rows.length > 8,
      pagination: tb.rows.length > 12 ? { limit: 10 } : false,
      style: { table: { "font-family": "Rajdhani, sans-serif" } },
    });
    _grid.render(tableEl);
  }

  // Markdown notes
  $("rmd").innerHTML = r.markdown ? mdRender(r.markdown) : "";

  rep.classList.add("open");
}

/* ---------------------------------------------------------- start */
(async function init() {
  try {
    serverConfig = { ...serverConfig, ...(await api("/api/config")) };
  } catch (err) {
    console.warn(err);
  }
  updateNotifBtn();
  listenEvents();
  goStandby();
})();
