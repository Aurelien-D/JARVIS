/* The HUD around the orb: status line, controls, clock, display cards,
   toasts and screen-reader announcements. */
import { $, bus, esc, md, state, usesMarked } from "./core.js";
import { connect, sleep } from "./voice.js";
import { SR, toggleWake, wakeWanted } from "./wake.js";

const MAX_CARDS = 6;
let statusEl, cardsEl, wakeBtn, notifBtn;

/* ---------------------------------------------------------- status & controls */
export function renderStatus() {
  document.body.dataset.state = `${state.mode}-${state.phase || "idle"}`;
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

export function showError(msg) {
  statusEl.innerHTML = `<b style="color:var(--err)">erreur</b> · ${esc(String(msg))}`;
}
export { showError as setStatusError };

function updateNotifBtn() {
  notifBtn.hidden = !("Notification" in window) || Notification.permission !== "default";
}
function askNotifications() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().then(updateNotifBtn, updateNotifBtn);
  }
}

/* ---------------------------------------------------------- display cards */
const cardId = (id) => `card-${id}`;

/* A card with this id already on screen is updated in place (no new
   animation); sticky cards are never evicted, only dismissed. */
export function makeCard(title, kind, { id, sticky = false } = {}) {
  let el = id ? document.getElementById(cardId(id)) : null;
  if (el) {
    el.className = `card ${kind}`;
    el.querySelector("h3 span").textContent = title;
    el.querySelector(".body").replaceChildren();
    el.querySelector(".actions")?.remove();
  } else {
    el = document.createElement("div");
    el.className = `card ${kind}`;
    if (id) el.id = cardId(id);
    el.innerHTML = `<h3><span></span><span class="x" title="fermer">✕</span></h3><div class="body"></div>`;
    el.querySelector("h3 span").textContent = title;
    el.querySelector(".x").addEventListener("click", () => el.remove());
    cardsEl.prepend(el);
  }
  if (sticky) el.dataset.sticky = "1"; else delete el.dataset.sticky;
  // keep at most MAX_CARDS cards: the oldest non-sticky ones go first
  for (let i = cardsEl.children.length - 1; i >= 0 && cardsEl.children.length > MAX_CARDS; i--) {
    const old = cardsEl.children[i];
    if (old !== el && !old.dataset.sticky) old.remove();
  }
  return el;
}

/* addCard(title, content, kind, {id, actions:[{label, onClick, primary}], sticky, ttlMs})
   kind: info | result | warning | code | image | confirm. Returns the card element. */
export function addCard(title, content, kind = "info", { id, actions = [], sticky = false, ttlMs = 0 } = {}) {
  const el = makeCard(title, kind, { id, sticky });
  const body = el.querySelector(".body");
  body.innerHTML = md(content);
  body.classList.toggle("md", usesMarked());
  if (actions.length) {
    const bar = document.createElement("div");
    bar.className = "actions";
    for (const a of actions) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = a.primary ? "ctl primary" : "ctl";
      b.textContent = a.label;
      b.addEventListener("click", () => a.onClick?.(el));
      bar.append(b);
    }
    el.append(bar);
  }
  clearTimeout(el._ttl);
  if (ttlMs > 0) el._ttl = setTimeout(() => el.remove(), ttlMs);
  return el;
}

export function addImageCard(title, src) {
  const img = new Image();
  img.src = src; img.alt = title;
  makeCard(title, "info").querySelector(".body").append(img);
}

export function removeCard(idOrEl) {
  (typeof idOrEl === "string" ? document.getElementById(cardId(idOrEl)) : idOrEl)?.remove();
}

/* ---------------------------------------------------------- toasts & announcements */
export function toast(text, { actionLabel, onAction, ms = 6000 } = {}) {
  const el = document.createElement("div");
  el.className = "toast";
  const span = document.createElement("span");
  span.textContent = text;
  el.append(span);
  if (actionLabel) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "ctl"; b.textContent = actionLabel;
    b.addEventListener("click", () => { el.remove(); onAction?.(); });
    el.append(b);
  }
  $("toasts").append(el);
  setTimeout(() => el.remove(), ms);
  return el;
}

/* Said by screen readers only (polite); urgent messages use #srAlert. */
export function announce(text, { urgent = false } = {}) {
  $(urgent ? "srAlert" : "sr").textContent = text;
}

export function init() {
  statusEl = $("statusPill"); cardsEl = $("cards");
  wakeBtn = $("wakeBtn"); notifBtn = $("notifBtn");

  /* clock */
  setInterval(() => {
    $("clock").textContent = new Date().toLocaleTimeString("fr-FR");
  }, 1000);

  $("orbBtn").addEventListener("click", () => {
    askNotifications();
    if (state.mode === "live" || state.mode === "connecting") sleep();
    else connect();
  });
  wakeBtn.addEventListener("click", () => {
    toggleWake();
    renderStatus();
  });
  notifBtn.addEventListener("click", askNotifications);

  bus.on("mode", renderStatus);
  bus.on("error", (e) => showError(e.message));
  updateNotifBtn();
}
