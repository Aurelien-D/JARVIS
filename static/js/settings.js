/* Réglages (WP12): a modal <dialog> styled as a right drawer, one section at
   a time: Connexion · Voix · Écoute · Proactivité · Claude Code · Coûts ·
   Accès à distance · Système · Données · À propos.
   - Other modules add a section of their own with registerSection() (the
     remote access and notification sections).
   - Server settings come from /api/settings (schema and values) and are
     saved one field at a time; each says when it takes effect: at once, at
     the next conversation, at the next task, or after a restart.
   - Sensitive ones (permission mode, working folder, MCP file, the key)
     carry a lock and ask for a confirmation first. The server refuses them
     without it, and no voice tool can reach them.
   - This browser's own preferences (microphone and speaker, push-to-talk,
     animations, sound volume) stay in core.settings.
   - Everything from the server is set as text, never as HTML. Inside a
     modal dialog the page's toasts and live regions are out of reach, so
     each field has its own status line. */
import { $, api, bus, settings as prefs, state } from "./core.js";
import { audio, earcon } from "./audio-fx.js";
import { T, explainError, fr } from "./strings-fr.js";
import { SR, toggleWake, wakeEngine, wakeWanted } from "./wake.js";
import * as voice from "./voice.js";
import { button, checkHealth, checkRow, h, keyForm, openOnboarding, renderChecks } from "./onboarding.js";

const TS = T.settings;
const S = TS;  // strings-fr.js, T.settings: one dictionary for the whole page
const SECTION_ORDER = ["connexion", "voix", "ecoute", "proactivite", "claude", "couts", "distance", "notifications",
                       "systeme", "donnees", "apropos"];
const DAY_KEYS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"];
const SELECT_SETTLE_MS = 800;  // a list looked through with the arrow keys is saved once it settles

let dialog = null, ui = null, model = null, invoker = null;
let current = prefs.get("settingsSection", "") || "voix";
let deviceWatch = null;
let aboutTries = 0;  // versions the server is still probing: asked again twice at most

/* ---------------------------------------------------------- helpers */
function lockIcon() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "set-lock");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", "M4.5 7V5a3.5 3.5 0 0 1 7 0v2H13v7.5H3V7h1.5zm1.6 0h3.8V5a1.9 1.9 0 0 0-3.8 0v2z");
  svg.append(path);
  return svg;
}

function labelNodes(entry) {
  const nodes = [fr(entry.label)];
  if (entry.sensitive) nodes.push(" ", lockIcon(), h("span", { class: "visually-hidden", text: S.sensitiveTag }));
  return nodes;
}

function say(wrap, text, error = false) {
  const status = wrap.querySelector(":scope > .set-status");
  if (!status) return;
  status.className = error ? "set-status err" : "set-status";
  status.textContent = text;
  for (const el of wrap.querySelectorAll("input, select")) {
    if (error) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid");
  }
}

function sameValue(a, b) {
  if (typeof a === "number" || typeof b === "number") return Number(a) === Number(b);
  return String(a ?? "") === String(b ?? "");
}

function choiceLabel(entry, value) {
  const c = (entry.choices || []).find(x => x.value === value);
  return c ? fr(c.label) : (value === "" ? S.empty : String(value));
}

/* '22:30-07:30' -> ['22:30', '07:30'] */
function splitHours(value) {
  const m = /^(\d{2}:\d{2})-(\d{2}:\d{2})$/.exec(String(value || ""));
  return m ? [m[1], m[2]] : null;
}

/* 'tous', 'lun-ven', 'lun,mer,ven', 'ven-lun' -> a Set of day keys (as settings.py reads them). */
export function parseDays(value) {
  const text = String(value || "").toLowerCase().replace(/\s+/g, "");
  if (text === "tous" || text === "*") return new Set(DAY_KEYS);
  const out = new Set();
  for (const token of text.split(",")) {
    const [a, b] = token.split("-").map(t => t.slice(0, 3));
    const i = DAY_KEYS.indexOf(a), j = b === undefined ? i : DAY_KEYS.indexOf(b);
    if (i < 0 || j < 0) continue;
    for (let k = i; ; k = (k + 1) % 7) { out.add(DAY_KEYS[k]); if (k === j) break; }
  }
  return out;
}

/* ---------------------------------------------------------- controls, by type */
/* Each returns { node, read(), write(value) }; read() undefined = not ready to save. */
function control(entry, id) {
  const kind = entry.type;
  if (kind === "bool") {
    const input = h("input", { type: "checkbox", id, class: "set-checkbox" });
    return { node: input, input, read: () => input.checked, write: (v) => { input.checked = !!v; } };
  }
  if (kind === "choice") {
    const select = h("select", { id, class: "set-input" });
    const fill = (value) => {
      const known = (entry.choices || []).some(c => c.value === value);
      select.replaceChildren(...(entry.choices || []).map(c => h("option", { value: c.value, text: fr(c.label) })));
      if (!known && value !== undefined) select.append(h("option", { value: String(value), text: S.current(value === "" ? S.empty : value) }));
      select.value = String(value ?? "");
    };
    return { node: select, input: select, read: () => select.value, write: fill };
  }
  if (kind === "int" || kind === "float") {
    const scale = entry.scale || 1;
    const input = h("input", { type: "number", id, class: "set-input set-number", min: entry.min / scale,
                               max: entry.max / scale, step: (entry.step || 1) / scale, inputmode: "decimal" });
    const unit = entry.unit ? h("span", { class: "set-unit", text: S.unit[entry.unit] || entry.unit }) : null;
    const node = h("span", { class: "set-numberbox" }, input, unit);
    return {
      node, input,
      read: () => (input.value === "" ? "" : Math.round(Number(input.value) * scale * 1000) / 1000),
      write: (v) => { input.value = v === "" || v === null || v === undefined ? "" : String(Math.round(Number(v) / scale * 1000) / 1000); },
    };
  }
  if (kind === "time") {
    // A switch and a time: an empty time field is hard to make in Chrome.
    const on = h("input", { type: "checkbox", id, class: "set-checkbox" });
    const time = h("input", { type: "time", id: `${id}-time`, class: "set-input set-time", "aria-label": fr(entry.label) });
    const node = h("span", { class: "set-hours" }, h("label", { class: "set-check", for: id }, on, " ", S.time.on), time);
    const sync = () => { time.disabled = !on.checked; };
    on.addEventListener("change", () => { if (on.checked && !time.value) time.value = "08:00"; sync(); });
    return {
      node, input: on,
      read: () => (!on.checked ? "" : time.value || undefined),
      write: (v) => { on.checked = !!v; time.value = v || ""; sync(); },
    };
  }
  if (kind === "hours") {
    const on = h("input", { type: "checkbox", id, class: "set-checkbox" });
    const from = h("input", { type: "time", id: `${id}-from`, class: "set-input set-time" });
    const to = h("input", { type: "time", id: `${id}-to`, class: "set-input set-time" });
    const node = h("span", { class: "set-hours" },
      h("label", { class: "set-check", for: id }, on, " ", S.hours.on),
      h("label", { for: from.id, text: S.hours.from }), from, h("label", { for: to.id, text: S.hours.to }), to);
    const sync = () => { from.disabled = to.disabled = !on.checked; };
    on.addEventListener("change", () => {
      if (on.checked && !from.value) from.value = "22:30";
      if (on.checked && !to.value) to.value = "07:30";
      sync();
    });
    return {
      node, input: on,
      read: () => (!on.checked ? "" : from.value && to.value ? `${from.value}-${to.value}` : undefined),
      write: (v) => { const parts = splitHours(v); on.checked = !!parts; if (parts) [from.value, to.value] = parts; sync(); },
    };
  }
  if (kind === "days") {
    const boxes = DAY_KEYS.map(d => h("input", { type: "checkbox", id: `${id}-${d}`, value: d, class: "set-checkbox" }));
    const node = h("span", { class: "set-days" }, ...boxes.map((box, i) => h("label", {
      class: "set-day", for: box.id, title: S.daysFull[DAY_KEYS[i]] }, box, " ", S.days[DAY_KEYS[i]])));
    return {
      node, input: boxes[0],
      read: () => boxes.filter(b => b.checked).map(b => b.value).join(","),
      write: (v) => { const on = parseDays(v); boxes.forEach(b => { b.checked = on.has(b.value); }); },
    };
  }
  // text, path, file, hotkey
  const mono = kind !== "text";
  const input = h("input", { type: "text", id, class: `set-input${mono ? " mono" : ""}`, autocomplete: "off",
                             spellcheck: mono ? "false" : "true", maxlength: entry.maxlength || null });
  return { node: input, input, read: () => input.value.trim(), write: (v) => { input.value = v ?? ""; } };
}

export function field(entry) {
  const id = `set-${entry.key}`;
  const wrap = h("div", { class: `set-field set-kind-${entry.type}`, "data-key": entry.key });
  const ctl = control(entry, id);
  const grouped = entry.type === "hours" || entry.type === "days" || entry.type === "time";
  const help = entry.help ? h("p", { class: "set-help", id: `${id}-help`, text: fr(entry.help) }) : null;
  const status = h("p", { class: "set-status", id: `${id}-status`, role: "status" });
  const described = [help && help.id, status.id].filter(Boolean).join(" ");
  if (grouped) {
    const set = h("fieldset", { class: "set-group", "aria-describedby": described },
      h("legend", {}, ...labelNodes(entry)), ctl.node);
    wrap.append(set);
  } else if (entry.type === "bool") {
    ctl.input.setAttribute("aria-describedby", described);
    wrap.append(h("label", { class: "set-check", for: id }, ctl.node, " ", ...labelNodes(entry)));
  } else {
    ctl.input.setAttribute("aria-describedby", described);
    wrap.append(h("label", { class: "set-label", for: id }, ...labelNodes(entry)), ctl.node);
  }
  let danger = null;
  if (entry.danger) {
    danger = h("p", { class: "set-danger", text: fr(entry.danger.text), hidden: true });
    wrap.append(danger);
  }
  if (help) wrap.append(help);
  wrap.append(status);
  const showDanger = () => { if (danger) danger.hidden = String(ctl.read()) !== entry.danger.value; };
  ctl.write(model.values[entry.key]);
  showDanger();
  const onChange = async () => {
    clearTimeout(wrap._t);
    wrap._t = null;
    showDanger();
    await commit(entry, ctl, wrap);
    showDanger();
  };
  for (const el of wrap.querySelectorAll("input")) el.addEventListener("change", onChange);
  // A closed list changes on each arrow key (Chrome on Windows fires 'change'
  // every time): looking through the choices must not save at each step, nor
  // open the confirmation (WCAG 3.2.2).
  const sel = wrap.querySelector("select");
  if (sel && entry.sensitive) {
    // A sensitive choice waits for 'Appliquer'; the red warning previews meanwhile.
    const apply = button(S.apply, onChange, "ctl set-apply");
    apply.setAttribute("aria-label", fr(`${S.apply} : ${entry.label}`));
    sel.after(apply);
    sel.addEventListener("change", () => {
      showDanger();
      say(wrap, sameValue(ctl.read(), model.values[entry.key]) ? "" : S.notApplied);
    });
  } else if (sel) {
    // Saved once the choice settles, or when the focus leaves the list.
    sel.addEventListener("change", () => {
      clearTimeout(wrap._t);
      wrap._t = setTimeout(onChange, SELECT_SETTLE_MS);
    });
    sel.addEventListener("blur", () => { if (wrap._t) onChange(); });
  }
  wrap._ctl = ctl;
  return wrap;
}

/* ---------------------------------------------------------- saving */
async function commit(entry, ctl, wrap) {
  const value = ctl.read();
  if (value === undefined) return;  // half filled (a range with one end)
  const before = model.values[entry.key];
  if (sameValue(value, before)) {
    // The wake word: the server already says so, this browser's switch may not.
    if (entry.key === "wake_word") { await afterChange(entry.key, value); say(wrap, S.when.now); } else say(wrap, "");
    return;
  }
  if (entry.sensitive) {
    say(wrap, "");  // 'pas encore appliqué' no more: the confirmation, then the server's answer
    if (!(await confirmChange(entry, value))) {
      ctl.write(before);
      return;
    }
  }
  // Quick changes (days ticked one after the other) may be answered out of
  // order: only the latest answer redraws the field.
  const seq = wrap._seq = (wrap._seq || 0) + 1;
  try {
    const body = { [entry.key]: value };
    if (entry.sensitive) body.confirm = true;
    const r = await api("/api/settings", { method: "PUT", body });
    if (seq !== wrap._seq) return;
    Object.assign(model, { values: r.values, restart: r.restart, overridden: r.overridden });
    if (r.versions) model.versions = r.versions;
    if ("deadlines" in r) model.deadlines = r.deadlines;
    ctl.write(model.values[entry.key]);  // as the server normalised it ('8h' -> '08:00')
    say(wrap, S.when[r.applied?.[entry.key]] || S.when.now);
    renderRestart();
    await afterChange(entry.key, model.values[entry.key]);
  } catch (err) {
    if (seq === wrap._seq) say(wrap, explainError(err), true);
  }
}

async function afterChange(key, value) {
  // The wake word switch of this browser follows the dialog (before the new
  // default arrives: toggleWake() flips what is in effect now).
  if (key === "wake_word" && SR && wakeWanted() !== !!value) toggleWake();
  try { Object.assign(state.config, await api("/api/config")); } catch { /* next load */ }
  bus.emit("settings:changed", { key, value });
}

/* A second modal over the drawer: what changes, and the bypass warning in red. */
function confirmChange(entry, value) {
  const shown = entry.type === "choice" ? choiceLabel(entry, value) : (value === "" ? S.empty : String(value));
  const danger = entry.danger && entry.danger.value === value ? fr(entry.danger.text) : "";
  return askConfirm({ title: S.confirmTitle, text: TS.sensitive, value: S.confirmValue(fr(entry.label), shown),
                      danger, ok: S.confirmOk });
}

export function askConfirm({ title, text, value = "", danger = "", ok }) {
  const d = ui.confirm;
  d.querySelector("h3").textContent = title;
  d.querySelector(".set-confirm-text").textContent = text;
  const v = d.querySelector(".set-confirm-value");
  v.textContent = value;
  v.hidden = !value;
  const warn = d.querySelector(".set-danger");
  warn.textContent = danger;
  warn.hidden = !danger;
  const okBtn = d.querySelector('button[value="ok"]');
  okBtn.textContent = ok;
  okBtn.classList.toggle("danger", !!danger);
  d.returnValue = "";
  return new Promise((resolve) => {
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    d.showModal();
    // A dangerous change starts on 'Annuler': Entrée must not confirm it by accident.
    (danger ? d.querySelector('button[value="cancel"]') : okBtn).focus();
  });
}

/* ---------------------------------------------------------- sections */
function entriesOf(section) {
  return model.schema.filter(e => e.section === section);
}

function heading(section) {
  const title = model.sections.find(s => s.id === section)?.title || section;
  return h("h3", { class: "set-section-title", id: `set-h-${section}`, text: fr(title) });
}

function sectionConnexion() {
  const health = h("ul", { class: "ob-checks", id: "setHealth", "aria-labelledby": "setHealthTitle" });
  const recheck = button(S.recheck, () => loadHealth(true));
  const onboard = button(S.openOnboarding, () => {
    const from = $("topActions")?.querySelector('[aria-controls="settingsDialog"]') || null;
    dialog.close();
    openOnboarding({ from });
  });
  ui.health = health;
  ui.recheck = recheck;
  return [
    h("div", { class: "set-field", "data-key": "openai_key" },
      h("p", { class: "set-label" }, T.onboarding.steps[0], " ", lockIcon(),
        h("span", { class: "visually-hidden", text: S.sensitiveTag })),
      keyForm({ masked: model.key?.masked || "",
                confirm: (old) => askConfirm({ title: S.confirmTitle, text: TS.sensitive,
                                               value: S.keyReplace(old), ok: S.confirmOk }),
                onSaved: (masked) => { model.key = { present: true, masked }; loadHealth(true); } })),
    h("h4", { class: "set-sub", id: "setHealthTitle", text: S.healthTitle }),
    health,
    h("div", { class: "set-actions" }, recheck, onboard),
  ];
}

function wakeField(entry) {
  const wrap = field(entry);
  const ctl = wrap._ctl;
  if (!SR) {
    ctl.input.disabled = true;
    wrap.append(h("p", { class: "set-help", text: S.noSR }));
  } else {
    ctl.write(wakeWanted());  // this browser's own switch is what is in effect here
  }
  return wrap;
}

function deviceSelect(kind, labelText, prefKey, nth) {
  const id = `set-${prefKey}`;
  const select = h("select", { id, class: "set-input" });
  const status = h("p", { class: "set-status", id: `${id}-status`, role: "status" });
  select.setAttribute("aria-describedby", status.id);
  const wrap = h("div", { class: "set-field", "data-key": prefKey },
    h("label", { class: "set-label", for: id, text: labelText }), select, status);
  wrap.fill = (devices) => {
    const saved = prefs.get(prefKey, "");
    const list = devices.filter(d => d.kind === kind && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications");
    select.replaceChildren(h("option", { value: "", text: S.devices.system }),
      ...list.map((d, i) => h("option", { value: d.deviceId, text: d.label || nth(i + 1) })));
    if (saved && !list.some(d => d.deviceId === saved)) select.append(h("option", { value: saved, text: S.devices.gone }));
    select.value = saved;
    return list.some(d => !d.label);
  };
  select.addEventListener("change", () => {
    prefs.set(prefKey, select.value);
    if (prefKey === "speakerId") { applySpeaker(select.value); say(wrap, S.when.now); } else say(wrap, S.devices.micSaved);
  });
  return wrap;
}

function sectionDevices() {
  const box = h("div", { class: "set-devices" });
  const mic = deviceSelect("audioinput", S.devices.mic, "micId", S.devices.micN);
  const sinkOk = typeof HTMLMediaElement !== "undefined" && "setSinkId" in HTMLMediaElement.prototype;
  const speaker = sinkOk ? deviceSelect("audiooutput", S.devices.speaker, "speakerId", S.devices.speakerN) : null;
  const names = button(S.devices.names, async () => {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach(t => t.stop());
    } catch (err) {
      say(mic, explainError(err), true);
    }
    refill();
  });
  names.hidden = true;
  const refill = async () => {
    let devices = [];
    try { devices = await navigator.mediaDevices.enumerateDevices(); } catch { /* no access */ }
    const unnamed = mic.fill(devices) | (speaker ? speaker.fill(devices) : false);
    names.hidden = !unnamed;
  };
  if (navigator.mediaDevices?.enumerateDevices) {
    refill();
    deviceWatch = refill;
    navigator.mediaDevices.addEventListener?.("devicechange", refill);
  }
  mic.append(h("p", { class: "set-help", text: S.devices.wakeNote }));
  box.append(mic, speaker || h("p", { class: "set-help", text: S.devices.noSink }), h("div", { class: "set-actions" }, names));
  return box;
}

function prefCheckbox(key, label, help) {
  const id = `set-${key}`;
  const input = h("input", { type: "checkbox", id, class: "set-checkbox", checked: prefs.get(key, false) === true });
  const status = h("p", { class: "set-status", id: `${id}-status`, role: "status" });
  const wrap = h("div", { class: "set-field set-bool", "data-key": key },
    h("label", { class: "set-check", for: id }, input, " ", label));
  if (help) wrap.append(h("p", { class: "set-help", id: `${id}-help`, text: help }));
  wrap.append(status);
  input.setAttribute("aria-describedby", [help ? `${id}-help` : "", status.id].filter(Boolean).join(" "));
  input.addEventListener("change", () => { prefs.set(key, input.checked); say(wrap, S.when.now); });
  return wrap;
}

function sectionEcoute() {
  const out = [];
  for (const entry of entriesOf("ecoute")) out.push(entry.key === "wake_word" ? wakeField(entry) : field(entry));
  out.push(sectionDevices(), prefCheckbox("ptt", S.ptt, S.pttHelp));
  return out;
}

function sectionSysteme() {
  const out = [];
  // Autostart: the shortcut in the Windows Startup folder (desktop.set_autostart).
  const id = "set-autostart";
  const input = h("input", { type: "checkbox", id, class: "set-checkbox", checked: model.autostart === true,
                             disabled: model.autostart === null || model.autostart === undefined });
  const status = h("p", { class: "set-status", id: `${id}-status`, role: "status" });
  input.setAttribute("aria-describedby", status.id);
  const auto = h("div", { class: "set-field set-bool", "data-key": "autostart" },
    h("label", { class: "set-check", for: id }, input, " ", S.autostart), status);
  if (input.disabled) auto.insertBefore(h("p", { class: "set-help", text: S.autostartNA }), status);
  input.addEventListener("change", async () => {
    try {
      const r = await api("/api/autostart", { method: "POST", body: { on: input.checked } });
      model.autostart = r.autostart ?? input.checked;
      say(auto, fr(r.message || S.when.now));
    } catch (err) {
      input.checked = !input.checked;
      say(auto, explainError(err), true);
    }
  });
  out.push(auto, ...entriesOf("systeme").map(field));

  // Animations and sounds: this browser only.
  const motion = h("select", { id: "set-motion", class: "set-input" },
    h("option", { value: "auto", text: S.motionAuto }), h("option", { value: "reduced", text: S.motionReduced }));
  motion.value = prefs.get("motion", "auto") === "reduced" ? "reduced" : "auto";
  const motionWrap = h("div", { class: "set-field", "data-key": "motion" },
    h("label", { class: "set-label", for: "set-motion", text: S.motion }), motion,
    h("p", { class: "set-help", id: "set-motion-help", text: S.motionHelp }),
    h("p", { class: "set-status", id: "set-motion-status", role: "status" }));
  motion.setAttribute("aria-describedby", "set-motion-help set-motion-status");
  motion.addEventListener("change", () => {
    prefs.set("motion", motion.value);
    document.body.classList.toggle("reduce-motion", motion.value === "reduced");
    say(motionWrap, S.when.now);
  });
  const level = Math.round(Math.max(0, Math.min(1, Number(prefs.get("earconVolume", 1)) || 0)) * 100);
  const range = h("input", { type: "range", id: "set-volume", class: "set-range", min: 0, max: 100, step: 5, value: String(level) });
  const output = h("output", { for: "set-volume", class: "set-output", text: S.volumeValue(level) });
  range.setAttribute("aria-valuetext", S.volumeValue(level));
  const volWrap = h("div", { class: "set-field", "data-key": "earconVolume" },
    h("label", { class: "set-label", for: "set-volume", text: S.volume }), h("span", { class: "set-rangebox" }, range, output));
  range.addEventListener("input", () => {
    output.textContent = S.volumeValue(range.value);
    range.setAttribute("aria-valuetext", S.volumeValue(range.value));
  });
  range.addEventListener("change", () => {
    prefs.set("earconVolume", Number(range.value) / 100);
    earcon("unmute", { userInitiated: true });  // a sample at the new level
  });
  out.push(motionWrap, volWrap);
  return out;
}

function sectionCouts() {
  const usage = h("div", { class: "set-usage", id: "settingsUsage" });  // WP18 draws the spending here
  return [...entriesOf("couts").map(field), usage];
}

function sectionDonnees() {
  const status = h("p", { class: "set-status", role: "status" });
  const actions = h("div", { class: "set-field", "data-key": "data-actions" });
  const open = button(TS.openData, async () => {
    try {
      await api("/api/settings/open-data", { method: "POST" });
      say(actions, S.opened);
    } catch (err) {
      say(actions, explainError(err), true);
    }
  });
  const purge = button(TS.purgeJournal, async () => {
    if (!(await askConfirm({ title: TS.purgeJournal, text: S.purgeAsk, ok: S.purgeOk }))) return;
    try {
      await api("/api/journal", { method: "DELETE" });
      say(actions, S.purged);
      bus.emit("journal:purged", {});
    } catch (err) {
      say(actions, err.status === 404 || err.status === 405 ? S.purgeMissing : explainError(err), true);
    }
  });
  actions.append(h("p", { class: "set-label", text: S.dataFolder }), h("p", { class: "set-path mono", text: model.data_dir || "" }),
                 h("div", { class: "set-actions" }, open, purge), status);
  return [h("p", { class: "set-where", text: TS.data }), actions, ...entriesOf("donnees").map(field)];
}

function browserName() {
  const brands = navigator.userAgentData?.brands || [];
  const real = brands.find(b => !/not.?a.?brand|chromium/i.test(b.brand)) || brands.find(b => /chromium/i.test(b.brand));
  if (real) return `${real.brand} ${real.version}`;
  const m = /(Edg|Chrome|Firefox)\/(\d+)/.exec(navigator.userAgent);
  return m ? `${m[1] === "Edg" ? "Microsoft Edge" : m[1]} ${m[2]}` : S.about.unknown;
}

function sectionApropos() {
  const v = model.versions || {};
  const engine = state.wakeEngine || wakeEngine();
  const rows = [
    [S.about.jarvis, v.jarvis || (aboutTries >= 2 ? S.about.unknown : S.about.checking)],
    [S.about.claude, v.claude_code || (aboutTries >= 2 ? S.about.unknown : S.about.checking)],
    [S.about.python, v.python || S.about.unknown],
    [S.about.voice, v.realtime_model || ""],
    [S.about.transcribe, v.transcribe_model || ""],
    [S.about.claudeModels, S.about.models(v.claude_models || {})],
    [S.about.browser, browserName()],
    [S.about.wake, engine === "local" ? S.about.wakeLocal : engine === "cloud" ? S.about.wakeCloud : S.about.wakeNone],
    // The dates that concern the models chosen above, as the health check says them.
    [S.about.deadlines, Array.isArray(model.deadlines)
      ? (model.deadlines.map(fr).join(" ") || S.about.noDeadline) : S.about.unknown],
    [S.about.update, S.about.updateText],
  ];
  const dl = h("dl", { class: "set-about" });
  for (const [k, value] of rows) dl.append(h("dt", { text: k }), h("dd", { text: fr(value) }));
  if ((!v.jarvis || !v.claude_code) && aboutTries < 2) {
    // Probed in the background by the server: ask again in a moment.
    setTimeout(async () => {
      aboutTries++;
      if (!dialog?.open || current !== "apropos") return;
      try { model.versions = (await api("/api/settings")).versions; renderSection("apropos"); } catch { /* keep */ }
    }, 3000);
  }
  return [dl];
}

/* Sections built by other modules: id -> build(ctx), which returns the nodes
   under the heading (ctx below). The built-in BUILDERS win over them. */
const registered = new Map();
export function registerSection(id, build) { registered.set(id, build); }

function sectionCtx() {
  return { model, field, entriesOf, askConfirm, say, h, button, api, remote: state.remote };
}

const BUILDERS = {
  connexion: sectionConnexion,
  voix: () => [h("p", { class: "set-note", text: S.voiceNote }), ...entriesOf("voix").map(field)],
  ecoute: sectionEcoute,
  proactivite: () => entriesOf("proactivite").map(field),
  claude: () => [h("p", { class: "set-note" }, lockIcon(), " ", TS.sensitive), ...entriesOf("claude").map(field)],
  couts: sectionCouts,
  systeme: sectionSysteme,
  donnees: sectionDonnees,
  apropos: sectionApropos,
};

function renderSection(id) {
  const panel = ui.panels.get(id);
  if (!panel || !model) return;
  const custom = registered.get(id);
  const kids = BUILDERS[id] ? BUILDERS[id]() : custom ? custom(sectionCtx()) : entriesOf(id).map(field);
  panel.replaceChildren(heading(id), ...kids);
  if (id === "couts") bus.emit("settings:section", { id, el: panel.querySelector("#settingsUsage") });
  if (id === "connexion") loadHealth(false);
}

export function showSection(id) {
  if (!ui.panels.has(id)) id = "voix";
  current = id;
  prefs.set("settingsSection", id);
  for (const [key, panel] of ui.panels) panel.hidden = key !== id;
  for (const b of ui.nav.querySelectorAll("button")) {
    if (b.dataset.section === id) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
  }
  // À propos follows what changed in the other sections (models, their deadlines).
  if (model && (id === "apropos" || !ui.panels.get(id).childElementCount)) renderSection(id);
}

function renderRestart() {
  const keys = model?.restart || [];
  const names = keys.map(k => fr(model.schema.find(e => e.key === k)?.label || k));
  ui.restart.hidden = !names.length;
  ui.restart.textContent = names.length ? S.restart(names.join(", ")) : "";
}

async function loadHealth(force) {
  if (!ui?.health) return;
  ui.health.replaceChildren(checkRow({ state: "wait", title: S.checking }));
  if (ui.recheck) ui.recheck.disabled = true;
  try {
    // Here, the key's fix points at the form just above, not at 'Réglages › Connexion'.
    const list = (await checkHealth(force)).map(c => (c && c.id === "openai" && typeof c.fix_fr === "string"
      ? { ...c, fix_fr: c.fix_fr.replace(/\s+dans Réglages\s*›\s*Connexion/g, ` ${S.keyAbove}`) } : c));
    renderChecks(list, ui.health);
  } catch {
    ui.health.replaceChildren(checkRow({ state: "fix", title: S.healthTitle, message: S.healthFailed }));
  } finally {
    if (ui.recheck) ui.recheck.disabled = false;
  }
}

/* ---------------------------------------------------------- speaker */
function applySpeaker(id) {
  voice.setOutputDevice?.(id || "");  // JARVIS's voice in conversations
  try {
    const ac = audio();  // the earcons
    ac.setSinkId?.(id || "")?.catch?.(() => {});
  } catch { /* not supported */ }
}

/* ---------------------------------------------------------- the dialog */
function build() {
  dialog = $("settingsDialog");
  if (!dialog) return false;
  const close = button("✕", () => dialog.close(), "x");
  close.setAttribute("aria-label", S.close);
  const head = h("header", { class: "set-head" }, h("h2", { id: "settingsTitle", text: S.title }), close);
  const restart = h("p", { class: "set-restart", role: "status", hidden: true });
  const nav = h("nav", { class: "set-nav", "aria-label": S.nav });
  const panels = new Map();
  const content = h("div", { class: "set-panels" });
  const loading = h("p", { class: "set-loading", role: "status", text: S.loading });
  content.append(loading);
  for (const id of SECTION_ORDER) {
    const panel = h("section", { class: "set-section", id: `set-sec-${id}`, "aria-labelledby": `set-h-${id}`, hidden: true });
    panels.set(id, panel);
    content.append(panel);
  }
  // The confirmation, a dialog of its own over the drawer.
  const confirm = h("dialog", { class: "set-confirm", id: "settingsConfirm", "aria-labelledby": "settingsConfirmTitle" });
  const form = h("form", { method: "dialog" },
    h("h3", { id: "settingsConfirmTitle" }),
    h("p", { class: "set-confirm-text" }),
    h("p", { class: "set-confirm-value mono" }),
    h("p", { class: "set-danger", hidden: true }),
    h("div", { class: "set-actions" },
      h("button", { type: "submit", value: "cancel", class: "ctl", text: S.cancel }),
      h("button", { type: "submit", value: "ok", class: "ctl primary" })));
  confirm.append(form);
  document.body.append(confirm);
  dialog.replaceChildren(head, restart, h("div", { class: "set-body" }, nav, content));
  dialog.addEventListener("close", onClose);
  ui = { nav, panels, content, loading, restart, confirm, health: null, recheck: null };
  return true;
}

function renderNav() {
  ui.nav.replaceChildren(...model.sections.map(s => {
    const b = button(fr(s.title), () => showSection(s.id), "set-tab");
    b.dataset.section = s.id;
    b.setAttribute("aria-controls", `set-sec-${s.id}`);
    return b;
  }));
}

export async function openSettings(section) {
  if (!dialog && !build()) return;
  if (!dialog.open) {
    invoker = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
    dialog.showModal();
  }
  ui.loading.hidden = false;
  ui.loading.textContent = S.loading;
  aboutTries = 0;
  for (const panel of ui.panels.values()) { panel.hidden = true; panel.replaceChildren(); }
  try {
    model = await api("/api/settings");
  } catch {
    ui.loading.textContent = S.loadFailed;
    return;
  }
  ui.loading.hidden = true;
  renderNav();
  renderRestart();
  const wanted = section || (model.key?.present ? current : "connexion");
  showSection(wanted);
  ui.nav.querySelector('[aria-current="true"]')?.focus();
}

function onClose() {
  if (deviceWatch) {
    navigator.mediaDevices?.removeEventListener?.("devicechange", deviceWatch);
    deviceWatch = null;
  }
  bus.emit("ui:close", "settings");
  const back = invoker;
  invoker = null;
  if (back && back.isConnected) back.focus();
}

export function isOpen() { return !!dialog?.open; }

export function init() {
  bus.on("ui:open", (name) => { if (name === "settings") openSettings(); });
  bus.on("settings:open", ({ section } = {}) => openSettings(section));
  // The chosen output device, for JARVIS's voice from the start; the earcons
  // get it once the sound starts (an AudioContext needs a click first).
  const speaker = prefs.get("speakerId", "");
  if (speaker) {
    voice.setOutputDevice?.(speaker);
    const off = bus.on("mode", ({ mode } = {}) => {
      if (mode === "connecting" || mode === "live") { off(); applySpeaker(prefs.get("speakerId", "")); }
    });
  }
  bus.emit("ui:ready", "settings");  // the 'Réglages' button shows only now: never a dead button
}
