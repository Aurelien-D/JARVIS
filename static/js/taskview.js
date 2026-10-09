/* Task viewer (design spec §3): a modal <dialog id="taskView"> with a Claude
   Code task's whole story: what was asked, profile, model, duration, steps,
   estimated cost, the step log, the full output (markdown through md(),
   sanitised), the files it wrote with 'Afficher dans l'explorateur', and what
   Claude was refused. Opened by a card's 'Lire'; Échap or ✕ closes it and the
   focus goes back to that button. Everything shown is text or sanitised
   markdown: a task's output is outside content. */
import { $, api, bus, md } from "./core.js";
import { T, fmtNumber, fmtTime, fr } from "./strings-fr.js";
import { toast } from "./hud.js";

const V = T.taskview, S = T.task;
const ACTIVE = new Set(["running", "en_file"]);
const dateFormat = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });

let dlg = null;
let opener = null;        // the 'Lire' button: the focus goes back there
let current = null;       // the task on screen
let seq = 0;              // a newer task wins over a slow answer
let revealOk = true;      // false once the server says it can't show files here
let logTimer = null, liveTimer = null;
const el = {};

/* '45 s', '3 min 05 s', '1 h 02 min'. */
export function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return V.seconds(s);
  if (s < 3600) return V.minutes(Math.floor(s / 60), String(s % 60).padStart(2, "0"));
  return V.hours(Math.floor(s / 3600), String(Math.floor((s % 3600) / 60)).padStart(2, "0"));
}

/* Claude Code's own figure (total_cost_usd): what the same work would cost
   on the API, not what monsieur's plan charges. */
function costText(usd) {
  const n = Number(usd);
  if (!Number.isFinite(n) || n <= 0) return "";
  const amount = n < 0.01 ? `< ${fmtNumber(0.01, { minimumFractionDigits: 2 })} $`
    : `${fmtNumber(n, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} $`;
  return V.costValue(amount);
}

function seconds(tk) {
  if (ACTIVE.has(tk.status)) return Date.now() / 1000 - (Number(tk.started) || Date.now() / 1000);
  if (Number.isFinite(Number(tk.duration_ms)) && Number(tk.duration_ms) > 0 && !(tk.ended && tk.started)) {
    return Number(tk.duration_ms) / 1000;
  }
  return tk.ended && tk.started ? Number(tk.ended) - Number(tk.started) : NaN;
}

function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined) n.textContent = text;
  return n;
}

function section(className, title) {
  const sec = node("section", `tv-sec ${className}`);
  const head = node("div", "tv-sec-head");
  head.append(node("h3", "", title));
  sec.append(head);
  return sec;
}

function build() {
  const head = node("header", "tv-head");
  el.title = node("h2", "");
  el.title.id = "tvTitle";
  const close = node("button", "x");
  close.type = "button";
  close.setAttribute("aria-label", V.close);
  close.innerHTML = `<span aria-hidden="true">✕</span>`;
  close.addEventListener("click", () => dlg.close());
  head.append(el.title, close);

  el.status = node("p", "tv-status");
  el.facts = node("dl", "tv-facts");
  el.facts.setAttribute("aria-label", V.facts);

  el.prompt = section("tv-prompt", V.prompt);
  el.promptText = node("div", "tv-text");
  el.promptText.tabIndex = 0;  // a long prompt scrolls: the keyboard must reach it
  el.promptText.setAttribute("role", "region");
  el.promptText.setAttribute("aria-label", V.prompt);
  el.prompt.append(el.promptText);

  el.note = section("tv-note", V.note);
  el.noteText = node("p", "tv-text");
  el.note.append(el.noteText);

  el.output = section("tv-output", V.output);
  el.copy = node("button", "ctl copy", V.copyOutput);
  el.copy.type = "button";
  el.copy.addEventListener("click", copyOutput);
  el.output.querySelector(".tv-sec-head").append(el.copy);
  el.outputBody = node("div", "tv-md md");
  el.output.append(el.outputBody);

  el.files = section("tv-files", V.files);
  el.filesList = node("ul", "tv-list");
  el.files.append(el.filesList);

  el.denials = section("tv-denials", V.denials);
  el.denialsList = node("ul", "tv-list");
  el.denials.append(node("p", "tv-intro", V.denialsIntro), el.denialsList);

  el.log = section("tv-log", V.log);
  el.logList = node("ol", "tv-list tv-steps");
  el.log.append(el.logList);

  const body = node("div", "tv-body");
  body.append(el.status, el.facts, el.prompt, el.note, el.output, el.files, el.denials, el.log);
  dlg.replaceChildren(head, body);
}

function fact(label, value, title = "") {
  if (value === "" || value === null || value === undefined) return null;
  const row = node("div");
  const dd = node("dd", "", String(value));
  if (title) dd.title = title;
  row.append(node("dt", "", label), dd);
  return row;
}

function render(tk) {
  current = tk;
  el.title.textContent = String(tk.title || "") || "Tâche";
  el.status.replaceChildren(node("span", `dot ${tk.status || ""}`), node("span", "lbl", S.status[tk.status] || S.status.error));
  el.status.querySelector(".dot").setAttribute("aria-hidden", "true");
  el.status.dataset.status = tk.status || "";
  renderFacts(tk);

  el.promptText.textContent = String(tk.prompt || "");
  el.prompt.hidden = !String(tk.prompt || "").trim();
  el.noteText.textContent = String(tk.note || "");
  el.note.hidden = !String(tk.note || "").trim();

  const output = String(tk.output || "");
  if (el.outputBody.dataset.raw !== output) {
    el.outputBody.dataset.raw = output;
    if (output.trim()) el.outputBody.innerHTML = md(output);
    else el.outputBody.replaceChildren(node("p", "tv-empty", ACTIVE.has(tk.status) ? S.working : V.noOutput));
  }
  el.copy.hidden = !output.trim();

  renderFiles(tk);
  const denials = (Array.isArray(tk.permission_denials) ? tk.permission_denials : []).filter(d => d && d.tool);
  el.denialsList.replaceChildren(...denials.map((d) => {
    const li = node("li");
    li.append(node("b", "", String(d.tool)));
    if (d.detail && d.detail !== d.tool) li.append("\u202f: ", String(d.detail));  // French colon
    return li;
  }));
  el.denials.hidden = !denials.length;
}

function renderFacts(tk) {
  const followed = tk.resumed_from ? $(`task-${tk.resumed_from}`)?.querySelector(".t")?.textContent : "";
  const started = Number(tk.started) ? new Date(Number(tk.started) * 1000) : null;
  const rows = [
    fact(V.profile, S.profile[tk.profile] || tk.profile || ""),
    fact(V.model, tk.model || S.defaultModel),
    fact(V.complexity, S.complexity[tk.complexity] || tk.complexity || ""),
    fact(V.duration, Number.isFinite(seconds(tk)) ? fmtDuration(seconds(tk)) : ""),
    fact(V.steps, Number.isFinite(Number(tk.steps)) && tk.steps !== null ? String(tk.steps) : ""),
    fact(V.cost, costText(tk.cost_usd)),
    fact(V.started, started ? `${dateFormat.format(started)} à ${fmtTime(started)}` : ""),
    fact(V.origin, V.origins[tk.origin] || ""),
    fact(V.followUp, tk.resumed_from ? fr(`« ${followed || tk.resumed_from} »`) : ""),
  ].filter(Boolean);
  el.facts.replaceChildren(...rows);
}

function renderFiles(tk) {
  const files = (Array.isArray(tk.files) ? tk.files : []).filter(f => typeof f === "string" && f);
  el.filesList.replaceChildren(...files.map((path) => {
    const li = node("li", "tv-file");
    const name = path.split(/[\\/]/).filter(Boolean).pop() || path;
    const code = node("code", "path", path);
    code.title = path;
    li.append(code);
    if (revealOk) {
      const b = node("button", "ctl reveal", V.reveal);
      b.type = "button";
      b.setAttribute("aria-label", V.revealLabel(name));
      b.addEventListener("click", () => reveal(tk.id, path, b));
      li.append(b);
    }
    return li;
  }));
  el.files.hidden = !files.length;
}

/* Shown in its folder, never opened (the server checks the task wrote it). */
async function reveal(id, path, b) {
  b.disabled = true;
  try {
    await api(`/api/task/${encodeURIComponent(id)}/reveal`, { method: "POST", body: { path } });
  } catch (err) {
    // No such route, or this system can't show it: the buttons go (design: hidden on 404).
    if ([405, 501].includes(err.status) || (err.status === 404 && /^not found$/i.test(err.message))) {
      revealOk = false;
      el.filesList.querySelectorAll(".reveal").forEach(x => x.remove());
      dlg.querySelector(".tv-head .x")?.focus();
      toast(V.revealUnavailable);
      return;
    }
    toast(V.revealFailed(err.message));
  } finally {
    b.disabled = false;
  }
}

async function loadLog(id, mine) {
  if (!el.logList.children.length) el.logList.replaceChildren(node("li", "tv-empty", V.logLoading));
  try {
    const out = await api(`/api/task/${encodeURIComponent(id)}/log`);
    if (mine !== seq) return;
    const lines = Array.isArray(out?.log) ? out.log : [];
    el.logList.replaceChildren(...lines.map((line) => {
      const li = node("li");
      const t = Number(line?.t);
      if (t) {
        const time = node("time", "", fmtTime(new Date(t * 1000)));
        time.dateTime = new Date(t * 1000).toISOString();
        li.append(time, " ");
      }
      li.append(String(line?.text ?? ""));
      return li;
    }));
    if (!lines.length) el.logList.replaceChildren(node("li", "tv-empty", V.logEmpty));
  } catch {
    if (mine === seq) el.logList.replaceChildren(node("li", "tv-empty", V.logFailed));
  }
}

async function copyOutput() {
  try {
    await navigator.clipboard.writeText(String(current?.output || ""));
    toast(T.controls.copied, { ms: 2500 });
  } catch {
    toast(T.hud.copyFailed);
  }
}

/* Open the viewer on a task (a snapshot from the panel); the server's fresh
   copy replaces it as soon as it answers. */
export function openTask(tk, from = document.activeElement) {
  if (!dlg || !tk || !tk.id) return;
  const mine = ++seq;
  if (!dlg.open) opener = from && from !== document.body ? from : null;
  el.outputBody.dataset.raw = "\u0000";  // force a fresh render
  el.logList.replaceChildren();
  render(tk);
  if (!dlg.open) {
    try {
      dlg.showModal();
    } catch (err) {
      console.warn(err);
      dlg.setAttribute("open", "");
    }
  }
  dlg.scrollTop = 0;
  api(`/api/task/${encodeURIComponent(tk.id)}`)
    .then((fresh) => { if (mine === seq && dlg.open && fresh?.id) render(fresh); })
    .catch(() => { /* a task the server no longer keeps: the snapshot stays */ });
  loadLog(tk.id, mine);
  clearInterval(liveTimer);
  liveTimer = setInterval(() => { if (dlg.open && current && ACTIVE.has(current.status)) renderFacts(current); }, 1000);
}

export function closeTask() {
  if (dlg?.open) dlg.close();
}

function onClose() {
  if (dlg.open) return;  // reopened at once: this late event is stale
  seq++;
  clearInterval(liveTimer);
  clearTimeout(logTimer);
  const id = current?.id;
  current = null;
  const back = opener;
  opener = null;
  const lost = !document.activeElement || document.activeElement === document.body || dlg.contains(document.activeElement);
  if (!lost) return;
  // The card may have been redrawn while the viewer was open: its 'Lire' then.
  const target = back && back.isConnected ? back : id ? document.getElementById(`task-${id}`)?.querySelector(".read") : null;
  if (target && !target.closest("[inert]")) target.focus({ preventScroll: true });
  if (document.activeElement === document.body) $("orbBtn")?.focus({ preventScroll: true });
}

export function init() {
  dlg = $("taskView");
  if (!dlg) return;
  dlg.classList.add("tv");
  build();
  dlg.addEventListener("close", onClose);
  // A click on the backdrop closes it (not a text selection dragged out of it).
  const outside = (e) => {
    if (e.target !== dlg) return false;
    const b = dlg.getBoundingClientRect();
    return e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom;
  };
  let downOutside = false;
  dlg.addEventListener("pointerdown", (e) => { downOutside = outside(e); });
  dlg.addEventListener("click", (e) => { if (downOutside && outside(e)) dlg.close(); downOutside = false; });
  // Live while open: progress, output, and the step log (at most once a second).
  bus.on("server:task", (tk) => {
    if (!dlg.open || !current || !tk || tk.id !== current.id) return;
    render({ ...current, ...tk });
    clearTimeout(logTimer);
    const mine = seq;
    logTimer = setTimeout(() => loadLog(tk.id, mine), 1000);
  });
}
