/* A.R.E.S (WP15): monsieur's organiser seen from JARVIS.
   - #agenda in the side panel: today's and the coming days' agenda, read by
     the server from A.R.E.S's local MCP server (GET /api/ares, the 'ares'
     live event). Hidden while A.R.E.S can't be reached.
   - #aresChip in the top bar: 'A.R.E.S ●' reachable, '○' not (hidden when
     A.R.E.S is switched off in the settings).
   - Every write JARVIS makes in A.R.E.S shows as a card, whichever way it
     was made: monsieur sees everything done in his organiser.
   Agenda lines are monsieur's own titles, shown as text only. */
import { $, api, bus, settings, state } from "./core.js";
import { T, fmtTime } from "./strings-fr.js";
import { makeCard } from "./hud.js";

const A = T.ares;  // strings-fr.js

const POLL_MS = 5 * 60e3;  // the server reads A.R.E.S again when its snapshot is older than that
const FOLD_KEY = "panels.folded";  // shared with panels.js: folded side sections

let section, chip, details, heading, listEl, metaEl;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function folded() {
  const v = settings.get(FOLD_KEY, []);
  return Array.isArray(v) ? v : [];
}

function build() {
  details = el("details", "side-sec");
  const summary = el("summary");
  heading = el("h2", "", A.title);
  summary.append(heading);
  listEl = el("ul", "rows agenda-rows");
  listEl.id = "agendaList";
  metaEl = el("p", "meta agenda-meta");
  details.append(summary, listEl, metaEl);
  details.open = !folded().includes("agenda");
  details.addEventListener("toggle", () => {
    const rest = folded().filter(x => x !== "agenda");
    settings.set(FOLD_KEY, details.open ? rest : [...rest, "agenda"]);
  });
  section.replaceChildren(details);
}

/* '• Appeler le labo — Aujourd'hui · 14:00 (en retard)' → title, when, late, reminder */
export function parseLine(line) {
  let text = String(line || "").trim();
  const reminder = /^\(rappel\)\s*/i.test(text);
  text = text.replace(/^\(rappel\)\s*/i, "");
  const cut = text.lastIndexOf(" — ");
  const title = cut > 0 ? text.slice(0, cut) : text;
  const when = cut > 0 ? text.slice(cut + 3) : "";
  return { title, when, reminder, late: /en retard/i.test(when) };
}

function renderChip(data) {
  if (!chip) return;
  if (data.mode === "off") { chip.replaceChildren(); chip.hidden = true; return; }
  const up = !!data.available;
  chip.hidden = false;
  chip.className = up ? "chip ares-chip up" : "chip ares-chip down";
  chip.title = up ? A.up : A.down;
  // The dot alone would say it by colour and shape only: the words are there for screen readers.
  const shown = el("span", "", `${A.chip} ${up ? "●" : "○"}`);
  shown.setAttribute("aria-hidden", "true");
  chip.replaceChildren(shown, el("span", "visually-hidden", `${A.chip} ${up ? A.upShort : A.downShort}`));
}

export function render(data = {}) {
  // What the other modules may read (the help card hides A.R.E.S when it is off).
  state.ares = { available: !!data.available, mode: data.mode || "auto" };
  renderChip(data);
  if (!section) return;
  const up = !!data.available;
  section.hidden = !up;
  if (!up) return;
  const lines = Array.isArray(data.lines) ? data.lines : [];
  heading.textContent = lines.length ? A.count(lines.length) : A.title;
  if (!lines.length) {
    listEl.replaceChildren(el("li", "none", T.empty.agenda));
  } else {
    listEl.replaceChildren(...lines.map((line) => {
      const it = parseLine(line);
      const li = el("li", `item agenda-item${it.late ? " late" : ""}`);
      const txt = el("span", "txt");
      if (it.reminder) txt.append(el("span", "tag", A.reminder), " ");
      txt.append(it.title);
      li.append(txt);
      if (it.when) li.append(el("span", "when", it.when));
      return li;
    }));
  }
  metaEl.textContent = data.at ? A.snapshot(fmtTime(new Date(data.at * 1000))) : "";
}

/* A write JARVIS made in A.R.E.S (ares_ajouter / ares_modifier). */
function showAction(text) {
  const card = makeCard(A.card, "result");
  const p = el("p", "", String(text || ""));
  card.querySelector(".body").replaceChildren(p);
}

export async function load() {
  try {
    render(await api("/api/ares"));
  } catch (err) {
    console.warn(err);
    render({ available: false, mode: "auto" });
  }
}

export function init() {
  section = $("agenda");
  chip = $("aresChip");
  if (section) build();
  bus.on("server:ares", (ev) => {
    if (!ev) return;
    if ("available" in ev) render(ev);
    if (ev.action) showAction(ev.action);
  });
  bus.on("server:config", () => load());  // A.R.E.S switched on or off in Réglages
  bus.on("sse:open", () => load());       // back after a disconnection: read it again
  setInterval(load, POLL_MS);
  load();
}
