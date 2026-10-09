/* Telling monsieur things: task results, reminders, briefings. Said now if a
   session is live; otherwise a chime, a short spoken notice and the full
   message kept for the next session. Other modules ask through
   bus.emit("deliver", {text, kind, priority, spoken}). */
import { bus, state, touch } from "./core.js";
import { earcon } from "./audio-fx.js";
import { addCard } from "./hud.js";
import { isLive, requestResponse, sendNotice } from "./voice.js";
import { startWake, stopWake } from "./wake.js";

/* kind: task | briefing | reminder | ... A reminder spoken aloud is not
   kept for later; anything else is, so JARVIS can tell the details. */
export function deliver({ text, kind = "info", priority = "normal", spoken = "", queue } = {}) {
  if (!text) return;
  const keep = queue ?? kind !== "reminder";
  if (isLive()) {
    sendNotice(text);
    requestResponse();
    touch();
    return;
  }
  if (state.mode === "connecting") { // about to go live: it will be said then
    state.queue.push(text);
    return;
  }
  earcon("alert");
  const said = spoken ? speak(spoken) : false;
  notify(spoken || text);
  if (keep || !said) {
    state.queue.push(text);
    if (state.queue.length > 10) state.queue.shift();
  }
}

/* Leader election between pages arrives with the inbox (WP09). */
export function isLeader() { return true; }
export function pendingCount() { return state.queue.length; }

export function speak(text) {
  if (!("speechSynthesis" in window)) return false;
  const lang = state.config.speech_lang;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang;
  const voice = speechSynthesis.getVoices().find(v => v.lang && v.lang.startsWith(lang.slice(0, 2)));
  if (voice) u.voice = voice;
  u.onstart = stopWake; // the wake word must not hear JARVIS itself
  u.onend = u.onerror = () => { if (state.mode === "standby") startWake(); };
  speechSynthesis.speak(u);
  return true;
}

export function notify(text) {
  if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
  try { new Notification("J.A.R.V.I.S.", { body: text, silent: true }); } catch { /* unsupported */ }
}

export function onTask(tk) {
  state.tasks.set(tk.id, tk);
  if (tk.status === "running" || state.announced.has(tk.id)) return;
  state.announced.add(tk.id);
  if (tk.status === "cancelled") return;
  const summary = (tk.output || "").slice(0, 4000);
  if (tk.origin === "briefing") {
    deliver({ kind: "briefing",
              text: `Briefing du matin (${tk.status}) : ${summary}\nPrésente-le à monsieur de façon vivante et concise, en 30 secondes maximum.`,
              spoken: "Bonjour monsieur. Votre briefing du matin est prêt, appelez-moi quand vous voudrez l'entendre." });
    return;
  }
  deliver({ kind: "task",
            text: `Résultat de la tâche "${tk.title}" (${tk.status}): ${summary}\nRésume oralement en une ou deux phrases. Si c'est une analyse de données (chiffres, stats, comparatifs), affiche un tableau de bord avec display_report (kpis, chart, table). Pour un simple résultat ponctuel, utilise display_card.`,
            spoken: `Monsieur, la tâche « ${tk.title} » est ${tk.status === "done" ? "terminée" : "en échec"}.` });
}

export function onReminder(r) {
  const late = r.late_minutes ? ` (en retard de ${r.late_minutes} min)` : "";
  addCard(`Rappel${late}`, r.text, "warning");
  deliver({ kind: "reminder",
            text: `Rappel programmé arrivé à échéance${late}, annonce-le à monsieur maintenant : « ${r.text} »`,
            spoken: `Monsieur, un rappel : ${r.text}` });
}

export function init() {
  bus.on("server:task", onTask);
  bus.on("server:reminder", onReminder);
  bus.on("deliver", deliver);
}
