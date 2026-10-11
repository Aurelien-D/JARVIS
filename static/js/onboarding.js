/* Mise en route (WP12): a first-run checklist that surfaces every setup
   problem before the first click: the OpenAI key, Claude Code, the
   microphone (with the Windows privacy switch), the kind of microphone, the
   wake word, A.R.E.S, notifications, then the server's other checks.
   - It opens by itself on a fresh data folder (state.json has no
     'onboarded'), or when /api/health finds a new error ('Terminer' or Échap
     remembers the errors already shown, so the same ones don't nag).
   - Client checks run here (microphone permission and devices, speech
     recognition, notifications); the server's come from /api/health.
   - Everything on screen is set as text, never as HTML.
   - Never on a remote page (the paired iPhone): every check here is about
     this PC, and only the PC can fix them.
   Also exports what the Réglages dialog reuses: the check list and the key form. */
import { $, api, bus, settings as prefs, state } from "./core.js";
import { audio } from "./audio-fx.js";
import { SR, wakeEngine, wakeWanted } from "./wake.js";
import { T, explainError, fr } from "./strings-fr.js";

const O = T.onboarding;
const S = O;  // strings-fr.js, T.onboarding: one dictionary for the whole page
const STATE_WORD = { ok: O.states.ok, fix: O.states.fix, info: O.states.info, wait: S.checking };
const ICON = { ok: "✓", fix: "!", info: "i", wait: "…" };
// Server checks shown in their own step, not under 'Autres vérifications'.
const STEP_CHECKS = new Set(["openai", "claude", "claude_connectors", "claude_hardening", "micro_windows", "ares"]);

/* ---------------------------------------------------------- small DOM helpers */
export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (typeof value !== "string" && key in el) el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of kids.flat()) {
    if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

export function button(label, onClick, className = "ctl") {
  return h("button", { type: "button", class: className, text: label, onclick: onClick });
}

/* ok | fix | info from a server level (ok, info, warning, error). */
export function stateOf(level) {
  return level === "ok" ? "ok" : level === "info" ? "info" : level === "wait" ? "wait" : "fix";
}

/* One row: an icon (decorative), the state as a word, a title, the message and the fix. */
export function checkRow({ id = "", state: st = "wait", title = "", message = "", fix = "" } = {}, tag = "li") {
  const row = h(tag, { class: "ob-row", "data-state": st, "data-check": id || null });
  const text = h("div", { class: "ob-text" });
  row.append(h("span", { class: "ob-icon", "aria-hidden": "true", text: ICON[st] || "i" }), text);
  const head = h("p", { class: "ob-title" });
  head.append(h("span", { class: "ob-state", text: STATE_WORD[st] || "" }), " ", h("strong", { text: fr(title) }));
  text.append(head);
  if (message) text.append(h("p", { class: "ob-msg", text: fr(message) }));
  if (fix) text.append(h("p", { class: "ob-fix", text: fr(fix) }));
  return row;
}

export function setRow(row, { state: st, title, message, fix } = {}) {
  const fresh = checkRow({ id: row.dataset.check, state: st ?? row.dataset.state, title: title ?? row.querySelector(".ob-title strong")?.textContent,
                           message, fix });
  const extra = row.querySelector(".ob-extra");
  row.replaceChildren(...fresh.childNodes);
  row.dataset.state = fresh.dataset.state;
  if (extra) row.querySelector(".ob-text").append(extra);
  return row;
}

/* The server's checks as a list (Réglages › Connexion uses it as is). */
export function renderChecks(list, host, { exclude = new Set() } = {}) {
  host.replaceChildren(...list.filter(c => !exclude.has(c.id)).map(c => checkRow(
    { id: c.id, state: stateOf(c.level), title: c.title_fr, message: c.message_fr, fix: c.fix_fr })));
  return host;
}

export function checkHealth(refresh = false) {
  return api(`/api/health${refresh ? "?refresh=true" : ""}`);
}

/* ---------------------------------------------------------- the key form (shared with Réglages) */
/* The masked key, and a password field to replace it. confirm(masked): a
   promise of true before a key in place is replaced (Réglages asks in a
   dialog; the first-run checklist does not, saving is its confirmation).
   onSaved(masked) after a success. */
export function keyForm({ masked = "", onSaved, confirm } = {}) {
  const box = h("div", { class: "key-form" });
  const current = h("p", { class: "key-current", text: masked ? S.key.current(masked) : S.key.none });
  const status = h("p", { class: "set-status", role: "status" });
  const input = h("input", { type: "password", class: "set-input mono", id: `key-${Math.random().toString(36).slice(2, 8)}`,
                             autocomplete: "off", spellcheck: "false", placeholder: "sk-…" });
  const label = h("label", { class: "visually-hidden", for: input.id, text: S.key.input });
  const save = button(S.key.save, null, "ctl primary");
  const cancel = button(S.key.cancel, null);
  const edit = h("form", { class: "key-edit", hidden: true }, label, input, save, cancel);
  const toggle = button(masked ? S.key.replace : S.key.add, () => {
    edit.hidden = false;
    toggle.hidden = true;
    input.focus();
  });
  cancel.addEventListener("click", () => {
    input.value = "";
    edit.hidden = true;
    toggle.hidden = false;
    status.textContent = "";
    toggle.focus();
  });
  edit.addEventListener("submit", async (e) => {
    e.preventDefault();
    save.disabled = true;
    status.className = "set-status";
    try {
      if (masked && confirm && !(await confirm(masked))) return;
      const r = await api("/api/settings/openai-key", { method: "POST", body: { key: input.value, confirm: true } });
      masked = r.masked;
      input.value = "";
      edit.hidden = true;
      toggle.hidden = false;
      toggle.textContent = S.key.replace;
      current.textContent = S.key.current(r.masked);
      status.textContent = S.key.saved;
      onSaved?.(r.masked);
      toggle.focus();
    } catch (err) {
      status.className = "set-status err";
      status.textContent = explainError(err);
      input.setAttribute("aria-invalid", "true");
      input.focus();
    } finally {
      save.disabled = false;
    }
  });
  save.type = "submit";
  input.addEventListener("input", () => input.removeAttribute("aria-invalid"));
  box.append(current, h("div", { class: "key-actions" }, toggle), edit, status,
             h("p", { class: "set-help", text: S.key.help }));
  return box;
}

/* ---------------------------------------------------------- client-side checks */
async function micPermission() {
  try {
    return (await navigator.permissions.query({ name: "microphone" })).state;  // granted | prompt | denied
  } catch {
    return "unknown";
  }
}

async function audioInputs() {
  try {
    return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput").length;
  } catch {
    return -1;  // unknown
  }
}

export async function micCheck(serverDeny = null) {
  if (!navigator.mediaDevices?.getUserMedia) return { state: "fix", message: S.mic.unsupported };
  const [perm, count] = await Promise.all([micPermission(), audioInputs()]);
  if (serverDeny) return { state: "fix", message: fr(serverDeny.message_fr), fix: fr(serverDeny.fix_fr) };
  if (perm === "denied") return { state: "fix", message: S.mic.denied };
  if (count === 0) return { state: "fix", message: S.mic.none };
  if (perm === "granted") return { state: "ok", message: S.mic.granted(count) };
  return { state: "info", message: perm === "prompt" ? S.mic.prompt : S.mic.unknown };
}

export function wakeCheck() {
  if (!SR) return { state: "info", message: S.wake.none, fix: S.wake.reader };
  if (!wakeWanted()) return { state: "info", message: S.wake.off, fix: S.wake.reader };
  const engine = state.wakeEngine || wakeEngine();
  return engine === "local" ? { state: "ok", message: S.wake.local, fix: S.wake.reader }
                            : { state: "info", message: S.wake.cloud, fix: S.wake.reader };
}

function notifCheck() {
  if (!("Notification" in window)) return { state: "info", message: S.notif.unsupported };
  const perm = Notification.permission;
  return perm === "granted" ? { state: "ok", message: S.notif.granted }
       : perm === "denied" ? { state: "info", message: S.notif.denied }
       : { state: "info", message: S.notif.default };
}

/* The microphone constraints a conversation uses (the chosen device, if any). */
export function micConstraints() {
  const id = prefs.get("micId", "");
  return { echoCancellation: true, noiseSuppression: true, autoGainControl: true,
           ...(id ? { deviceId: { ideal: id } } : {}) };
}

/* 'Tester le micro': six seconds of level meter. */
let micTest = null;
export async function testMic(meter, note, onResult) {
  stopMicTest();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: micConstraints() });
  } catch (err) {
    note.textContent = explainError(err);
    onResult?.(false, err);
    return;
  }
  const ac = audio();
  const source = ac.createMediaStreamSource(stream);
  const analyser = ac.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const test = { stream, source, peak: 0, raf: 0, end: performance.now() + 6000 };
  micTest = test;
  meter.hidden = false;
  note.textContent = S.mic.listening;
  const tick = (now) => {
    if (micTest !== test) return;
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    const level = Math.min(1, Math.sqrt(sum / buf.length) * 5);
    test.peak = Math.max(test.peak, level);
    meter.value = level;
    if (now < test.end) { test.raf = requestAnimationFrame(tick); return; }
    const heard = test.peak > 0.05;
    stopMicTest();
    note.textContent = heard ? S.mic.works : S.mic.silent;
    onResult?.(heard);
  };
  test.raf = requestAnimationFrame(tick);
}

export function stopMicTest() {
  if (!micTest) return;
  cancelAnimationFrame(micTest.raf);
  micTest.stream.getTracks().forEach(t => t.stop());
  try { micTest.source.disconnect(); } catch { /* already */ }
  micTest = null;
}

/* 'Tester la voix': the browser's own voice, the one used outside conversations. */
export function testVoice(note) {
  if (!("speechSynthesis" in window)) { if (note) note.textContent = S.voice.none; return false; }
  const u = new SpeechSynthesisUtterance(S.voice.sample);
  u.lang = "fr-FR";
  const voices = speechSynthesis.getVoices().filter(v => /^fr/i.test(v.lang));
  u.voice = voices.find(v => /google|natural|neural/i.test(v.name)) || voices[0] || null;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
  if (note) note.textContent = S.voice.note;
  return true;
}

/* ---------------------------------------------------------- the dialog */
let dialog = null, ui = null, invoker = null, lastErrors = "", lastList = null;

function errorSignature(list) {
  return (list || []).filter(c => c.level === "error").map(c => `${c.id}:${c.message_fr}`).sort().join("|");
}

function build() {
  dialog = $("onboarding");
  if (!dialog) return false;
  dialog.classList.add("ob-dialog");
  const close = button("✕", () => dialog.close(), "x");
  close.setAttribute("aria-label", S.close);
  const head = h("header", { class: "ob-head" }, h("h2", { id: "obTitle", text: O.title }), close);
  const intro = h("p", { class: "ob-intro", text: S.intro });
  const steps = h("ol", { class: "ob-steps", "aria-label": S.stepsLabel });
  const live = h("p", { class: "visually-hidden", role: "status", "aria-live": "polite" });

  const [stepKey, stepClaude, stepMic, stepKind, stepWake, stepAres, stepNotif] = O.steps;
  const rows = {
    key: checkRow({ id: "openai", title: stepKey }),
    claude: checkRow({ id: "claude", title: stepClaude }),
    mic: checkRow({ id: "micro", title: stepMic }),
    kind: checkRow({ id: "mic_kind", state: "info", title: stepKind, message: S.micKind }),
    wake: checkRow({ id: "wake", title: stepWake }),
    ares: checkRow({ id: "ares", state: "info", title: stepAres, message: O.ares }),
    notif: checkRow({ id: "notifications", title: stepNotif }),
  };
  // Extras that stay in their row when it is updated.
  const extra = (row, ...nodes) => row.querySelector(".ob-text").append(h("div", { class: "ob-extra" }, ...nodes));

  const keyHost = h("div");
  extra(rows.key, keyHost);

  const meter = h("meter", { class: "ob-meter", min: 0, max: 1, low: 0.05, optimum: 0.5, value: 0, hidden: true,
                             "aria-label": S.mic.meter });
  const micNote = h("p", { class: "set-status", role: "status" });
  const micBtn = button(O.buttons.testMic, () => testMic(meter, micNote, (ok) => {
    if (ok) setRow(rows.mic, { state: "ok", message: S.mic.works });
  }));
  extra(rows.mic, h("div", { class: "ob-inline" }, micBtn, meter), micNote);

  const kindName = "obMicKind";
  const kindNote = h("p", { class: "set-status", role: "status" });
  const kinds = h("div", { class: "ob-radios", role: "radiogroup", "aria-label": stepKind });
  for (const [value, label] of [["near_field", O.micKinds.headset], ["far_field", O.micKinds.builtin]]) {
    const input = h("input", { type: "radio", name: kindName, value, id: `ob-${value}` });
    input.addEventListener("change", async () => {
      try {
        await api("/api/settings", { method: "PUT", body: { noise_reduction: value } });
        kindNote.className = "set-status";
        kindNote.textContent = S.micKindSaved;
        setRow(rows.kind, { state: "ok", message: S.micKind });
      } catch (err) {
        kindNote.className = "set-status err";
        kindNote.textContent = explainError(err);
      }
    });
    kinds.append(h("label", { class: "ob-radio", for: input.id }, input, " ", label));
  }
  extra(rows.kind, kinds, kindNote);

  const notifBtn = button(S.notif.enable, async () => {
    try { await Notification.requestPermission(); } catch { /* old signature */ }
    refreshClient();
  });
  extra(rows.notif, notifBtn);

  steps.append(...Object.values(rows));
  const othersTitle = h("h3", { class: "ob-others-title", text: S.others });
  const others = h("ul", { class: "ob-others", "aria-labelledby": "obOthersTitle" });
  othersTitle.id = "obOthersTitle";

  const voiceNote = h("p", { class: "set-status ob-voice-note", role: "status" });
  const recheck = button(O.buttons.recheck, () => refresh(true));
  const voiceBtn = button(O.buttons.testVoice, () => testVoice(voiceNote));
  const finish = button(O.buttons.finish, finishOnboarding, "ctl primary");
  const foot = h("footer", { class: "ob-foot" }, voiceNote, h("div", { class: "ob-buttons" }, recheck, voiceBtn, finish));
  const body = h("div", { class: "ob-body" }, intro, steps, othersTitle, others);
  dialog.replaceChildren(head, body, foot, live);

  dialog.addEventListener("close", onClose);
  ui = { rows, keyHost, kinds, others, othersTitle, notifBtn, live, recheck, finish, micBtn, voiceNote };
  return true;
}

async function finishOnboarding() {
  ui.finish.disabled = true;
  try {
    await api("/api/onboarding", { method: "POST", body: { done: true } });
    dialog.close();
  } catch {
    ui.voiceNote.textContent = S.finishFailed;
  } finally {
    ui.finish.disabled = false;
  }
}

function onClose() {
  stopMicTest();
  prefs.set("onboardingSeen", lastErrors);  // these problems were shown: no reopening for them
  bus.emit("ui:close", "onboarding");
  const back = invoker;
  invoker = null;
  if (back && back.isConnected) back.focus();
}

async function refreshClient(list = lastList) {
  if (!ui) return;
  const deny = (list || []).find(c => c.id === "micro_windows") || null;
  setRow(ui.rows.mic, await micCheck(deny));
  setRow(ui.rows.wake, wakeCheck());
  const notif = notifCheck();
  setRow(ui.rows.notif, notif);
  ui.notifBtn.hidden = !("Notification" in window) || Notification.permission !== "default";
}

function applyServer(list) {
  const byId = Object.fromEntries(list.map(c => [c.id, c]));
  const merge = (row, ids) => {
    const found = ids.map(id => byId[id]).filter(Boolean);
    if (!found.length) { setRow(row, { state: "info", message: S.unchecked }); return; }
    // The step's own check sets its state; a note beside it only if it needs fixing.
    const main = found[0];
    const worst = found.some(c => stateOf(c.level) === "fix") ? "fix" : stateOf(main.level);
    const rest = found.slice(1).map(c => fr(c.message_fr + (c.fix_fr ? ` ${c.fix_fr}` : ""))).join(" ");
    setRow(row, { state: worst, message: main.message_fr, fix: [main.fix_fr, rest].filter(Boolean).join(" ") });
  };
  merge(ui.rows.key, ["openai"]);
  merge(ui.rows.claude, ["claude", "claude_hardening", "claude_connectors"]);
  if (byId.ares) {
    setRow(ui.rows.ares, { state: "info", message: O.ares, fix: fr(byId.ares.message_fr + (byId.ares.fix_fr ? ` ${byId.ares.fix_fr}` : "")) });
  }
  renderChecks(list, ui.others, { exclude: STEP_CHECKS });
  const none = !ui.others.children.length;
  ui.others.hidden = ui.othersTitle.hidden = none;
}

/* The key form and the microphone kind come from the saved settings. */
async function renderSettings() {
  let s = null;
  try { s = await api("/api/settings"); } catch { /* the form still lets monsieur paste a key */ }
  const masked = s?.key?.masked || "";
  // Rebuilt only when the key changed elsewhere: never under monsieur's focus.
  if (!ui.keyHost.firstChild || ui.keyMasked !== masked) {
    ui.keyMasked = masked;
    ui.keyHost.replaceChildren(keyForm({ masked, onSaved: (m) => { ui.keyMasked = m; refresh(true); } }));
  }
  const kind = s?.values?.noise_reduction;
  for (const input of ui.kinds.querySelectorAll("input")) input.checked = input.value === kind;
}

/* Revérifier: the server's checks again (refresh skips its caches), and ours. */
export async function refresh(force = false, list = null) {
  if (!ui) return;
  ui.recheck.disabled = true;
  for (const key of ["key", "claude", "mic", "wake", "notif"]) setRow(ui.rows[key], { state: "wait", message: "" });
  ui.live.textContent = S.checking;
  try {
    lastList = list || await checkHealth(force);
    lastErrors = errorSignature(lastList);
    // Shown now: remembered at once, not only on 'close' (an event the browser
    // queues, which a reload right after Échap can outrun).
    if (dialog.open) prefs.set("onboardingSeen", lastErrors);
    applyServer(lastList);
  } catch {
    setRow(ui.rows.key, { state: "fix", message: S.serverDown });
    setRow(ui.rows.claude, { state: "fix", message: S.serverDown });
  }
  await Promise.all([renderSettings(), refreshClient(lastList)]);
  const fixes = dialog.querySelectorAll('.ob-row[data-state="fix"]').length;
  ui.live.textContent = fixes ? fr(`${O.states.fix} : ${fixes}`) : O.states.ok;
  ui.recheck.disabled = false;
}

export function openOnboarding({ list = null, from = null } = {}) {
  if (state.remote) return;  // the iPhone: this PC's setup is not its business
  if (!dialog && !build()) return;
  if (!dialog.open) {
    invoker = from || (document.activeElement !== document.body ? document.activeElement : null);
    dialog.showModal();  // the focus goes to its first control (the ✕), Échap closes it
  }
  return refresh(false, list);
}

export function isOpen() { return !!dialog?.open; }

/* At load: the first run, or a problem not shown yet. Never blocks the page's start. */
async function autoOpen() {
  if (state.remote) return;
  let first = false;
  try {
    first = !(await api("/api/onboarding")).onboarded;
  } catch {
    return;  // the server is down: sse.js already says so
  }
  if (first) { openOnboarding(); return; }
  let list;
  try { list = await checkHealth(false); } catch { return; }
  const errors = errorSignature(list);
  if (!errors) { prefs.set("onboardingSeen", ""); return; }
  if (prefs.get("onboardingSeen", "") === errors) return;
  // Not over a conversation monsieur already started: the next load will show it.
  if (state.mode === "live" || state.mode === "connecting" || document.querySelector("dialog[open]")) return;
  openOnboarding({ list });
}

export function init() {
  bus.on("ui:open", (name) => { if (name === "onboarding") openOnboarding(); });
  autoOpen();  // not awaited: the other modules start meanwhile
}
