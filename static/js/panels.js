/* Side panel: Claude Code task cards, reminders and routines, memory.
   Fed by the server's live events (sse.js puts them on the bus). */
import { $, api, bus } from "./core.js";

const PROFILE_LABEL = { recherche: "web", lecture: "lecture seule", complet: "accès complet" };
const ORIGIN_LABEL = { routine: "routine", briefing: "briefing" };
const REPEAT_LABEL = { daily: "chaque jour", weekdays: "en semaine", weekly: "chaque semaine" };

let tasksEl, schedulesEl, memoryEl;

export function renderTask(tk) {
  let el = document.getElementById(`task-${tk.id}`);
  if (!el) {
    tasksEl.querySelector(".none")?.remove();
    el = document.createElement("div");
    el.className = "task"; el.id = `task-${tk.id}`;
    el.innerHTML = `<div class="t"></div><div class="meta"></div>
      <div class="s"><span class="dot"></span><span class="lbl">Claude Code travaille…</span> <span class="chrono"></span>
      <span class="cancel" title="annuler">✕</span></div>
      <div class="prog"></div><div class="out"></div>`;
    el.querySelector(".t").textContent = tk.title;
    el.querySelector(".cancel").addEventListener("click", () =>
      api(`/api/task/${tk.id}/cancel`, { method: "POST" }).catch(err => console.warn(err)));
    tasksEl.prepend(el);
    while (tasksEl.children.length > 20) tasksEl.lastChild.remove();
  }
  el.querySelector(".meta").textContent = [
    PROFILE_LABEL[tk.profile] || tk.profile, tk.model || "modèle par défaut",
    tk.resumed_from ? `suite de ${tk.resumed_from}` : "", ORIGIN_LABEL[tk.origin] || "",
  ].filter(Boolean).join(" · ");
  el._started = tk.started * 1000;
  if (tk.status === "running") {
    el.querySelector(".prog").textContent = tk.progress || "";
    if (!el._timer) el._timer = setInterval(() => tickChrono(el), 1000);
    tickChrono(el);
    return;
  }
  clearInterval(el._timer); el._timer = null;
  el.classList.add({ done: "done", cancelled: "cancelled" }[tk.status] || "error");
  el.querySelector(".lbl").textContent = { done: "Terminé", cancelled: "Annulée" }[tk.status] || "Erreur";
  el.querySelector(".chrono").textContent = tk.ended ? `${Math.round(tk.ended - tk.started)}s` : "";
  el.querySelector(".prog").textContent = "";
  el.querySelector(".out").textContent = (tk.output || "").slice(0, 1200);
  el.querySelector(".cancel")?.remove();
}

function tickChrono(el) {
  el.querySelector(".chrono").textContent = Math.round((Date.now() - el._started) / 1000) + "s";
}

export function itemRow(when, text, onDelete) {
  const row = document.createElement("div");
  row.className = "item";
  row.innerHTML = `<span class="when"></span><span class="txt"></span><span class="x" title="supprimer">✕</span>`;
  row.querySelector(".when").textContent = when;
  row.querySelector(".txt").textContent = text;
  row.querySelector(".x").addEventListener("click", () => onDelete().catch(err => console.warn(err)));
  return row;
}

function fmtWhen(d) {
  const now = new Date(), tomorrow = new Date(now.getTime() + 86400e3);
  const hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return hm;
  if (d.toDateString() === tomorrow.toDateString()) return `demain ${hm}`;
  return `${d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" })} ${hm}`;
}

export function renderSchedules(items) {
  schedulesEl.innerHTML = "";
  if (!items.length) {
    schedulesEl.innerHTML = `<div class="none">Aucun rappel. « Rappelle-moi dans dix minutes de… »</div>`;
    return;
  }
  for (const it of items) {
    const repeat = REPEAT_LABEL[it.repeat] ? ` · ${REPEAT_LABEL[it.repeat]}` : "";
    schedulesEl.append(itemRow(fmtWhen(new Date(it.due * 1000)) + repeat,
      (it.kind === "task" ? "⚙ " : "") + it.title,
      () => api(`/api/schedules/${it.id}`, { method: "DELETE" })));
  }
}

export function renderMemory(facts) {
  memoryEl.innerHTML = "";
  if (!facts.length) {
    memoryEl.innerHTML = `<div class="none">Rien encore. « Retiens que je préfère… »</div>`;
    return;
  }
  for (const f of facts.slice().reverse()) {
    memoryEl.append(itemRow("•", f.text, () => api(`/api/memory/${f.id}`, { method: "DELETE" })));
  }
}

export function init() {
  tasksEl = $("taskList"); schedulesEl = $("scheduleList"); memoryEl = $("memoryList");
  bus.on("server:task", renderTask);
  bus.on("server:schedules", (ev) => renderSchedules(ev.items));
  bus.on("server:memory", (ev) => renderMemory(ev.facts));
}
