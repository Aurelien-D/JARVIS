/* Shared foundations every module imports: DOM lookup, the page token, the
   API helper, the event bus, shared state, per-browser settings, and safe
   text/markdown rendering. */

export const $ = (id) => document.getElementById(id);
export const TOKEN = document.querySelector('meta[name="jarvis-token"]').content;

/* What the server stamped into the page: a remote page (the paired iPhone
   through Tailscale Serve) and the origin string of its caller ("pc",
   "app:d_…"). A missing tag means this PC's own page. */
function meta(name) {
  return document.querySelector(`meta[name="${name}"]`)?.content || "";
}

export const state = {
  mode: "off",            // off | standby (wake word) | connecting | live
  phase: null,            // live only: listening | user | thinking | tool | speaking | confirm
  connecting: false,
  wantLive: false,        // a session is wanted: drives auto-reconnect
  retries: 0,
  lastActivity: Date.now(),
  responseActive: false,
  pendingResponse: false,
  endRequested: false,
  muted: false,
  wake: null,             // { final, command } while a wake word is handled
  pendingText: "",        // typed text waiting for the session to open
  history: [],            // recent exchanges, replayed into a new session
  queue: [],              // app notices waiting for the next session (voice.sendNotice)
  confirming: [],         // pending confirmation ids on screen (confirm.js)
  announced: new Set(),   // task ids already read out
  tasks: new Map(),       // task id -> latest task snapshot from the server
  config: { wake_word: true, speech_lang: "fr-FR", idle_minutes: 3 },  // /api/config
  ready: false,           // every module started (main.js)
  synced: false,          // live events connected and the panels loaded (sse.js)
  remote: meta("jarvis-remote") === "1",  // the paired iPhone, never this PC's page
  origin: meta("jarvis-origin") || "pc",  // "pc" | "app:d_…": items of this origin are its own
};

/* iPhone or iPad (iPadOS says "MacIntel" but has a touch screen). */
export function isIOS() {
  return /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

/* A finger, not a mouse: bigger targets, no hover-only controls. */
export function touchUI() {
  return matchMedia("(pointer: coarse)").matches;
}

/* This page's origin string, as the server stores it on tasks and requests. */
export function myOrigin() { return state.origin || "pc"; }

export function touch() { state.lastActivity = Date.now(); }

export async function api(path, { method = "GET", body, signal } = {}) {
  const headers = { "X-Jarvis-Token": TOKEN };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { method, headers, signal, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.detail || `HTTP ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return data;
}

/* Module-to-module events, so a module reacts to others without importing
   them. A failing listener is reported but never stops the next ones. */
const listeners = new Map();
export const bus = {
  on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
    return () => listeners.get(type).delete(fn);
  },
  emit(type, detail) {
    for (const fn of [...(listeners.get(type) || [])]) {
      try { fn(detail); } catch (err) { reportError(err); }
    }
  },
};

/* The mode drives the status line, the orb and the wake word. */
export function setMode(mode, reason = "") {
  state.mode = mode;
  bus.emit("mode", { mode, reason });
}

/* Per-browser preferences. Storage can throw (private mode, blocked site data):
   the page then keeps them for this visit only. */
const SETTINGS_KEY = "jarvis.settings";
const saved = (() => {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {}; } catch { return {}; }
})();
export const settings = {
  get(key, fallback) { return saved[key] ?? fallback; },
  set(key, value) {
    saved[key] = value;
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(saved)); } catch { /* private mode */ }
  },
};

/* ---------------------------------------------------------- text rendering */
export function esc(s) {
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

export function usesMarked() { return !!(window.marked && window.DOMPurify); }

/* Markdown to safe HTML: marked + DOMPurify, or the tiny fallback offline. */
let linkHook = false;
export function md(text) {
  if (usesMarked()) {
    if (!linkHook) {
      // A link opens beside JARVIS, never in its window: the HUD and the
      // voice session would be gone with it. Only web and mail links stay
      // links (not tel:, file:, relative or JARVIS's own URLs); the others
      // keep their text. SVG links (tagName "a") follow the same rules.
      DOMPurify.addHook("afterSanitizeAttributes", (node) => {
        const tag = String(node.nodeName || "").toLowerCase();
        if (tag !== "a" && tag !== "area") return;
        for (const name of ["href", "xlink:href"]) {
          if (!node.hasAttribute(name)) continue;
          if (/^(https?|mailto):/i.test(node.getAttribute(name).trim())) {
            node.setAttribute("target", "_blank");
            node.setAttribute("rel", "noopener noreferrer");
          } else {
            node.removeAttribute(name);
          }
        }
      });
      linkHook = true;
    }
    // No forms, inputs or inline styles: injected text could draw a fake
    // password box inside a card even without running any script.
    return DOMPurify.sanitize(marked.parse(text), {
      FORBID_TAGS: ["form", "input", "button", "textarea", "select", "option", "style", "iframe"],
      FORBID_ATTR: ["style"],
    });
  }
  return mdLite(text); // offline fallback
}

export async function init() {
  try {
    Object.assign(state.config, await api("/api/config"));
  } catch (err) {
    console.warn(err);
  }
}
