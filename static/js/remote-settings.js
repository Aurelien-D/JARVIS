/* Owned by package A4 (JARVIS on iPhone): Réglages › Accès à distance, added
   with settings.registerSection().
   - On the PC: Tailscale's state, the remote access switch, « Publier sur
     Tailscale » and the Serve check, pairing an iPhone (URL, QR code, the
     requests with their codes), the paired devices, the full-access opt-in
     from the iPhone and the recent activity.
   - On the paired iPhone: its name, pausing remote access (after a
     confirmation: only the PC can switch it back on before the pause ends)
     and forgetting this iPhone.
   - Rendering rule (P0): every string that comes from a device, from whois or
     from the audit (name, OS, host name, login, title, audit text, request
     code) goes into the page through textContent or createElement, never
     md() nor markup strings: the PC page holds the token that runs tasks.
   - Hooks for other packages: each device row has an empty
     [data-slot="siri"] element and the phone view a [data-slot="raccourci"]
     one; the bus says when they are drawn ("remote:devices", "remote:phone").
   - B3 fills each device's Siri slot here: [Créer une clé Siri], its keys
     and [Révoquer]; the phone's « Assistant raccourci » is siri-ui.js. */
import { api, bus } from "./core.js";
import { T, explainError, fmtElapsed, fmtRelative, fmtTime, fr } from "./strings-fr.js";
import { button, h } from "./onboarding.js";
import { askConfirm, registerSection, showSection } from "./settings.js";

const R = T.remote;
const S = T.siri;
const MAX_SIRI_KEYS = 2;  // devices.MAX_SIRI_KEYS
// qrcode-generator, pinned with its integrity hash and loaded only when pairing opens.
const QR = {
  src: "https://cdn.jsdelivr.net/npm/qrcode-generator@2.0.4/dist/qrcode.js",
  integrity: "sha384-e9EFD6BGC90bkW9aDV5xbbBfzwN7G8YImHao2lfLVKV/hPB0E0go+H3I64h7oHtA",
};
const QR_TIMEOUT_MS = 15000;
const CONSENT_RE = /^https:\/\/login\.tailscale\.com\/[\w\-/?=&.%]+$/;
const POLL_MS = 3000;  // the pairing requests, while pairing is open
// Personal Tailscale logins (spec 3.16b); anything else gets an information line.
const PERSONAL = new Set(["gmail.com", "googlemail.com", "icloud.com", "me.com", "mac.com",
  "privaterelay.appleid.com", "outlook.com",
  "outlook.fr", "hotmail.com", "hotmail.fr", "live.com", "live.fr", "msn.com", "yahoo.com", "yahoo.fr",
  "proton.me", "protonmail.com", "gmx.fr", "gmx.com", "laposte.net", "orange.fr", "free.fr", "sfr.fr"]);
const COMPLET = [["never", "never"], ["24h", "day"], ["7d", "week"]];

let view = null;     // the section on screen: { root, blocks, st, serve, ... }
let qrLoading = null;
let pauseDialog = null;

/* ---------------------------------------------------------- helpers */
function now() { return Date.now(); }

function isPersonal(login) {
  const text = String(login || "").toLowerCase();
  if (!text) return true;  // nothing detected: nothing to say
  if (/@(github|passkey)$/.test(text)) return true;
  return PERSONAL.has(text.split("@").pop());
}

function quoted(text) { return R.quoted(String(text ?? "")); }

function onScreen(v) {
  return v === view && v.root.isConnected && !v.root.closest("[hidden]") && !!v.root.closest("dialog[open]");
}

function unavailable(err) {
  return h("p", { class: "set-note", text: err?.status === 501 ? R.notYet : explainError(err) });
}

function block(key) { return h("div", { class: "set-field rm-block", "data-key": key }); }

function status(v, key) {
  const note = v.notes[key];
  return h("p", { class: note?.error ? "set-status err" : "set-status", role: "status", text: note?.text || "" });
}

/* A short message under a block, kept when the block is drawn again. */
function note(v, key, text, error = false) {
  v.notes[key] = text ? { text: fr(text), error } : null;
  const el = v.blocks?.[key]?.querySelector(":scope > .set-status");
  if (el) { el.className = error ? "set-status err" : "set-status"; el.textContent = text ? fr(text) : ""; }
}

/* Redraws a block unless nothing changed or monsieur is typing in it; the
   focus stays on the control that had it. */
function draw(v, key, data, build) {
  const el = v.blocks?.[key];
  if (!el || v.editing.has(key)) return;
  const sig = JSON.stringify([data, v.notes[key]]);
  if (el._sig === sig) return;
  el._sig = sig;
  const focused = el.contains(document.activeElement) ? document.activeElement.id : "";
  el.replaceChildren(...build().filter(Boolean), status(v, key));
  if (focused) document.getElementById(focused)?.focus();
}

function redraw(v, key) { if (v.blocks?.[key]) v.blocks[key]._sig = ""; }

function copyButton(id, text, v, key) {
  const b = button(R.copy, async () => {
    try {
      await navigator.clipboard.writeText(text);
      note(v, key, R.copied);
    } catch {
      note(v, key, R.copyFailed, true);
    }
  }, "ctl rm-copy");
  b.id = id;
  return b;
}

function dateLong(epoch) {
  const d = new Date(Number(epoch) * 1000);
  return `${d.toLocaleDateString("fr-FR", { day: "numeric", month: "long" })}, ${fmtTime(d)}`;
}

/* When a pause ends, with its day (as remote._until_text): '15 h 30' today,
   'demain à 14 h 30' tomorrow, else the date. A 24 h pause never reads as 'now'. */
function untilText(epoch) {
  const d = new Date(Number(epoch) * 1000), today = new Date(now());
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(d) - day(today)) / 86400e3);
  return days <= 0 ? fmtTime(d) : days === 1 ? T.time.tomorrow(fmtTime(d)) : dateLong(epoch);
}

function heading(text, id) { return h("h4", { class: "set-sub", id, text }); }

/* ---------------------------------------------------------- the QR code */
function loadQr() {
  if (window.qrcode) return Promise.resolve(window.qrcode);
  if (!qrLoading) {
    qrLoading = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = QR.src;
      s.integrity = QR.integrity;
      s.crossOrigin = "anonymous";
      s.referrerPolicy = "no-referrer";
      s.async = true;
      s.dataset.lazy = "qrcode";
      s.onload = () => resolve(window.qrcode || null);
      s.onerror = () => { s.remove(); qrLoading = null; resolve(null); };
      document.head.append(s);  // never in <body>: the page's own content stays script-free
    });
  }
  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), QR_TIMEOUT_MS));
  return Promise.race([qrLoading, timeout]);
}

/* The URL as an SVG drawn module by module (never the library's own HTML). */
function qrSvg(lib, url) {
  const qr = lib(0, "M");
  qr.addData(url);
  qr.make();
  const n = qr.getModuleCount(), quiet = 4, size = n + quiet * 2;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  svg.setAttribute("class", "rm-qr-svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", R.qrLabel(url));
  svg.setAttribute("shape-rendering", "crispEdges");
  const bg = document.createElementNS(NS, "rect");
  bg.setAttribute("width", String(size));
  bg.setAttribute("height", String(size));
  bg.setAttribute("fill", "#ffffff");
  let d = "";
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + quiet} ${r + quiet}h1v1h-1z`;
  }
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "#000000");
  svg.append(bg, path);
  return svg;
}

async function drawQr(box, url) {
  box.replaceChildren(h("p", { class: "set-help", text: R.qrLoading }));
  const lib = await loadQr();
  if (!box.isConnected) return;
  try {
    if (!lib) throw new Error("qrcode");
    box.replaceChildren(qrSvg(lib, url));
  } catch {
    box.replaceChildren(h("p", { class: "set-help rm-qr-fallback", text: R.qrFallback }));
  }
}

/* ---------------------------------------------------------- PC: loading */
async function loadPc(v) {
  let st;
  try {
    st = await api("/api/remote/state");
  } catch (err) {
    if (v === view) v.root.replaceChildren(unavailable(err));
    return;
  }
  if (v !== view) return;
  v.st = st;
  v.blocks = Object.fromEntries(["status", "enabled", "serve", "pairing", "devices", "complet", "audit"]
    .map(key => [key, block(`remote-${key}`)]));
  v.root.replaceChildren(...Object.values(v.blocks));
  renderPc(v);
  loadServe(v);
  if (onScreen(v)) watch(v);
}

async function refresh(v) {
  if (v !== view || v.refreshing) return;
  v.refreshing = true;
  try {
    const st = await api("/api/remote/state");
    if (v === view) { v.st = st; renderPc(v); }
  } catch { /* the next event or poll */ } finally {
    v.refreshing = false;
  }
}

async function loadServe(v) {
  try {
    v.serve = await api("/api/remote/serve");
  } catch (err) {
    v.serve = { state: "", detail: err?.status === 501 ? R.notYet : explainError(err) };
  }
  if (v === view) renderServe(v);
}

function renderPc(v) {
  renderStatus(v);
  renderSwitch(v);
  renderServe(v);
  renderPairing(v);
  renderDevices(v);
  renderComplet(v);
  renderAudit(v);
  watch(v);
}

/* ---------------------------------------------------------- PC: 1. status */
function renderStatus(v) {
  const st = v.st, ts = st.tailscale || {};
  const logins = Array.isArray(st.logins) ? st.logins : [];
  draw(v, "status", [ts, st.host, st.host_source, logins, st.logins_source, st.cap_ok, st.listener], () => {
    const rows = h("dl", { class: "set-about rm-status" });
    const row = (label, ...value) => rows.append(h("dt", { text: label }), h("dd", {}, ...value));
    row(R.tailscale, !ts.installed ? R.tsMissing : !ts.running ? R.tsStopped : R.tsRunning);
    row(R.address, st.host ? h("span", { class: "mono", text: st.host }) : R.noHost,
        st.host ? h("span", { class: "rm-source", text: ` ${R.hostSource[st.host_source] || ""}` }) : null);
    if (!logins.length) row(R.account, R.noLogin);
    for (const login of logins) {
      row(R.account, h("span", { class: "mono", text: login }),
          h("span", { class: "rm-source", text: ` ${R.loginSource[st.logins_source] || ""}` }));
    }
    const out = [heading(R.statusTitle), rows];
    if (!isPersonal(ts.login || logins[0])) out.push(h("p", { class: "set-help rm-personal", text: R.personal }));
    if (st.cap_ok === false) {
      out.push(h("p", { class: "set-danger", text: R.capMissing }),
               h("div", { class: "set-actions" }, button(R.setCap, () => showSection("couts"))));
    }
    if (st.listener?.error) out.push(h("p", { class: "set-danger", text: String(st.listener.error) }));
    return out;
  });
}

/* ---------------------------------------------------------- PC: 2. the switch */
function renderSwitch(v) {
  const st = v.st;
  const paused = Number(st.paused_until) * 1000 > now();
  draw(v, "enabled", [st.enabled, st.ready, paused, st.paused_until], () => {
    const id = "set-remote-enabled";
    const input = h("input", { type: "checkbox", id, class: "set-checkbox", checked: !!st.enabled,
                               disabled: st.ready === false, "aria-describedby": `${id}-help` });
    input.addEventListener("change", () => toggle(v, input));
    const out = [h("label", { class: "set-check", for: id }, input, " ", R.switchLabel),
                 h("p", { class: "set-help", id: `${id}-help`, text: R.switchHelp })];
    if (st.ready === false) out.push(h("p", { class: "set-note", text: R.notReady }));
    if (paused) {
      const resume = button(R.resume, () => resumeNow(v));
      resume.id = "rm-resume";
      out.push(h("p", { class: "set-note", text: R.pausedUntil(untilText(st.paused_until)) }),
               h("div", { class: "set-actions" }, resume));
    }
    return out;
  });
}

async function toggle(v, input) {
  const on = input.checked, st = v.st;
  input.disabled = true;
  note(v, "enabled", on ? R.switching : "");
  try {
    const body = on ? { enabled: true, host: st.host || undefined, login: (st.logins || [])[0] || undefined }
                    : { enabled: false };
    const next = await api("/api/remote/state", { method: "POST", body });
    note(v, "enabled", on ? R.switchedOn : R.switchedOff);
    if (v === view) { v.st = { ...v.st, ...next }; renderPc(v); }
  } catch (err) {
    input.checked = !on;
    note(v, "enabled", explainError(err), true);
  } finally {
    input.disabled = v.st.ready === false;
  }
}

async function resumeNow(v) {
  try {
    const r = await api("/api/remote/pause", { method: "POST", body: { hours: 0 } });
    v.st.paused_until = r.paused_until || 0;
    note(v, "enabled", R.resumed);
    renderPc(v);
  } catch (err) {
    note(v, "enabled", explainError(err), true);
  }
}

/* ---------------------------------------------------------- PC: 3. Publier sur Tailscale */
function renderServe(v) {
  const serve = v.serve, st = v.st || {};
  const command = serve?.command || st.serve_command || "";
  draw(v, "serve", [serve, v.publish, v.publishing, command], () => {
    const publish = button(v.publishing ? R.publishing : R.publish, () => publishNow(v), "ctl primary");
    publish.id = "rm-publish";
    publish.disabled = !!v.publishing;
    const actions = h("div", { class: "set-actions" }, publish);
    if (serve?.state === "ready") {
      const withdraw = button(R.unpublish, () => unpublishNow(v));
      withdraw.id = "rm-unpublish";
      actions.append(withdraw);
    }
    const out = [heading(R.serveTitle), h("p", { class: "set-help", text: R.serveHelp }), actions];
    const result = v.publish;
    if (result) {
      if (result.state === "consent" && CONSENT_RE.test(String(result.consent_url || ""))) {
        out.push(h("p", { class: "set-note rm-consent", text: R.consent }),
                 h("p", {}, h("a", { class: "rm-link", href: result.consent_url, target: "_blank",
                                     rel: "noopener noreferrer", text: R.consentLink })),
                 h("p", { class: "mono rm-url-small", text: result.consent_url }));
      } else if (result.state === "consent") {
        out.push(h("p", { class: "set-danger", text: R.consentBad }));
      } else if (result.ok) {
        out.push(h("p", { class: "set-status", text: R.published }));
      } else {
        out.push(h("p", { class: "set-danger rm-publish-error", text: String(result.error || R.publishFailed) }));
      }
    }
    const check = button(R.recheck, () => { v.serve = null; renderServe(v); loadServe(v); });
    check.id = "rm-serve-check";
    const word = serve ? (R.serveStates[serve.state] || R.serveStates.unknown) : R.checking;
    out.push(h("div", { class: "rm-serve-line" },
                 h("p", { class: `rm-serve rm-serve-${serve?.state || "wait"}`, text: R.serveLine(word) }), check));
    if (serve?.detail) out.push(h("p", { class: "set-help rm-serve-detail", text: String(serve.detail) }));
    // A danger: the command that removes just it, on its own line with Copier
    // (in the sentence above it would wrap anywhere, « -- » included).
    if (serve?.fix) {
      out.push(h("p", { class: "set-label", text: R.fixLabel }),
               h("div", { class: "rm-command" }, h("code", { class: "mono", text: String(serve.fix) }),
                 copyButton("rm-copy-fix", String(serve.fix), v, "serve")));
      if (serve.fix_full) {
        out.push(h("p", { class: "set-help", text: R.manualFull }),
                 h("div", { class: "rm-command" }, h("code", { class: "mono", text: String(serve.fix_full) }),
                   copyButton("rm-copy-fix-full", String(serve.fix_full), v, "serve")));
      }
    }
    if (command) {
      const full = serve?.command_full || "";
      out.push(h("p", { class: "set-label", text: R.manual }),
               h("p", { class: "set-help", text: R.manualHelp }),
               h("div", { class: "rm-command" }, h("code", { class: "mono", text: command }),
                 copyButton("rm-copy-command", command, v, "serve")));
      if (full) {
        out.push(h("p", { class: "set-help", text: R.manualFull }),
                 h("div", { class: "rm-command" }, h("code", { class: "mono", text: full }),
                   copyButton("rm-copy-command-full", full, v, "serve")));
      }
    }
    return out;
  });
}

async function publishNow(v) {
  v.publishing = true;
  v.publish = null;
  note(v, "serve", "");
  renderServe(v);
  try {
    v.publish = await api("/api/remote/serve/publish", { method: "POST" });
  } catch (err) {
    v.publish = { ok: false, state: "error", error: explainError(err) };
  }
  v.publishing = false;
  if (v !== view) return;
  renderServe(v);
  loadServe(v);
}

async function unpublishNow(v) {
  try {
    const r = await api("/api/remote/serve/unpublish", { method: "POST" });
    v.publish = null;
    note(v, "serve", r.ok ? R.unpublished : String(r.error || R.publishFailed), !r.ok);
  } catch (err) {
    note(v, "serve", explainError(err), true);
  }
  loadServe(v);
}

/* ---------------------------------------------------------- PC: 4. pairing */
function pairingOpen(v) { return Number(v.st?.pairing_until) * 1000 > now(); }

function renderPairing(v) {
  const st = v.st, open = pairingOpen(v);
  const url = v.pairUrl || st.url || "";
  const requests = (Array.isArray(st.requests) ? st.requests : []).filter(r => r.status === "waiting" || r.status === "approved");
  // Not the seconds left of each request: a poll must not redraw what did not change.
  const shown = requests.map(r => [r.id, r.code, r.name, r.ip, r.login, r.os, r.host_name, r.status]);
  draw(v, "pairing", [open, url, shown, st.pairing_until], () => {
    const out = [heading(R.pairTitle)];
    if (!open) {
      const go = button(R.pair, () => openPairing(v), "ctl primary");
      go.id = "rm-pair-open";
      out.push(h("p", { class: "set-help", text: R.pairHelp }), h("div", { class: "set-actions" }, go));
      return out;
    }
    const close = button(R.pairClose, () => closePairing(v));
    close.id = "rm-pair-close";
    out.push(h("p", { class: "rm-countdown", id: "rm-countdown", role: "timer", text: countdown(v) }),
             h("p", { class: "set-help", text: R.pairSteps }));
    if (url) {
      const qr = h("div", { class: "rm-qr" });
      out.push(h("div", { class: "rm-command" }, h("code", { class: "mono rm-url", text: url }),
                 copyButton("rm-copy-url", url, v, "pairing")), qr);
      drawQr(qr, url);
    }
    const waiting = requests.filter(r => r.status === "waiting");
    if (waiting.length > 1) out.push(h("p", { class: "set-danger rm-several", text: R.several }));
    out.push(requests.length
      ? h("ul", { class: "rm-requests", "aria-label": R.requestsLabel }, ...requests.map(r => requestRow(v, r, waiting.length)))
      : h("p", { class: "set-help rm-none", text: R.noRequest }));
    out.push(h("div", { class: "set-actions" }, close));
    return out;
  });
}

function countdown(v) {
  const left = Math.max(0, Math.round(Number(v.st?.pairing_until || 0) - now() / 1000));
  return R.pairOpenFor(fmtElapsed(left));
}

/* One request: the whois data in separate cells, the code, the requested name in « ». */
function requestRow(v, r, waitingCount) {
  const id = String(r.id || "");
  const safe = id.replace(/[^\w-]/g, "");
  const cells = [r.os, r.host_name, r.ip, r.login].map(x => h("span", { class: "rm-cell", text: String(x || "—") }));
  const line = h("p", { class: "rm-cells" });
  cells.forEach((cell, i) => { if (i) line.append(" · "); line.append(cell); });
  const li = h("li", { class: "rm-request", "data-request": id },
    line,
    h("p", { class: "rm-code-line" }, R.codeIs, " ", h("strong", { class: "rm-code mono", text: String(r.code || "") })),
    h("p", { class: "rm-name", text: quoted(r.name) }));
  const rowStatus = h("p", { class: "set-status", role: "status" });
  if (r.status === "approved") {
    li.append(h("p", { class: "set-help", text: R.approvedWait }));
    return li;
  }
  const allow = button(R.allow, () => {
    if (waitingCount > 1) { askCode(v, li, r, rowStatus); return; }
    allowRequest(v, r, null, rowStatus);
  }, "ctl primary");
  allow.id = `rm-allow-${safe}`;  // stable ids: the focus survives a redraw
  allow.setAttribute("aria-label", R.allowLabel(String(r.code || "")));
  const deny = button(R.deny, () => denyRequest(v, r, rowStatus));
  deny.id = `rm-deny-${safe}`;
  deny.setAttribute("aria-label", R.denyLabel(String(r.code || "")));
  li.append(h("div", { class: "set-actions" }, allow, deny), rowStatus);
  return li;
}

/* Several requests wait: the PC types the code the iPhone shows. */
function askCode(v, li, r, rowStatus) {
  if (li.querySelector(".rm-code-form")) return;
  v.editing.add("pairing");
  const id = `rm-code-${String(r.id || "").replace(/[^\w-]/g, "")}`;
  const input = h("input", { id, class: "set-input mono rm-code-input", type: "text", inputmode: "numeric",
                             autocomplete: "off", maxlength: "4", pattern: "[0-9]{4}" });
  const ok = h("button", { type: "submit", class: "ctl primary", text: R.allow });
  // The row's first [Autoriser] [Refuser] give way to the code's own buttons.
  const first = li.querySelector(":scope > .set-actions");
  const cancel = button(R.cancel, () => {
    form.remove();
    first?.removeAttribute("hidden");
    v.editing.delete("pairing");
    renderPairing(v);
  });
  const form = h("form", { class: "rm-code-form" },
    h("label", { class: "set-label", for: id, text: R.typeCode }), input, h("div", { class: "set-actions" }, ok, cancel));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const code = input.value.trim();
    if (!/^\d{4}$/.test(code)) {
      rowStatus.className = "set-status err";
      rowStatus.textContent = R.codeFormat;
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return;
    }
    allowRequest(v, r, code, rowStatus);
  });
  first?.setAttribute("hidden", "");
  li.insertBefore(form, rowStatus);
  input.focus();
}

async function allowRequest(v, r, code, rowStatus) {
  try {
    const body = code ? { code } : {};
    await api(`/api/remote/pair-requests/${encodeURIComponent(r.id)}/allow`, { method: "POST", body });
    v.editing.delete("pairing");
    note(v, "pairing", R.allowed(String(r.code || "")));
    redraw(v, "pairing");
    refresh(v);
  } catch (err) {
    rowStatus.className = "set-status err";
    rowStatus.textContent = explainError(err);
  }
}

async function denyRequest(v, r, rowStatus) {
  try {
    await api(`/api/remote/pair-requests/${encodeURIComponent(r.id)}/deny`, { method: "POST" });
    v.editing.delete("pairing");
    note(v, "pairing", R.denied(String(r.code || "")));
    redraw(v, "pairing");
    refresh(v);
  } catch (err) {
    rowStatus.className = "set-status err";
    rowStatus.textContent = explainError(err);
  }
}

async function openPairing(v) {
  try {
    const r = await api("/api/remote/pairing", { method: "POST", body: { open: true } });
    v.st.pairing_until = r.until || 0;
    v.pairUrl = r.url || "";
    note(v, "pairing", "");
    renderPc(v);
    document.getElementById("rm-pair-close")?.focus();
    refresh(v);  // the requests already waiting
  } catch (err) {
    note(v, "pairing", explainError(err), true);
  }
}

async function closePairing(v) {
  try {
    await api("/api/remote/pairing", { method: "POST", body: { open: false } });
  } catch { /* closes by itself after 10 minutes */ }
  v.st.pairing_until = 0;
  v.editing.delete("pairing");
  note(v, "pairing", R.pairClosed);
  renderPc(v);
  document.getElementById("rm-pair-open")?.focus();
}

/* While pairing is open and the section shows: a 1 s countdown and a 3 s poll
   (the 'remote' event also says when a request arrives). */
function watch(v) {
  const open = v === view && pairingOpen(v) && onScreen(v);
  if (open && !v.tick) {
    v.tick = setInterval(() => {
      if (!onScreen(v)) { unwatch(v); return; }
      if (!pairingOpen(v)) { unwatch(v); refresh(v); return; }
      const el = document.getElementById("rm-countdown");
      if (el) el.textContent = countdown(v);
    }, 1000);
    v.poll = setInterval(() => { if (onScreen(v)) refresh(v); }, POLL_MS);
  } else if (!open) {
    unwatch(v);
  }
}

function unwatch(v) {
  clearInterval(v.tick);
  clearInterval(v.poll);
  v.tick = v.poll = 0;
}

/* ---------------------------------------------------------- PC: 5. devices */
function renderDevices(v) {
  const list = Array.isArray(v.st.devices) ? v.st.devices : [];
  draw(v, "devices", [list, v.siri], () => {
    const out = [heading(R.devicesTitle)];
    if (!list.length) { out.push(h("p", { class: "set-help rm-none", text: R.noDevices })); return out; }
    const rows = list.map(d => deviceRow(v, d));
    out.push(h("ul", { class: "rm-devices" }, ...rows.map(r => r.li)));
    queueMicrotask(() => bus.emit("remote:devices", { devices: rows.map(r => ({ device: r.device, slot: r.slot })) }));
    return out;
  });
}

function deviceRow(v, d) {
  const id = String(d.id || "");
  const name = String(d.name || "");
  const safe = id.replace(/[^\w-]/g, "");
  const rename = button(R.rename, () => renameForm(v, li, d));
  rename.id = `rm-rename-btn-${safe}`;
  rename.setAttribute("aria-label", R.renameLabel(name));
  const remove = button(R.revoke, () => revoke(v, d));
  remove.id = `rm-revoke-${safe}`;
  remove.setAttribute("aria-label", R.revokeLabel(name));
  const meta = h("p", { class: "rm-device-meta" });
  [d.login, d.ip].filter(Boolean).forEach((x, i) => {
    if (i) meta.append(" · ");
    meta.append(h("span", { class: "mono", text: String(x) }));
  });
  const seen = d.last_seen ? R.lastSeen(fmtRelative(Number(d.last_seen) * 1000)) : R.neverSeen;
  const slot = h("div", { class: "rm-device-extra", "data-slot": "siri", "data-device": id });
  slot.append(...siriKeys(v, d));
  const li = h("li", { class: "rm-device", "data-device": id },
    h("div", { class: "rm-device-head" }, h("strong", { class: "rm-device-name", text: name }), rename),
    meta,
    h("p", { class: "set-help" }, ...(d.paired_at ? [R.pairedOn(dateLong(d.paired_at)), " · "] : []), seen),
    h("div", { class: "set-actions" }, remove),
    slot);
  return { li, device: d, slot };
}

function renameForm(v, li, d) {
  if (li.querySelector(".rm-rename")) return;
  v.editing.add("devices");
  const id = `rm-rename-${String(d.id || "").replace(/[^\w-]/g, "")}`;
  const input = h("input", { id, class: "set-input", type: "text", maxlength: "24", value: String(d.name || ""),
                             autocomplete: "off" });
  const status = h("p", { class: "set-status", role: "status" });
  const done = () => { v.editing.delete("devices"); redraw(v, "devices"); renderDevices(v); };
  const form = h("form", { class: "rm-rename" }, h("label", { class: "set-label", for: id, text: R.newName }), input,
    h("div", { class: "set-actions" }, h("button", { type: "submit", class: "ctl primary", text: R.save }),
      button(R.cancel, done)), status);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const device = await api(`/api/remote/devices/${encodeURIComponent(d.id)}`, { method: "PATCH",
                                                                                   body: { name: input.value.trim() } });
      v.st.devices = (v.st.devices || []).map(x => (x.id === d.id ? { ...x, ...device } : x));
      note(v, "devices", R.renamed(String(device.name || "")));
      done();
    } catch (err) {
      status.className = "set-status err";
      status.textContent = explainError(err);
    }
  });
  li.querySelector(".rm-device-head").after(form);
  input.focus();
  input.select();
}

async function revoke(v, d) {
  const name = String(d.name || "");
  const ok = await askConfirm({ title: R.revokeTitle(name), text: R.revokeAsk, ok: R.revoke, danger: R.revokeWarn });
  if (!ok) return;
  try {
    const r = await api(`/api/remote/devices/${encodeURIComponent(d.id)}`, { method: "DELETE" });
    v.st.devices = (v.st.devices || []).filter(x => x.id !== d.id);
    note(v, "devices", R.revoked(name, String(r.cancelled_tasks || 0)) + (r.topic_renewed ? ` ${R.topicRenewed}` : ""));
    renderDevices(v);
    refresh(v);
  } catch (err) {
    note(v, "devices", explainError(err), true);
  }
}

/* ---------------------------------------------------------- PC: 5b. Siri keys (B3) */
/* Under a device: its active Siri keys with [Révoquer], [Créer une clé Siri]
   and what the last action said. The secret never comes to the PC page: it
   waits on the server for this iPhone's own « Assistant raccourci ». */
function siriKeys(v, d) {
  const id = String(d.id || "");
  const safe = id.replace(/[^\w-]/g, "");
  const keys = (Array.isArray(d.siri_keys) ? d.siri_keys : []).filter(k => k && !k.revoked);
  const said = v.siri[id];
  const create = button(said?.busy ? S.creating : S.create, () => createSiriKey(v, d), "ctl");
  create.id = `rm-siri-create-${safe}`;
  create.disabled = !!said?.busy || keys.length >= MAX_SIRI_KEYS;
  const out = [h("p", { class: "set-label siri-title", text: S.title })];
  if (keys.length) {
    out.push(h("ul", { class: "siri-keys", "aria-label": S.title }, ...keys.map((k) => {
      const kid = String(k.id || "");
      const created = k.created ? dateLong(k.created) : "";
      const revoke = button(S.revoke, () => revokeSiriKey(v, d, k), "ctl");
      revoke.id = `rm-siri-revoke-${kid.replace(/[^\w-]/g, "")}`;
      revoke.setAttribute("aria-label", S.revokeLabel(created));
      const used = k.last_used ? S.keyUsed(fmtRelative(Number(k.last_used) * 1000)) : S.keyUnused;
      return h("li", { class: "siri-key", "data-key": kid },
        h("span", { class: "siri-key-text" }, h("span", { class: "mono", text: S.keyLabel(kid) }),
          " · ", created ? S.keyCreated(created) : "", created ? " · " : "", used),
        revoke);
    })));
  } else {
    out.push(h("p", { class: "set-help", text: S.createHelp }));
  }
  out.push(h("div", { class: "set-actions" }, create));
  if (keys.length >= MAX_SIRI_KEYS) out.push(h("p", { class: "set-help", text: S.maxKeys(String(MAX_SIRI_KEYS)) }));
  if (said?.text) {
    out.push(h("p", { class: said.error ? "set-status err siri-said" : "set-status siri-said", role: "status",
                      text: said.text }));
  }
  return out;
}

function siriSay(v, id, text, error = false, busy = false) {
  v.siri = { ...v.siri, [id]: { text, error, busy } };
  renderDevices(v);
}

async function createSiriKey(v, d) {
  const id = String(d.id || "");
  siriSay(v, id, "", false, true);
  try {
    const r = await api(`/api/remote/devices/${encodeURIComponent(id)}/siri-key`, { method: "POST" });
    if (v !== view) return;
    siriSay(v, id, S.created(fmtTime(new Date(Number(r.handoff_until) * 1000))));
    refresh(v);
  } catch (err) {
    if (v === view) siriSay(v, id, explainError(err), true);
  }
}

async function revokeSiriKey(v, d, k) {
  const id = String(d.id || "");
  const ok = await askConfirm({ title: S.revokeTitle, text: S.revokeAsk, ok: S.revoke });
  if (!ok) return;
  try {
    await api(`/api/remote/siri-keys/${encodeURIComponent(String(k.id || ""))}`, { method: "DELETE" });
    if (v !== view) return;
    v.st.devices = (v.st.devices || []).map(x => (x.id === d.id ? {
      ...x, siri_keys: (x.siri_keys || []).map(y => (y.id === k.id ? { ...y, revoked: true } : y)) } : x));
    siriSay(v, id, S.revoked);
    refresh(v);
  } catch (err) {
    if (v === view) siriSay(v, id, explainError(err), true);
  }
}

/* ---------------------------------------------------------- PC: 6. full access from the iPhone */
function completChoice(until) {
  const left = Number(until) * 1000 - now();
  return left <= 0 ? "never" : left <= 24 * 3600e3 + 60e3 ? "24h" : "7d";
}

function renderComplet(v) {
  const until = Number(v.st.complet_until) || 0;
  draw(v, "complet", [until, completChoice(until)], () => {
    const current = completChoice(until);
    const radios = COMPLET.map(([value, key]) => {
      const input = h("input", { type: "radio", name: "rm-complet", value, id: `rm-complet-${value}`,
                                 class: "set-checkbox", checked: value === current });
      input.addEventListener("change", () => { if (input.checked) setComplet(v, value); });
      return h("label", { class: "set-check rm-radio", for: input.id }, input, " ", R.complet[key]);
    });
    return [h("fieldset", { class: "set-group" }, h("legend", { text: R.completTitle }),
                h("div", { class: "rm-radios" }, ...radios)),
            h("p", { class: "set-note rm-complet-now", text: until * 1000 > now() ? R.completUntil(dateLong(until))
                                                                                  : R.completNever }),
            h("p", { class: "set-help", text: R.completHelp })];
  });
}

async function setComplet(v, duration) {
  if (duration !== "never") {
    const label = R.complet[duration === "24h" ? "day" : "week"];
    const ok = await askConfirm({ title: R.completTitle, text: R.completAsk(label), ok: R.completOk });
    if (!ok) { redraw(v, "complet"); renderComplet(v); return; }
  }
  try {
    const r = await api("/api/remote/complet", { method: "POST", body: { duration } });
    v.st.complet_until = r.complet_until || 0;
    note(v, "complet", R.saved);
  } catch (err) {
    note(v, "complet", explainError(err), true);
  }
  redraw(v, "complet");
  renderComplet(v);
}

/* ---------------------------------------------------------- PC: 7. recent activity */
function renderAudit(v) {
  draw(v, "audit", [v.auditOpen, v.audit, v.auditError, (v.st.devices || []).map(d => [d.id, d.name])], () => {
    const toggle = button(R.auditTitle, () => {
      v.auditOpen = !v.auditOpen;
      renderAudit(v);
      if (v.auditOpen) loadAudit(v);
    }, "ctl rm-audit-toggle");
    toggle.id = "rm-audit-toggle";
    toggle.setAttribute("aria-expanded", v.auditOpen ? "true" : "false");
    toggle.setAttribute("aria-controls", "rm-audit-list");
    const out = [h("div", { class: "set-actions" }, toggle)];
    if (!v.auditOpen) return out;
    if (v.auditError) { out.push(h("p", { class: "set-danger", text: v.auditError })); return out; }
    if (!v.audit) { out.push(h("p", { class: "set-help", text: R.checking })); return out; }
    if (!v.audit.length) { out.push(h("p", { class: "set-help rm-none", text: R.auditEmpty })); return out; }
    const names = new Map((v.st.devices || []).map(d => [d.id, String(d.name || "")]));
    out.push(h("ol", { class: "rm-audit", id: "rm-audit-list", "aria-label": R.auditTitle },
      ...v.audit.slice(0, 50).map(line => auditRow(line, names))));
    const again = button(R.auditRefresh, () => loadAudit(v));
    again.id = "rm-audit-refresh";
    out.push(h("div", { class: "set-actions" }, again));
    return out;
  });
}

async function loadAudit(v) {
  try {
    const r = await api("/api/remote/audit?limit=50");
    v.audit = Array.isArray(r.lines) ? r.lines : [];
    v.auditError = "";
  } catch (err) {
    v.audit = null;
    v.auditError = err?.status === 501 ? R.notYet : explainError(err);
  }
  if (v === view) renderAudit(v);
}

/* One audit line in plain French: when, who, what, and how it ended. As text. */
function auditRow(line, names) {
  const when = Number(line.t) ? fmtRelative(Number(line.t) * 1000) : "";
  const device = String(line.device || "");
  // The PC's own actions (switch, opt-in, a Siri key made for a device) and the
  // alerts with no caller (the PC's switch, the Serve watch) are the PC's.
  const who = line.caller === "pc" ? R.callers.pc
    : names.get(device) || (line.caller ? R.callers[line.caller] || String(line.caller) : "")
    || (line.kind === "alert" ? R.callers.pc : R.callers.unpaired);
  const where = line.ip ? ` (${line.ip})` : "";
  const kind = R.kinds[line.kind] || String(line.kind || "");
  let what = line.text ? String(line.text) : "";
  if (!what && line.kind === "request") what = [line.method, line.route].filter(Boolean).join(" ");
  if (!what && line.tool) what = String(line.tool);
  if (!what && line.alert) what = String(line.alert);
  let result = "";
  if (line.reason) result = R.reasons[line.reason] || String(line.reason);
  else if (Number(line.status) >= 400) result = R.refused;
  else if (Number(line.status)) result = R.accepted;
  if (line.count) result = `${result} ${R.times(String(line.count))}`.trim();
  return h("li", { class: "rm-audit-line" },
    h("span", { class: "rm-audit-time", text: when }),
    h("span", { class: "rm-audit-who", text: `${who}${where}` }),
    h("span", { class: "rm-audit-what", text: kind && what ? R.kindWhat(kind, what) : kind || what }),
    h("span", { class: "rm-audit-result", text: result }));
}

/* ---------------------------------------------------------- the iPhone */
async function loadPhone(v) {
  let st;
  try {
    st = await api("/api/remote/state");
  } catch (err) {
    if (v === view) v.root.replaceChildren(unavailable(err));
    return;
  }
  if (v !== view) return;
  v.st = st;
  v.blocks = { phone: block("remote-phone"), shortcut: block("remote-shortcut") };
  v.root.replaceChildren(v.blocks.phone, v.blocks.shortcut);
  renderPhone(v);
  const slot = h("div", { class: "rm-shortcut-slot", "data-slot": "raccourci" });
  v.blocks.shortcut.replaceChildren(slot);
  bus.emit("remote:phone", { slot, state: st });
}

function renderPhone(v) {
  const st = v.st;
  draw(v, "phone", [st.device, st.enabled, st.paused_until, st.complet_until, v.paused], () => {
    const pause = button(R.pause, () => pauseNow(v), "ctl primary");
    pause.id = "rm-pause";
    pause.disabled = !!v.paused;
    const forget = button(R.forget, () => forgetNow(v));
    forget.id = "rm-forget";
    const out = [h("p", { class: "rm-phone-name" }, h("strong", { text: String(st.device?.name || R.thisPhone) })),
                 h("p", { class: "rm-phone-state", text: v.paused ? R.pausedUntil(untilText(v.paused))
                                                                  : R.phoneActive })];
    if (Number(st.complet_until) * 1000 > now()) {
      out.push(h("p", { class: "set-help", text: R.phoneComplet(dateLong(st.complet_until)) }));
    }
    out.push(h("p", { class: "set-help", text: R.pauseHelp }), h("div", { class: "set-actions" }, pause, forget));
    return out;
  });
}

/* « Couper l'accès depuis l'iPhone ? » with [1 h] [24 h] [Annuler]: 1, 24 or 0. */
function askPause() {
  if (!pauseDialog) {
    pauseDialog = h("dialog", { class: "set-confirm rm-pause-dialog", id: "remotePause", "aria-labelledby": "remotePauseTitle",
                                "aria-describedby": "remotePauseText" });
    pauseDialog.append(h("form", { method: "dialog" },
      h("h3", { id: "remotePauseTitle", text: R.pauseTitle }),
      h("p", { id: "remotePauseText", text: R.pauseText }),
      h("div", { class: "set-actions" },
        h("button", { type: "submit", value: "1", class: "ctl", text: R.pause1 }),
        h("button", { type: "submit", value: "24", class: "ctl", text: R.pause24 }),
        h("button", { type: "submit", value: "cancel", class: "ctl primary", text: R.cancel }))));
    document.body.append(pauseDialog);
  }
  const d = pauseDialog;
  d.returnValue = "";
  return new Promise((resolve) => {
    d.addEventListener("close", () => resolve(d.returnValue === "1" ? 1 : d.returnValue === "24" ? 24 : 0), { once: true });
    d.showModal();
    d.querySelector('button[value="cancel"]').focus();  // Entrée must not cut access by accident
  });
}

async function pauseNow(v) {
  const hours = await askPause();
  if (!hours) return;
  try {
    const r = await api("/api/remote/pause", { method: "POST", body: { hours } });
    v.paused = Number(r.paused_until) || (now() / 1000 + hours * 3600);
    note(v, "phone", R.pausedNow(untilText(v.paused)));
  } catch (err) {
    note(v, "phone", explainError(err), true);
  }
  redraw(v, "phone");
  renderPhone(v);
}

async function forgetNow(v) {
  const ok = await askConfirm({ title: R.forgetTitle, text: R.forgetAsk, ok: R.forget, danger: R.forgetWarn });
  if (!ok) return;
  // The stream ends and a 401 may reload the page first: not « Appareil retiré » (sse.js).
  bus.emit("remote:forgetting", true);
  try {
    await api("/api/remote/forget", { method: "POST" });
    note(v, "phone", R.forgotten);
    setTimeout(() => location.replace("/"), 800);
  } catch (err) {
    bus.emit("remote:forgetting", false);
    note(v, "phone", explainError(err), true);
  }
}

/* ---------------------------------------------------------- the section */
function build(ctx) {
  if (view) unwatch(view);
  const root = h("div", { class: "rm-root" }, h("p", { class: "set-loading", role: "status", text: R.loading }));
  view = { root, ctx, st: null, serve: null, blocks: null, notes: {}, editing: new Set(), tick: 0, poll: 0,
           siri: {} };
  (ctx.remote ? loadPhone : loadPc)(view);
  return [root];
}

export function init() {
  registerSection("distance", build);
  // A request, a device or the switch changed (PC-only event): Réglages follows.
  bus.on("server:remote", () => { if (view && !view.ctx.remote && view.st && onScreen(view)) refresh(view); });
  bus.on("ui:close", (name) => {
    if (name !== "settings" || !view) return;
    unwatch(view);
    view = null;
  });
}

