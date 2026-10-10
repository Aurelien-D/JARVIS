/* Owned by package B3 (JARVIS on iPhone): the « Assistant raccourci » screen
   on the paired iPhone, opened from its view of Réglages › Accès à distance
   (the [data-slot="raccourci"] element remote-settings.js leaves, announced
   by the bus event "remote:phone").
   - The screen collects the Siri key the PC created for this iPhone, once
     (GET /api/remote/siri-key): four rows (URL, header name, header value,
     body field), each with its own [Copier], and the 3-action recipe.
   - The key lives only in this screen: closing it empties it, and the server
     never hands it out again.
   - Every value is set with textContent. */
import { api, bus } from "./core.js";
import { T, explainError } from "./strings-fr.js";
import { button, h } from "./onboarding.js";

const S = T.siri;
let screen = null;   // the <dialog>, built once

/* Copies one value; the status line says which, or how to copy by hand. */
async function copy(value, what, status, code) {
  try {
    await navigator.clipboard.writeText(value);
    status.className = "set-status siri-status";
    status.textContent = S.copied(what);
  } catch {
    // No clipboard (an old Safari, a refused permission): select it for a long press.
    try {
      const range = document.createRange();
      range.selectNodeContents(code);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch { /* nothing to select */ }
    status.className = "set-status siri-status err";
    status.textContent = S.copyFailed;
  }
}

function row(id, label, value, status) {
  const code = h("code", { class: "mono siri-value", id: `siri-value-${id}`, text: value });
  const b = button(S.copy, () => copy(value, label, status, code), "ctl siri-copy");
  b.id = `siri-copy-${id}`;
  b.setAttribute("aria-label", S.copyLabel(label));
  return h("div", { class: "siri-row", "data-row": id },
    h("p", { class: "set-label siri-label", id: `siri-label-${id}`, text: label }),
    h("div", { class: "siri-line" }, code, b));
}

function recipe() {
  return h("section", { class: "siri-recipe", "aria-labelledby": "siri-recipe-title" },
    h("h4", { class: "set-sub", id: "siri-recipe-title", text: S.recipeTitle }),
    h("p", { class: "set-help", text: S.recipeStart }),
    h("ol", { class: "siri-steps" }, ...S.recipe.map(step => h("li", { text: step }))),
    h("p", { class: "set-help", text: S.recipeEnd }));
}

/* The collected key, drawn as four rows to copy. */
function filled(body, data) {
  const status = h("p", { class: "set-status siri-status", role: "status", "aria-live": "polite" });
  const rows = [["url", S.rowUrl, data.url], ["header", S.rowHeader, data.header],
                ["value", S.rowValue, data.value], ["field", S.rowField, "text"]];
  body.replaceChildren(
    h("p", { class: "set-danger siri-once", text: S.once }),
    ...rows.map(([id, label, value]) => row(id, label, String(value ?? ""), status)),
    status, recipe());
}

function build() {
  const body = h("div", { class: "siri-body", id: "siri-body" });
  const close = button(S.close, () => { body.replaceChildren(); screen.close(); }, "ctl primary");
  close.id = "siri-close";
  screen = h("dialog", { class: "set-confirm siri-screen", id: "siriAssistant", "aria-labelledby": "siriTitle" },
    h("h3", { id: "siriTitle", text: S.assistant }), body, h("div", { class: "set-actions" }, close));
  // Closed (button, Échap, Réglages closing): the key goes with it.
  screen.addEventListener("close", () => body.replaceChildren());
  document.body.append(screen);
  return screen;
}

/* Opens the screen and collects the waiting key (404: none waits). */
export async function openAssistant() {
  const d = screen || build();
  const body = d.querySelector("#siri-body");
  body.replaceChildren(h("p", { class: "set-loading", role: "status", text: S.loading }));
  if (!d.open) d.showModal();
  d.querySelector("#siri-close").focus();
  try {
    const data = await api("/api/remote/siri-key");
    if (!d.open) return;  // closed meanwhile: never drawn
    filled(body, data && typeof data === "object" ? data : {});
  } catch (err) {
    if (!d.open) return;
    body.replaceChildren(h("p", { class: "set-note siri-none", role: "status",
                                  text: err?.status === 404 ? S.none : explainError(err) }));
  }
}

function fillSlot({ slot } = {}) {
  if (!slot) return;
  const open = button(S.assistant, () => openAssistant(), "ctl primary");
  open.id = "siri-open";
  slot.replaceChildren(
    h("h4", { class: "set-sub", text: S.title }),
    h("p", { class: "set-help", text: S.phoneHelp }),
    h("div", { class: "set-actions" }, open));
}

export function init() {
  bus.on("remote:phone", fillSlot);
  bus.on("ui:close", (name) => { if (name === "settings" && screen?.open) screen.close(); });
}
