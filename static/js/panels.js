/* Side panel: Claude Code task cards, reminders and routines, memory.
   Fed by the server's live events (sse.js puts them on the bus).
   Interim until WP10 rebuilds it: real buttons, the words of strings-fr.js
   and French times, the output as sanitised markdown. */
import { $, api, bus, md } from "./core.js";
import { T, fmtElapsed, fmtTime } from "./strings-fr.js";

const S = T.task;
const ORIGIN_LABEL = { routine: S.routine, briefing: S.briefing };
const REPEAT_LABEL = { daily: "chaque jour", weekdays: "en semaine", weekly: "chaque semaine" };
const dayFormat = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short" });

let tasksEl, schedulesEl, memoryEl;

export function renderTask(tk) {
  let el = document.getElementById(`task-${tk.id}`);
  if (!el) {
    tasksEl.querySelector(".none")?.remove();
    el = document.createElement("div");
    el.className = "task"; el.id = `task-${tk.id}`;
    el.innerHTML = `<div class="t"></div><div class="meta"></div>
      <div class="s"><span class="dot"></span><span class="lbl"></span> <span class="chrono"></span>
      <button type="button" class="x cancel"><span aria-hidden="true">✕</span></button></div>
      <div class="prog"></div><div class="out md"></div>`;
    el.querySelector(".t").textContent = tk.title;
    el.querySelector(".lbl").textContent = S.working;
    const cancel = el.querySelector(".cancel");
    cancel.setAttribute("aria-label", S.cancelLabel(tk.title || ""));
    cancel.addEventListener("click", () =>
      api(`/api/task/${tk.id}/cancel`, { method: "POST" }).catch(err => console.warn(err)));
    tasksEl.prepend(el);
    while (tasksEl.children.length > 20) tasksEl.lastChild.remove();
  }
  el.querySelector(".meta").textContent = [
    S.profile[tk.profile] || tk.profile, tk.model || S.defaultModel,
    tk.resumed_from ? S.followUp(titleOf(tk.resumed_from)) : "", ORIGIN_LABEL[tk.origin] || "",
  ].filter(Boolean).join(" · ");
  el._started = tk.started * 1000;
  if (tk.status === "running") {
    el.querySelector(".prog").textContent = tk.progress || "";
    if (!el._timer) el._timer = setInterval(() => tickChrono(el), 1000);
    tickChrono(el);
    return;
  }
  clearInterval(el._timer); el._timer = null;
  const look = { done: "done", cancelled: "cancelled", error: "error", interrompue: "error", interrupted: "error" };
  if (look[tk.status]) el.classList.add(look[tk.status]);  // a queued task is no failure
  el.querySelector(".lbl").textContent = S.status[tk.status] || S.status.error;
  el.querySelector(".chrono").textContent = tk.ended ? fmtElapsed(tk.ended - tk.started) : "";
  el.querySelector(".prog").textContent = "";
  const out = el.querySelector(".out");
  const text = (tk.output || "").slice(0, 1200);
  out.innerHTML = text ? md(text) : "";
  // A scroller the keyboard reaches, named after its task.
  if (text) {
    out.tabIndex = 0;
    out.setAttribute("role", "region");
    out.setAttribute("aria-label", S.outputLabel(tk.title || ""));
  }
  el.querySelector(".cancel")?.remove();
}

/* The task a follow-up continues, by its title when it is on screen. */
function titleOf(id) {
  return document.getElementById(`task-${id}`)?.querySelector(".t")?.textContent || id;
}

function tickChrono(el) {
  el.querySelector(".chrono").textContent = fmtElapsed((Date.now() - el._started) / 1000);
}

/* A row with its delete button. label: what the button says to a screen
   reader; tag: a word before the text ('Routine'). */
export function itemRow(when, text, onDelete, label = "", tag = "") {
  const row = document.createElement("div");
  row.className = "item";
  row.innerHTML = `<span class="when"></span><span class="txt"></span>`
    + `<button type="button" class="x"><span aria-hidden="true">✕</span></button>`;
  row.querySelector(".when").textContent = when;
  const txt = row.querySelector(".txt");
  if (tag) {
    const t = document.createElement("span");
    t.className = "tag";
    t.textContent = tag;
    txt.append(t, " ");
  }
  txt.append(text);
  const x = row.querySelector(".x");
  x.setAttribute("aria-label", label || S.deleteReminder(text));
  x.addEventListener("click", () => onDelete().catch(err => console.warn(err)));
  return row;
}

/* '17 h 41', 'demain à 8 h', 'lun. 12 oct. à 9 h 30' (fmtTime, design spec §5). */
function fmtWhen(d) {
  const now = new Date(), tomorrow = new Date(now.getTime() + 86400e3);
  if (d.toDateString() === now.toDateString()) return fmtTime(d);
  if (d.toDateString() === tomorrow.toDateString()) return T.time.tomorrow(fmtTime(d));
  return T.time.onDate(dayFormat.format(d), fmtTime(d));
}

function emptyLine(text) {
  const div = document.createElement("div");
  div.className = "none";
  div.textContent = text;
  return div;
}

export function renderSchedules(items) {
  schedulesEl.replaceChildren();
  if (!items.length) {
    schedulesEl.append(emptyLine(T.empty.reminders));
    return;
  }
  for (const it of items) {
    const repeat = REPEAT_LABEL[it.repeat] ? ` · ${REPEAT_LABEL[it.repeat]}` : "";
    schedulesEl.append(itemRow(fmtWhen(new Date(it.due * 1000)) + repeat, it.title || "",
      () => api(`/api/schedules/${it.id}`, { method: "DELETE" }),
      S.deleteReminder(it.title || ""), it.kind === "task" ? S.routine : ""));
  }
}

export function renderMemory(facts) {
  memoryEl.replaceChildren();
  if (!facts.length) {
    memoryEl.append(emptyLine(T.empty.memory));
    return;
  }
  for (const f of facts.slice().reverse()) {
    memoryEl.append(itemRow("•", f.text, () => api(`/api/memory/${f.id}`, { method: "DELETE" }),
      S.forget(f.text)));
  }
}

export function init() {
  tasksEl = $("taskList"); schedulesEl = $("scheduleList"); memoryEl = $("memoryList");
  bus.on("server:task", renderTask);
  bus.on("server:schedules", (ev) => renderSchedules(ev.items));
  bus.on("server:memory", (ev) => renderMemory(ev.facts));
}
