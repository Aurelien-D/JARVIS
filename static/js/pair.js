/* Owned by package A4 (JARVIS on iPhone): the pairing page (pair.html), what a
   device reaching JARVIS through Tailscale sees until the PC has allowed it.
   - The server says which page to show (meta jarvis-pair-state): "pair" when
     the PC opened pairing, else why the device cannot get in.
   - Reverse pairing: the phone asks, shows a 4-digit code, and the PC allows
     the request showing the same code. This page then polls the status every
     2 s (paused while hidden); once allowed, the server hands this browser its
     device cookie and the page goes to JARVIS.
   - On an iPhone outside the Home Screen web app, the page first explains how
     to add JARVIS there (the cookie belongs to where pairing happens), with a
     way to stay in Safari instead.
   - Under the remote CSP: no inline script, no CDN, no HTML from strings; it
     imports only strings-fr.js (core.js needs the page token, which this page
     never has). */
import { T } from "./strings-fr.js";

const P = T.pair;
const POLL_MS = 2000;
const GO_MS = 1200;  // « Associé ! » stays on screen this long before JARVIS opens
const STATES = ["pair", "closed", "off", "paused", "refused", "locked", "revoked"];

const body = document.getElementById("pairBody");
const live = document.getElementById("pairLive");
const meta = document.querySelector('meta[name="jarvis-pair-state"]')?.content || "";
const pageState = STATES.includes(meta) ? meta : "refused";
let pollTimer = 0;
let polling = false;
let request = null;  // { code } once the PC was asked

/* ---------------------------------------------------------- helpers */
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
  return el;
}

function button(label, onClick, className = "pair-btn") {
  const b = h("button", { type: "button", class: className, text: label });
  b.addEventListener("click", onClick);
  return b;
}

function announce(text) { live.textContent = text; }

let started = false;  // the first view is not announced: the page has just loaded

function show(...nodes) {
  body.replaceChildren(...nodes);
  const heading = body.querySelector("h2");
  if (heading && started) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
}

/* iPhone or iPad (iPadOS says "MacIntel" but has a touch screen), as core.js says it. */
function isIOS() {
  return /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function standalone() {
  return navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
}

async function call(path, options = {}) {
  const r = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : "");
    err.status = r.status;
    throw err;
  }
  return data;
}

/* ---------------------------------------------------------- the views */
function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = 0;
}

function retryButton() {
  return button(P.retry, () => location.reload(), "pair-btn primary");
}

function stateView(state) {
  show(h("h2", { class: "pair-title", text: P.titles[state] }),
       h("p", { class: "pair-text", text: P.states[state] }),
       h("div", { class: "pair-actions" }, retryButton()));
}

function installView() {
  const steps = h("ol", { class: "pair-steps" }, ...P.installSteps.map(text => h("li", { text })));
  show(h("h2", { class: "pair-title", text: P.installTitle }),
       h("p", { class: "pair-text", text: P.installIntro }),
       steps,
       h("div", { class: "pair-actions" }, button(P.useSafari, () => formView(), "pair-btn secondary")),
       h("p", { class: "pair-note", text: P.safariNote }));
}

function formView(note = "", error = false) {
  stopPolling();
  request = null;
  const input = h("input", { id: "pairName", class: "pair-input", type: "text", value: P.nameDefault,
                             maxlength: "24", autocomplete: "off", autocapitalize: "words", spellcheck: "false",
                             enterkeyhint: "send", "aria-describedby": "pairNameHelp pairStatus" });
  const status = h("p", { id: "pairStatus", class: error ? "pair-status err" : "pair-status", role: "status",
                          text: note });
  const ask = h("button", { type: "submit", class: "pair-btn primary", text: P.ask });
  const form = h("form", { class: "pair-form" },
    h("label", { class: "pair-label", for: "pairName", text: P.nameLabel }), input,
    h("p", { id: "pairNameHelp", class: "pair-note", text: P.nameHelp }),
    h("div", { class: "pair-actions" }, ask), status);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    ask.disabled = true;
    status.className = "pair-status";
    status.textContent = P.asking;
    try {
      const r = await call("/api/remote/pair-request", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: input.value.trim() || P.nameDefault }) });
      codeView(String(r.code ?? ""));
    } catch (err) {
      ask.disabled = false;
      status.className = "pair-status err";
      status.textContent = err.message || P.failed;
      announce(status.textContent);
    }
  });
  show(h("h2", { class: "pair-title", text: P.title }), h("p", { class: "pair-text", text: P.intro }), form);
}

function codeView(code) {
  request = { code };
  const status = h("p", { id: "pairStatus", class: "pair-status", role: "status", text: P.waiting });
  show(h("h2", { class: "pair-title", text: P.codeTitle }),
       h("p", { class: "pair-code", "aria-label": P.codeLabel(code.split("").join(" ")), text: code }),
       h("p", { class: "pair-text", text: P.check }),
       status);
  announce(P.codeLabel(code.split("").join(" ")));
  schedule(POLL_MS);
}

function endView(text) {
  stopPolling();
  request = null;
  show(h("h2", { class: "pair-title", text: P.endTitle }),
       h("p", { class: "pair-text", text }),
       h("div", { class: "pair-actions" }, button(P.restart, () => formView(), "pair-btn primary")));
  announce(text);
}

function approvedView() {
  stopPolling();
  request = null;
  show(h("h2", { class: "pair-title pair-ok", text: P.approved }), h("p", { class: "pair-text", text: P.opening }));
  announce(P.approved);
  setTimeout(() => location.replace("/"), GO_MS);
}

/* ---------------------------------------------------------- the poll */
function schedule(delay) {
  stopPolling();
  if (!request || document.hidden) return;  // resumed by visibilitychange
  pollTimer = setTimeout(poll, delay);
}

async function poll() {
  pollTimer = 0;
  if (!request || polling || document.hidden) return;
  polling = true;
  const status = document.getElementById("pairStatus");
  try {
    const r = await call("/api/remote/pair-status");
    if (!request) return;
    if (r.status === "approved" || r.status === "done") { approvedView(); return; }
    if (r.status === "denied") { endView(P.denied); return; }
    if (r.status === "expired") { endView(P.expired); return; }
    if (status) { status.className = "pair-status"; status.textContent = P.waiting; }
    schedule(POLL_MS);
  } catch (err) {
    if (!request) return;
    if (err.status && err.status < 500 && err.status !== 429) {
      // The request is gone (another browser, another address, a server restart).
      endView(err.message || P.failed);
      return;
    }
    if (status) { status.className = "pair-status err"; status.textContent = P.lost; }
    schedule(POLL_MS);
  } finally {
    polling = false;
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPolling(); else if (request) schedule(0);
});

/* ---------------------------------------------------------- start */
function start() {
  if (pageState !== "pair") stateView(pageState);
  else if (isIOS() && !standalone()) installView();
  else formView();
  started = true;
  window.__pair = { ready: true, state: pageState };
}

start();
