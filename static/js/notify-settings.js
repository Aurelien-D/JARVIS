/* Owned by package B1 (JARVIS on iPhone): Réglages › Notifications (ntfy),
   added with settings.registerSection().
   - On the PC: the four settings (on or off, the server, only when monsieur
     is away, the reminder's text), the topic in monospace with « Copier le
     sujet », the server it lives on, « Envoyer un test » and « Nouveau sujet »
     (after a confirmation: the iPhone must subscribe again).
   - On the paired iPhone: the topic, « Copier le sujet », « Envoyer un test »
     and the four steps to subscribe in the ntfy app. The settings themselves
     change on the PC only (the server refuses them from the phone).
   - The topic and every server text go in through textContent. */
import { api, bus } from "./core.js";
import { T, explainError, fmtTime } from "./strings-fr.js";
import { button, h } from "./onboarding.js";
import { askConfirm, registerSection } from "./settings.js";

const N = T.notify;
const DEFAULT_SERVER = "https://ntfy.sh";

let view = null;  // the section on screen: { root, ctx, data }

function say(el, text, error = false) {
  if (!el) return;
  el.className = error ? "set-status err" : "set-status";
  el.textContent = text || "";
}

function statusLine(id) { return h("p", { class: "set-status", id, role: "status" }); }

function onScreen(v) {
  return v === view && v.root.isConnected && !v.root.closest("[hidden]") && !!v.root.closest("dialog[open]");
}

function stateText(v) {
  if (v.data.enabled) return N.on;
  return v.ctx.remote ? N.off : N.offPc;
}

function lastText(data) {
  if (data.last_error) return N.lastError(String(data.last_error));
  const sent = Number(data.last_sent) || 0;
  return sent > 0 ? N.lastSent(fmtTime(new Date(sent * 1000))) : "";
}

/* ---------------------------------------------------------- actions */
async function copyTopic(v) {
  const status = v.root.querySelector("#nt-copy-status");
  try {
    await navigator.clipboard.writeText(String(v.data.topic || ""));
    say(status, N.copied);
  } catch {
    // iOS may refuse the clipboard: the topic is selected for a manual copy.
    const code = v.root.querySelector("#nt-topic");
    if (code) window.getSelection()?.selectAllChildren(code);
    say(status, N.copyFailed, true);
  }
}

async function refreshLast(v) {
  try {
    const data = await api("/api/notify");
    if (v !== view) return;
    v.data = data;
    const last = v.root.querySelector("#nt-last");
    if (last) last.textContent = lastText(data);
  } catch { /* the test's own answer is already on screen */ }
}

async function sendTest(v, btn) {
  const status = v.root.querySelector("#nt-test-status");
  btn.disabled = true;
  say(status, N.sending);
  try {
    const r = await api("/api/notify/test", { method: "POST" });
    if (r && r.ok) say(status, N.sent);
    else say(status, String(r?.error || T.error.unknown), true);
  } catch (err) {
    say(status, explainError(err), true);
  } finally {
    btn.disabled = false;
  }
  await refreshLast(v);
}

async function renewTopic(v) {
  const ok = await askConfirm({ title: N.newTitle, text: N.newAsk, danger: N.newWarn, ok: N.newTopic });
  if (!ok || v !== view) return;
  try {
    const r = await api("/api/notify/topic", { method: "POST" });
    v.data = { ...v.data, topic: String(r.topic || "") };
    render(v);
    say(v.root.querySelector("#nt-copy-status"), N.newDone);
  } catch (err) {
    say(v.root.querySelector("#nt-test-status"), explainError(err), true);
  }
}

/* ---------------------------------------------------------- drawing */
function topicBlock(v) {
  const d = v.data;
  const code = h("code", { class: "mono nt-topic-value", id: "nt-topic", text: String(d.topic || "") });
  const copy = button(N.copyTopic, () => copyTopic(v), "ctl primary nt-copy");
  copy.id = "nt-copy";
  return h("div", { class: "set-field nt-block", "data-key": "ntfy-topic" },
    h("h4", { class: "set-sub", id: "nt-topic-title", text: N.topicTitle }),
    h("div", { class: "nt-topic-line" }, code, copy),
    h("p", { class: "set-help nt-server", id: "nt-server", text: N.server(String(d.server || DEFAULT_SERVER)) }),
    h("p", { class: "set-help", text: N.topicHelp }),
    statusLine("nt-copy-status"));
}

function testBlock(v) {
  const test = button(N.test, () => sendTest(v, test));
  test.id = "nt-test";
  const actions = h("div", { class: "set-actions" }, test);
  if (!v.ctx.remote) {
    const renew = button(N.newTopic, () => renewTopic(v));
    renew.id = "nt-new";
    actions.append(renew);
  }
  return h("div", { class: "set-field nt-block", "data-key": "ntfy-test" },
    h("p", { class: v.data.enabled ? "nt-state" : "nt-state nt-off", id: "nt-state", text: stateText(v) }),
    actions,
    h("p", { class: "set-help nt-last", id: "nt-last", text: lastText(v.data) }),
    statusLine("nt-test-status"));
}

function guide(v) {
  const server = String(v.data.server || DEFAULT_SERVER);
  const steps = [N.step1, N.step2, server === DEFAULT_SERVER ? N.step3 : N.step3Server(server), N.step4];
  return h("div", { class: "set-field nt-block", "data-key": "ntfy-guide" },
    h("h4", { class: "set-sub", id: "nt-guide-title", text: N.guideTitle }),
    h("ol", { class: "nt-guide", "aria-labelledby": "nt-guide-title" }, ...steps.map(text => h("li", { text }))));
}

function render(v) {
  const parts = [topicBlock(v), testBlock(v)];
  if (v.ctx.remote) parts.push(guide(v));
  v.root.replaceChildren(...parts);
}

async function load(v) {
  let data;
  try {
    data = await api("/api/notify");
  } catch (err) {
    if (v === view) {
      v.root.replaceChildren(h("p", { class: "set-note", text: err?.status === 501 ? N.notYet : explainError(err) }));
    }
    return;
  }
  if (v !== view) return;
  v.data = data;
  render(v);
}

/* ---------------------------------------------------------- the section */
function build(ctx) {
  const root = h("div", { class: "nt-root" }, h("p", { class: "set-loading", role: "status", text: N.loading }));
  view = { root, ctx, data: null };
  load(view);
  // The settings themselves: the PC only (the phone's schema has none of them).
  const fields = ctx.remote ? [] : ctx.entriesOf("notifications").map(ctx.field);
  return [h("p", { class: "set-note", text: N.intro }), ...fields, root];
}

export function init() {
  registerSection("notifications", build);
  // A setting of this section saved: the state line and the server follow.
  bus.on("settings:changed", ({ key } = {}) => {
    if (!String(key || "").startsWith("ntfy") || !view?.data || !onScreen(view)) return;
    load(view);
  });
  bus.on("ui:close", (name) => { if (name === "settings") view = null; });
}
