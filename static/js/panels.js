/* Side panel (design spec §3): Claude Code task cards, reminders and
   routines, memory. Fed by the server's live events (sse.js puts them on the
   bus); every text from a task, a reminder or a fact is shown as text, or as
   markdown through md() (sanitised).
   - Task cards: status, profile, follow-up title, model in a tooltip, live
     progress, output as markdown, and the actions Lire / Copier / Continuer /
     Réessayer / Annuler la tâche. Order: waiting for a "oui" > running >
     failed > done; finished tasks older than an hour go into 'Historique'.
   - A full-access 'Réessayer' never starts anything: the server parks it in
     the confirmation store and its card asks first (POST /api/tasks refuses
     complet). 'Continuer' goes through the composer, so through JARVIS.
   - Reminders by day, memory facts; deleting hides the row at once and the
     DELETE leaves only when the 'Annuler' toast goes (6 s). Editing appears
     only when the server has the PATCH routes (WP14, WP17). */
import { $, TOKEN, api, bus, md, settings } from "./core.js";
import { T, fmtElapsed, fmtRelative, fmtTime } from "./strings-fr.js";
import { setSideOpen, toast } from "./hud.js";
import { openTask } from "./taskview.js";

const S = T.task, P = T.panels;
const REPEAT_LABEL = { daily: "chaque jour", weekdays: "en semaine", weekly: "chaque semaine" };
const ACTIVE = new Set(["running", "en_file"]);
const FAILED = new Set(["error", "interrompue", "interrupted"]);
const FINISHED = new Set(["done", "cancelled", "error", "interrompue", "interrupted"]);
const LOOK = { done: "done", cancelled: "cancelled", error: "error", interrompue: "error",
               interrupted: "error", en_file: "queued", attente: "waiting" };
const HISTORY_AFTER_S = 3600;   // finished tasks older than this go into 'Historique'
const MAX_TASKS = 40;
const OUTPUT_PREVIEW = 1500;     // characters rendered on the card; 'Lire' shows it all
export const UNDO_MS = 6000;

const dayFormat = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short" });
const longDate = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric" });

let tasksEl, schedulesEl, memoryEl;
const heads = {};                // section id -> its <h2>
const tasksById = new Map();     // task id -> latest snapshot
const waiting = new Map();       // pending id -> a full-access task waiting for monsieur's "oui"
let historyEl = null;
let schedules = [], facts = [];
const hidden = new Set();        // "s:id" / "m:id": deleted, waiting out its undo (or the server)
const editable = { schedules: false, memory: false };
let editing = null;              // {key, form, cancel} while a row is being edited

/* ---------------------------------------------------------- task cards */
const taskId = (tk) => (tk.waiting ? `task-attente-${tk.pendingId}` : `task-${tk.id}`);
const titleOf = (tk) => String(tk.title || "") || "Tâche";

/* Waiting > running > failed > done, newest first in each group. */
function rank(tk) {
  if (tk.waiting) return 0;
  if (ACTIVE.has(tk.status)) return 1;
  if (FAILED.has(tk.status)) return 2;
  return 3;
}

function ended(tk) { return Number(tk.ended) || Number(tk.started) || 0; }

function isOld(tk, now) {
  return !tk.waiting && FINISHED.has(tk.status) && ended(tk) && ended(tk) < now - HISTORY_AFTER_S;
}

/* A task the server pushed (or a test): kept, then the list redrawn. */
export function renderTask(tk) {
  if (!tk || typeof tk !== "object" || !tk.id) return;
  tasksById.set(String(tk.id), { ...tk });
  if (tasksById.size > MAX_TASKS) {
    const finished = [...tasksById.values()].filter(t => !ACTIVE.has(t.status))
      .sort((a, b) => ended(a) - ended(b));
    for (const t of finished.slice(0, tasksById.size - MAX_TASKS)) {
      tasksById.delete(String(t.id));
      document.getElementById(`task-${t.id}`)?.remove();
    }
  }
  renderTasks();
}

/* A full-access task asked by voice (or 'Réessayer'): shown with Lancer / Annuler
   until it is decided or expires (the confirmation card says the same). */
function waitingItem(p) {
  const summary = String(p.summary || "");
  const quoted = [...summary.matchAll(/«\s*([^»]+?)\s*»/g)].pop();
  return { waiting: true, pendingId: String(p.id), title: quoted ? quoted[1] : summary,
           status: "attente", profile: "complet", output: String(p.detail || ""),
           started: Number(p.created) || Date.now() / 1000 };
}

function renderTasks() {
  if (!tasksEl) return;
  const now = Date.now() / 1000;
  for (const [id, p] of waiting) if (p._deadline && Date.now() > p._deadline) waiting.delete(id);
  const items = [...[...waiting.values()].map(waitingItem), ...tasksById.values()]
    .sort((a, b) => rank(a) - rank(b) || (Number(b.started) || 0) - (Number(a.started) || 0));
  const recent = items.filter(tk => !isOld(tk, now));
  const old = items.filter(tk => isOld(tk, now));

  // Keep the focus where it was: cards are moved, not rebuilt.
  const focused = tasksEl.contains(document.activeElement) ? document.activeElement : null;
  const want = recent.map(tk => taskEl(tk));
  if (old.length) {
    if (!historyEl) historyEl = makeHistory();
    historyEl.querySelector("summary").textContent = S.history(old.length);
    const list = historyEl.querySelector(".history-list");
    placeChildren(list, old.map(tk => taskEl(tk)));
    want.push(historyEl);
  }
  if (!items.length) want.push(emptyLine(T.empty.tasks));
  placeChildren(tasksEl, want);
  // Cards that left (a decided request) take nothing with them.
  if (focused && !focused.isConnected) tasksEl.querySelector(".task .actions button")?.focus({ preventScroll: true });
  else if (focused && document.activeElement !== focused) focused.focus({ preventScroll: true });

  const running = items.filter(tk => tk.status === "running").length;
  setHead("tasks", running ? S.sectionTitle(running) : items.length ? P.count(P.tasks, items.length) : P.tasks);
}

/* Children in this order, moving only what is out of place. */
function placeChildren(parent, wanted) {
  const keep = new Set(wanted);
  for (const child of [...parent.children]) if (!keep.has(child)) child.remove();
  wanted.forEach((el, i) => {
    if (parent.children[i] !== el) parent.insertBefore(el, parent.children[i] || null);
  });
}

function makeHistory() {
  const d = document.createElement("details");
  d.className = "task-history";
  const s = document.createElement("summary");
  const list = document.createElement("div");
  list.className = "history-list";
  d.append(s, list);
  return d;
}

function taskEl(tk) {
  let el = document.getElementById(taskId(tk));
  if (!el) {
    el = document.createElement("article");
    el.id = taskId(tk);
    el.innerHTML = `<header class="task-head"><h3 class="t"></h3>`
      + `<p class="s"><span class="dot" aria-hidden="true"></span><span class="lbl"></span>`
      + `<span class="chrono"></span></p></header>`
      + `<p class="meta"></p><p class="prog"></p><div class="approval" hidden></div>`
      + `<div class="out"></div><div class="actions"></div>`;
    el.querySelector(".t").id = `${el.id}-t`;
    el.setAttribute("aria-labelledby", `${el.id}-t`);
    el.querySelector(".out").addEventListener("scroll", (e) => fadeOut(e.currentTarget), { passive: true });
  }
  fillTask(el, tk);
  return el;
}

function fillTask(el, tk) {
  const title = titleOf(tk);
  el.className = `task ${LOOK[tk.status] || (ACTIVE.has(tk.status) ? "running" : "error")}`;
  el.dataset.status = tk.status || "";
  setText(el.querySelector(".t"), title);
  setText(el.querySelector(".lbl"), S.status[tk.status] || S.status.error);
  const chrono = el.querySelector(".chrono");
  if (tk.status === "running") {
    el.dataset.started = String(Number(tk.started) || Date.now() / 1000);
    setText(chrono, fmtElapsed(Date.now() / 1000 - Number(el.dataset.started)));
  } else {
    delete el.dataset.started;
    setText(chrono, tk.ended && tk.started ? fmtElapsed(tk.ended - tk.started) : "");
  }

  // 'Web uniquement · il y a 3 min · suite de « Comparatif »'; the model in a tooltip.
  const meta = el.querySelector(".meta");
  const followed = tk.resumed_from ? tasksById.get(String(tk.resumed_from)) : null;
  setText(meta, [
    S.profile[tk.profile] || tk.profile || "",
    tk.started ? fmtRelative(Number(tk.started) * 1000) : "",
    tk.resumed_from ? S.followUp(followed ? titleOf(followed) : String(tk.resumed_from)) : "",
    P.origins[tk.origin] || "",
  ].filter(Boolean).join(" · "));
  if (tk.waiting) meta.removeAttribute("title");
  else meta.title = P.model(tk.model || S.defaultModel);

  const prog = el.querySelector(".prog");
  setText(prog, ACTIVE.has(tk.status) ? String(tk.progress || (tk.status === "en_file" ? P.queued : "")) : "");

  fillApproval(el, tk);
  fillOutput(el, tk);
  fillActions(el, tk);
}

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

/* Claude was refused some tools: 'Autoriser' resumes with them (confirm.py
   holds the request; this is the same [Lancer] as its card). */
function fillApproval(el, tk) {
  const box = el.querySelector(".approval");
  const pid = !tk.waiting && FINISHED.has(tk.status) ? tk.approval : null;
  if (!pid) {
    if (!box.hidden) { box.hidden = true; box.replaceChildren(); delete box.dataset.pid; }
    return;
  }
  if (box.dataset.pid === String(pid)) return;
  box.dataset.pid = String(pid);
  const tools = [...new Set((Array.isArray(tk.permission_denials) ? tk.permission_denials : [])
    .map(d => String(d?.tool || "")).filter(Boolean))].join(", ");
  const ask = document.createElement("p");
  ask.textContent = P.approvalAsk(tools || "d'autres outils");
  const bar = document.createElement("div");
  bar.className = "ap-actions";
  const yes = button(P.approve, "ctl primary approve", P.approveLabel(titleOf(tk)));
  const no = button(P.refuse, "ctl refuse", P.refuseLabel(titleOf(tk)));
  yes.addEventListener("click", () => decide(String(pid), "oui", [yes, no]));
  no.addEventListener("click", () => decide(String(pid), "non", [yes, no]));
  bar.append(yes, no);
  box.replaceChildren(ask, bar);
  box.hidden = false;
}

async function decide(pendingId, decision, buttons) {
  buttons.forEach(b => { b.disabled = true; });
  try {
    const out = await api(`/api/pending/${encodeURIComponent(pendingId)}/decide`,
                          { method: "POST", body: { decision } });
    if (out && out.ok === false && out.error) toast(P.decideFailed(out.error));
  } catch (err) {
    toast(P.decideFailed(err.message));
  } finally {
    buttons.forEach(b => { b.disabled = false; });
  }
  if (waiting.delete(pendingId)) renderTasks();  // the server says the same right after
}

function fillOutput(el, tk) {
  const out = el.querySelector(".out");
  const text = ACTIVE.has(tk.status) ? "" : String(tk.output || "").slice(0, OUTPUT_PREVIEW);
  const key = `${tk.waiting ? "t" : "m"}:${text}`;
  if (out.dataset.key === key) return;
  out.dataset.key = key;
  if (!text) {
    out.replaceChildren();
    out.className = "out";
    out.removeAttribute("tabindex");
    out.removeAttribute("role");
    out.removeAttribute("aria-label");
    return;
  }
  if (tk.waiting) {
    out.className = "out plain";  // what will run, exactly as asked: plain text
    out.textContent = text;
  } else {
    out.className = "out md";
    out.innerHTML = md(text);
  }
  // A scroller the keyboard reaches, named after its task.
  out.tabIndex = 0;
  out.setAttribute("role", "region");
  out.setAttribute("aria-label", S.outputLabel(titleOf(tk)));
  requestAnimationFrame(() => fadeOut(out));
}

/* The fade at the bottom says there is more; gone once scrolled to the end. */
function fadeOut(out) {
  const more = out.scrollHeight - out.scrollTop - out.clientHeight > 2;
  out.classList.toggle("clipped", more);
}

function button(text, className, label = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = text;
  if (label) b.setAttribute("aria-label", label);
  return b;
}

/* Which actions a card offers, by status (design spec §3). */
function actionsFor(tk) {
  if (tk.waiting) return ["launch", "dismiss"];
  const list = ["read"];
  if (ACTIVE.has(tk.status)) return [...list, "cancel"];
  if (String(tk.output || "").trim()) list.push("copy");
  if (tk.status === "done") list.push("continue");
  if ((FAILED.has(tk.status) || tk.status === "cancelled") && String(tk.prompt || "").trim()
      && tk.origin !== "approbation") list.push("retry");
  return list;
}

function fillActions(el, tk) {
  const bar = el.querySelector(":scope > .actions");
  const names = actionsFor(tk);
  el._tk = tk;  // the handlers read the latest snapshot
  if (bar.dataset.names === names.join(",")) return;
  const hadFocus = bar.contains(document.activeElement);
  bar.dataset.names = names.join(",");
  const title = titleOf(tk);
  const A = S.actions;
  const make = {
    read: () => button(A.read, "ctl read", P.readLabel(title)),
    copy: () => button(A.copy, "ctl copy", P.copyLabel(title)),
    continue: () => button(A.continue, "ctl continue", P.continueLabel(title)),
    retry: () => button(A.retry, "ctl retry", P.retryLabel(title)),
    cancel: () => button(A.cancel, "ctl cancel", S.cancelLabel(title)),
    launch: () => button(P.launch, "ctl primary launch", P.launchLabel(title)),
    dismiss: () => button(P.dismiss, "ctl dismiss", P.dismissLabel(title)),
  };
  // Buttons that stay are kept (not rebuilt): the focus, and the viewer's way
  // back to its 'Lire', stay on them.
  const have = new Map([...bar.querySelectorAll(":scope > button")].map(b => [b.dataset.action, b]));
  placeChildren(bar, names.map((name) => {
    const b = have.get(name) || make[name]();
    if (have.has(name)) b.setAttribute("aria-label", make[name]().getAttribute("aria-label"));
    else {
      b.dataset.action = name;
      b.addEventListener("click", () => act(name, el._tk, b));
    }
    return b;
  }));
  if (hadFocus && !bar.contains(document.activeElement)) bar.querySelector("button")?.focus({ preventScroll: true });
}

function act(name, tk, b) {
  if (name === "read") return openTask(tk, b);
  if (name === "copy") return copy(String(tk.output || ""));
  if (name === "continue") return continueTask(tk);
  if (name === "retry") return retry(tk, b);
  if (name === "cancel") {
    return api(`/api/task/${encodeURIComponent(tk.id)}/cancel`, { method: "POST" })
      .then((out) => { if (out && out.ok === false && out.error) toast(out.error); })
      .catch((err) => toast(err.message));
  }
  if (name === "launch" || name === "dismiss") {
    return decide(tk.pendingId, name === "launch" ? "oui" : "non",
                  [...b.parentElement.querySelectorAll("button")]);
  }
  return undefined;
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(T.controls.copied, { ms: 2500 });
  } catch {
    toast(T.hud.copyFailed);
  }
}

/* 'Continuer': monsieur says what comes next, through the composer and so
   through JARVIS (a full-access follow-up is confirmed there, like any other). */
function continueTask(tk) {
  if (document.body.classList.contains("side-open")) setSideOpen(false, { returnFocus: false });
  bus.emit("ui:compose", { text: P.continueText(titleOf(tk)) });
}

/* 'Réessayer': the same prompt and profile. Full access is never posted to
   /api/tasks: the server parks it and its confirmation card asks first. */
async function retry(tk, b) {
  b.disabled = true;
  try {
    if (tk.profile === "complet") {
      const out = await api(`/api/task/${encodeURIComponent(tk.id)}/retry`, { method: "POST" });
      if (out && out.status === "needs_confirmation" && out.pending_id) {
        // The card at once; the server's own event fills in the details.
        bus.emit("server:pending", { type: "pending", pending: {
          id: out.pending_id, name: "delegate_to_claude", kind: "tool", summary: out.summary || "",
          state: "pending", expires_in: out.expires_in } });
        toast(P.retryConfirm);
      }
    } else {
      const body = { prompt: String(tk.prompt || ""), title: titleOf(tk), profile: tk.profile || "lecture",
                     complexity: tk.complexity || "normale" };
      if (tk.resumed_from) body.continue_task = String(tk.resumed_from);
      await api("/api/tasks", { method: "POST", body });
      toast(P.retried(titleOf(tk)));
    }
  } catch (err) {
    toast(P.retryFailed(err.message));
  } finally {
    b.disabled = false;
  }
}

function onPending(p) {
  if (!p || !p.id || p.name !== "delegate_to_claude") return;
  const id = String(p.id);
  if (p.state && p.state !== "pending") {
    if (waiting.delete(id)) renderTasks();
    return;
  }
  const known = waiting.get(id);
  const left = Number.isFinite(Number(p.expires_in)) ? Number(p.expires_in) : 90;
  waiting.set(id, { ...known, ...p, detail: p.detail || known?.detail || "",
                    summary: p.summary || known?.summary || "",
                    _deadline: known?._deadline || Date.now() + left * 1000 });
  renderTasks();
}

/* ---------------------------------------------------------- reminders */
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

/* Aujourd'hui / Demain / Cette semaine (until Sunday) / Plus tard. */
export function dayGroup(due, now = new Date()) {
  const days = Math.round((startOfDay(due) - startOfDay(now)) / 86400e3);
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  const toSunday = (7 - now.getDay()) % 7;  // Sunday ends a French week
  return days <= toSunday ? "week" : "later";
}

function whenText(d, group) {
  if (group === "today") return fmtTime(d);
  if (group === "tomorrow") return T.time.tomorrow(fmtTime(d));
  return T.time.onDate(dayFormat.format(d), fmtTime(d));
}

function emptyLine(text) {
  const div = document.createElement("div");
  div.className = "none";
  div.textContent = text;
  return div;
}

export function renderSchedules(items) {
  schedules = Array.isArray(items) ? items.filter(it => it && typeof it === "object") : [];
  prune("s", schedules);
  drawSchedules();
}

function drawSchedules() {
  if (!schedulesEl || isEditing("s")) return;
  const items = schedules.filter(it => !hidden.has(`s:${it.id}`))
    .sort((a, b) => (Number(a.due) || 0) - (Number(b.due) || 0));
  setHead("schedules", items.length ? P.count(P.reminders, items.length) : P.reminders);
  if (!items.length) {
    schedulesEl.replaceChildren(emptyLine(T.empty.reminders));
    return;
  }
  const now = new Date();
  const out = [];
  let group = null, list = null;
  for (const it of items) {
    const due = new Date((Number(it.due) || 0) * 1000);
    const g = dayGroup(due, now);
    if (g !== group) {
      group = g;
      const h = document.createElement("h3");
      h.className = "grp";
      h.textContent = P.groups[g];
      list = document.createElement("ul");
      list.className = "rows";
      out.push(h, list);
    }
    const text = String(it.title || it.text || "");
    const repeat = REPEAT_LABEL[it.repeat] ? ` · ${REPEAT_LABEL[it.repeat]}` : "";
    list.append(itemRow({
      key: `s:${it.id}`, text, when: whenText(due, g) + repeat,
      tag: it.kind === "task" ? S.routine : "",
      deleteLabel: S.deleteReminder(text), editLabel: P.editReminder(text),
      url: `/api/schedules/${encodeURIComponent(it.id)}`, undoText: P.reminderDeleted,
      edit: editable.schedules ? { field: it.title ? "title" : "text", kind: "schedules" } : null,
    }));
  }
  schedulesEl.replaceChildren(...out);
}

/* ---------------------------------------------------------- memory */
export function renderMemory(list) {
  facts = Array.isArray(list) ? list.filter(f => f && typeof f === "object") : [];
  prune("m", facts);
  drawMemory();
}

function drawMemory() {
  if (!memoryEl || isEditing("m")) return;
  const items = facts.filter(f => !hidden.has(`m:${f.id}`)).slice().reverse();
  setHead("memory", items.length ? P.count(P.memory, items.length) : P.memory);
  if (!items.length) {
    memoryEl.replaceChildren(emptyLine(T.empty.memory));
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "rows";
  for (const f of items) {
    const text = String(f.text || "");
    const at = Number(f.created);
    ul.append(itemRow({
      key: `m:${f.id}`, text, fact: true,
      hover: at ? P.factDate(`${longDate.format(new Date(at * 1000))} à ${fmtTime(new Date(at * 1000))}`) : "",
      deleteLabel: S.forget(text), editLabel: P.editFact(text),
      url: `/api/memory/${encodeURIComponent(f.id)}`, undoText: P.factForgotten,
      edit: editable.memory ? { field: "text", kind: "memory" } : null,
    }));
  }
  memoryEl.replaceChildren(ul);
}

/* ---------------------------------------------------------- rows: delete with undo, edit */
/* A row: its text (and 'Routine' tag), when it is due, then ✎ and ✕.
   The older form itemRow(when, text, onDelete, label, tag) still works (a
   row deleted at once, for other panels). */
export function itemRow(opts, ...legacy) {
  if (typeof opts !== "object" || opts === null) return legacyRow(opts, ...legacy);
  const { key, text, when = "", tag = "", hover = "", fact = false, deleteLabel, editLabel,
          url, undoText, edit = null } = opts;
  const row = document.createElement("li");
  row.className = fact ? "item fact" : "item";
  row.dataset.key = key;
  const txt = document.createElement("span");
  txt.className = "txt";
  if (tag) {
    const t = document.createElement("span");
    t.className = "tag";
    t.textContent = tag;
    txt.append(t, " ");
  }
  txt.append(text);
  if (hover) txt.title = hover;
  row.append(txt);
  if (when) {
    const w = document.createElement("span");
    w.className = "when";
    w.textContent = when;
    row.append(w);
  }
  if (edit) {
    const e = button("✎", "ibtn edit", editLabel);
    e.addEventListener("click", () => startEdit(row, { key, text, url, edit }));
    row.append(e);
  }
  const x = button("✕", "x", deleteLabel);
  x.addEventListener("click", (ev) => deleteLater({ key, url, undoText, row, byKeyboard: ev.detail === 0 }));
  row.append(x);
  return row;
}

function legacyRow(when, text, onDelete, label = "", tag = "") {
  const row = document.createElement("li");
  row.className = "item";
  const txt = document.createElement("span");
  txt.className = "txt";
  if (tag) {
    const t = document.createElement("span");
    t.className = "tag";
    t.textContent = tag;
    txt.append(t, " ");
  }
  txt.append(String(text ?? ""));
  const w = document.createElement("span");
  w.className = "when";
  w.textContent = String(when ?? "");
  const x = button("✕", "x", label || S.deleteReminder(String(text ?? "")));
  x.addEventListener("click", () => Promise.resolve(onDelete?.()).catch(err => console.warn(err)));
  row.append(txt, w, x);
  return row;
}

const pendingDeletes = new Map();  // key -> {url, toast, send}

function redraw(key) {
  if (key.startsWith("s:")) drawSchedules(); else drawMemory();
}

/* Pruned once the server's list no longer has it (the DELETE went through). */
function prune(prefix, items) {
  const ids = new Set(items.map(it => `${prefix}:${it.id}`));
  for (const key of [...hidden.keys()]) {
    if (key.startsWith(`${prefix}:`) && !ids.has(key) && !pendingDeletes.has(key)) hidden.delete(key);
  }
}

/* The row goes at once; the DELETE leaves when the toast goes (6 s, longer
   while the pointer or the focus holds it), unless 'Annuler' was pressed. */
function deleteLater({ key, url, undoText, row, byKeyboard }) {
  if (pendingDeletes.has(key)) return;
  // the focus moves to the next row's ✕ (or the section), never to <body>
  const nextKey = row.nextElementSibling?.dataset.key || row.previousElementSibling?.dataset.key;
  hidden.add(key);
  let done = false;
  const entry = { url, toast: null };
  entry.send = () => {
    if (done) return;
    done = true;
    pendingDeletes.delete(key);
    api(url, { method: "DELETE" }).catch((err) => {
      hidden.delete(key);
      redraw(key);
      toast(P.deleteFailed(err.message));
    });
  };
  const undo = () => {
    if (done) return;
    done = true;
    pendingDeletes.delete(key);
    hidden.delete(key);
    redraw(key);
    document.querySelector(`[data-key="${CSS.escape(key)}"] button.x`)?.focus({ preventScroll: true });
  };
  pendingDeletes.set(key, entry);
  redraw(key);
  entry.toast = toast(undoText, { actionLabel: P.undo, onAction: undo, ms: UNDO_MS, focus: byKeyboard });
  if (!byKeyboard) {
    const section = key.startsWith("s:") ? "schedules" : "memory";
    const next = nextKey && document.querySelector(`[data-key="${CSS.escape(nextKey)}"] button.x`);
    (next || $(section)?.querySelector("summary"))?.focus({ preventScroll: true });
  }
  watchToasts();
}

/* A toast that left without 'Annuler': its DELETE goes now. */
let toastWatch = null;
function watchToasts() {
  if (toastWatch || !$("toasts")) return;
  toastWatch = new MutationObserver(() => {
    for (const entry of [...pendingDeletes.values()]) if (entry.toast && !entry.toast.isConnected) entry.send();
  });
  toastWatch.observe($("toasts"), { childList: true });
}

/* Leaving the page: what was deleted is deleted (keepalive outlives the page). */
function flushDeletes() {
  for (const [key, entry] of [...pendingDeletes]) {
    pendingDeletes.delete(key);
    try {
      fetch(entry.url, { method: "DELETE", keepalive: true, headers: { "X-Jarvis-Token": TOKEN } });
    } catch { /* the page is going anyway */ }
  }
}

function isEditing(prefix) {
  return !!editing && editing.key.startsWith(`${prefix}:`) && !!editing.form?.isConnected;
}

function startEdit(row, { key, text, url, edit }) {
  if (editing?.form?.isConnected) editing.cancel();
  const form = document.createElement("form");
  form.className = "edit-form";
  const input = document.createElement("input");
  input.type = "text";
  input.value = text;
  input.maxLength = 500;
  input.setAttribute("aria-label", P.editField);
  input.autocomplete = "off";
  const save = button(P.save, "ctl primary");
  save.type = "submit";
  const cancel = button("✕", "x", P.cancelEdit);
  form.append(input, save, cancel);
  const txt = row.querySelector(".txt");
  txt.hidden = true;
  row.classList.add("editing");
  txt.after(form);
  const end = (redrawAfter = true) => {
    form.remove();
    txt.hidden = false;
    row.classList.remove("editing");
    if (editing?.form === form) editing = null;
    if (redrawAfter) redraw(key);
    document.querySelector(`[data-key="${CSS.escape(key)}"] .edit`)?.focus({ preventScroll: true });
  };
  editing = { key, form, cancel: () => end() };
  cancel.addEventListener("click", () => end());
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); end(); } });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const value = input.value.trim();
    if (!value || value === text) { end(); return; }
    save.disabled = true;
    try {
      await api(url, { method: "PATCH", body: { [edit.field]: value } });
      const list = edit.kind === "schedules" ? schedules : facts;
      const item = list.find(it => `${key[0]}:${it.id}` === key);
      if (item) item[edit.field] = value;  // the server's event confirms it right after
      end();
    } catch (err) {
      save.disabled = false;
      toast(P.saveFailed(err.message));
    }
  });
  input.focus();
  input.select();
}

/* Edit buttons only when the server can PATCH (WP14 memory, WP17 reminders):
   a route that does not exist yet answers 405 (the path has DELETE). */
async function probeEditing() {
  const probe = async (url) => {
    try {
      await api(url, { method: "PATCH", body: {} });
      return true;
    } catch (err) {
      // 405: no PATCH on that path; 404 'Not Found': no such path; 401/403: no answer at all.
      const missing = !Number.isFinite(err.status) || [401, 403, 405, 501].includes(err.status)
        || (err.status === 404 && /^not found$/i.test(err.message));
      return !missing;
    }
  };
  const [s, m] = await Promise.all([probe("/api/schedules/__sonde__"), probe("/api/memory/__sonde__")]);
  if (s !== editable.schedules) { editable.schedules = s; drawSchedules(); }
  if (m !== editable.memory) { editable.memory = m; drawMemory(); }
}

/* ---------------------------------------------------------- sections */
/* Each section is a <details open> with its count in the title (design
   spec §3); whether it is folded is remembered on this PC. */
function folded() {
  const v = settings.get("panels.folded", []);
  return Array.isArray(v) ? v : [];
}

function section(id, listId, title) {
  const sec = $(id), list = $(listId);
  if (!sec || !list) return;
  let details = sec.querySelector(":scope > details");
  if (!details) {
    details = document.createElement("details");
    const summary = document.createElement("summary");
    details.append(summary);
    sec.append(details);
  }
  details.classList.add("side-sec");
  const summary = details.querySelector(":scope > summary");
  let h2 = summary.querySelector("h2") || sec.querySelector(":scope > h2");
  if (!h2) h2 = document.createElement("h2");
  summary.replaceChildren(h2);
  h2.textContent = title;
  if (list.parentElement !== details) details.append(list);
  details.open = !folded().includes(id);
  details.addEventListener("toggle", () => {
    const rest = folded().filter(x => x !== id);
    settings.set("panels.folded", details.open ? rest : [...rest, id]);
  });
  heads[id] = h2;
}

function setHead(id, text) {
  if (heads[id]) setText(heads[id], text);
}

/* ---------------------------------------------------------- the clock */
function tick() {
  const now = Date.now() / 1000;
  for (const el of tasksEl.querySelectorAll(".task[data-started]")) {
    setText(el.querySelector(".chrono"), fmtElapsed(now - Number(el.dataset.started)));
  }
}

export function init() {
  tasksEl = $("taskList"); schedulesEl = $("scheduleList"); memoryEl = $("memoryList");
  if (!tasksEl || !schedulesEl || !memoryEl) return;
  section("tasks", "taskList", P.tasks);
  section("schedules", "scheduleList", P.reminders);
  section("memory", "memoryList", P.memory);
  tasksEl.replaceChildren(emptyLine(T.empty.tasks));
  bus.on("server:task", renderTask);
  bus.on("server:schedules", (ev) => renderSchedules(ev?.items));
  bus.on("server:memory", (ev) => renderMemory(ev?.facts));
  bus.on("server:pending", (ev) => onPending(ev?.pending));
  // On (re)connection: the requests still open, and whether editing exists.
  let probed = false;
  bus.on("sse:open", () => {
    api("/api/pending").then((items) => {
      waiting.clear();
      (Array.isArray(items) ? items : []).forEach(onPending);
      renderTasks();
    }).catch((err) => console.warn(err));
    if (!probed) { probed = true; probeEditing().catch((err) => console.warn(err)); }
  });
  setInterval(tick, 1000);
  // Relative times, the move to 'Historique', expired requests, the day groups.
  setInterval(() => { renderTasks(); drawSchedules(); }, 30000);
  addEventListener("pagehide", flushDeletes);
}
