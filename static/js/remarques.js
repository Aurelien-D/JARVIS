/* « JARVIS a remarqué » (WP17): at most three suggestions at the top of the
   side panel, found by the server with simple local rules (GET
   /api/remarques, the 'remarques' live event). Hidden when there are none.
   - Each row: the remark, its action, a « Pourquoi ? » and a ✕.
   - « Replanifier ? » only prefills the composer (no session is opened for
     it); « Relancer ? » uses the panel's own retry route, where a
     full-access task still waits for its confirmation card.
   - ✕ hides a remark, longer each time (the server doubles the delay).
   A remark may quote a task's title: shown as text only, never as markup.
   The voice hears at most one remark, at the opening of a session: the
   server puts it in the voice instructions, nothing to do here. */
import { $, api, bus, settings } from "./core.js";
import { T } from "./strings-fr.js";
import { toast } from "./hud.js";

const R = T.remarques, P = T.panels;
const POLL_MS = 5 * 60e3;
const FOLD_KEY = "panels.folded";  // shared with panels.js and ares.js: folded side sections
const FINAL = new Set(["done", "error", "cancelled", "interrompue", "interrupted"]);

let section, details, heading, listEl;
let items = [];
let soon = null;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function button(text, cls, label = "") {
  const b = el("button", cls, text);
  b.type = "button";
  if (label) b.setAttribute("aria-label", label);
  return b;
}

function folded() {
  const v = settings.get(FOLD_KEY, []);
  return Array.isArray(v) ? v : [];
}

function build() {
  details = el("details", "side-sec");
  const summary = el("summary");
  heading = el("h2", "", R.title);
  summary.append(heading);
  listEl = el("ul", "rows");
  listEl.id = "remarquesList";
  details.append(summary, listEl);
  details.open = !folded().includes("remarques");
  details.addEventListener("toggle", () => {
    const rest = folded().filter(x => x !== "remarques");
    settings.set(FOLD_KEY, details.open ? rest : [...rest, "remarques"]);
  });
  section.replaceChildren(details);
}

function row(r) {
  const li = el("li", `item remarque ${["review", "question", "notify"].includes(r.kind) ? r.kind : ""}`);
  li.dataset.key = String(r.key);
  li.append(el("span", "txt", String(r.text)));
  const acts = el("div", "acts");
  const a = r.action && typeof r.action === "object" ? r.action : null;
  if (a && a.label) {
    const b = button(String(a.label), "ctl");
    b.addEventListener("click", () => act(a, b));
    acts.append(b);
  }
  if (r.why) {
    const why = el("details", "why");
    why.append(el("summary", "", R.why), el("p", "", String(r.why)));
    acts.append(why);
  }
  li.append(acts);
  const x = button("✕", "x", R.dismiss(String(r.text)));
  x.addEventListener("click", () => dismiss(r, li));
  li.append(x);
  return li;
}

export function render(list) {
  items = (Array.isArray(list) ? list : [])
    .filter(r => r && typeof r === "object" && r.key && r.text).slice(0, 3);
  if (!section) return;
  section.hidden = !items.length;
  heading.textContent = items.length ? R.count(items.length) : R.title;
  listEl.replaceChildren(...items.map(row));
}

async function act(a, b) {
  if (a.type === "compose") {
    bus.emit("ui:compose", { text: String(a.text || "") });
    return;
  }
  if (a.type !== "retry" || !a.task) return;
  b.disabled = true;
  try {
    const out = await api(`/api/task/${encodeURIComponent(a.task)}/retry`, { method: "POST" });
    toast(out && out.status === "needs_confirmation" ? P.retryConfirm : P.retried(String(a.title || "")));
    load();
  } catch (err) {
    toast(P.retryFailed(err.message));
  } finally {
    b.disabled = false;
  }
}

async function dismiss(r, li) {
  const nextKey = li.nextElementSibling?.dataset.key || li.previousElementSibling?.dataset.key;
  try {
    const out = await api(`/api/remarques/${encodeURIComponent(r.key)}/dismiss`, { method: "POST" });
    render(items.filter(it => it.key !== r.key));
    toast(R.dismissed(out && out.days));
  } catch (err) {
    toast(R.dismissFailed(err.message));
    return;
  }
  // The focus goes to the next remark's ✕, never lost on <body>.
  const next = nextKey && listEl.querySelector(`[data-key="${CSS.escape(nextKey)}"] .x`);
  (next || (items.length ? details.querySelector("summary") : $("orbBtn")))?.focus({ preventScroll: true });
}

export async function load() {
  try {
    render(await api("/api/remarques"));
  } catch (err) {
    console.warn(err);  // the next event or poll asks again
  }
}

/* Several events often come together (a task ends, the agenda follows). */
function loadSoon() {
  clearTimeout(soon);
  soon = setTimeout(load, 1500);
}

export function init() {
  section = $("remarques");
  if (!section) return;
  build();
  section.hidden = true;
  bus.on("server:remarques", (ev) => render(ev && ev.items));
  bus.on("server:task", (tk) => { if (tk && FINAL.has(tk.status)) loadSoon(); });
  bus.on("server:reminder", loadSoon);
  bus.on("server:ares", loadSoon);
  bus.on("sse:open", load);  // back after a disconnection: read them again
  setInterval(load, POLL_MS);
  load();
}
