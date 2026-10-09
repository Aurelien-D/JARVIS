/* Live events pushed by the server (Server-Sent Events): each one goes on
   the bus as "server:<type>" (task, reminder, schedules, memory...). */
import { TOKEN, api, bus, state } from "./core.js";

let panelsLoaded = false;

export function listenEvents() {
  const es = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  es.onopen = refreshPanels;
  es.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    if (ev && typeof ev.type === "string") bus.emit(`server:${ev.type}`, ev);
  };
  es.onerror = () => {
    state.synced = false;
    // A restarted server has a new token, so this page's is dead: reload to get it.
    api("/api/config").catch(err => { if (err.status === 401) location.reload(); });
  };
}

/* On (re)connection: the full state, as if each item had just been pushed. */
async function refreshPanels() {
  try {
    const [allTasks, items, facts] = await Promise.all([api("/api/tasks"), api("/api/schedules"), api("/api/memory")]);
    for (const tk of allTasks.slice().reverse()) {
      // History isn't news; a task that ended while we were disconnected is.
      if (!panelsLoaded && tk.status !== "running") state.announced.add(tk.id);
      bus.emit("server:task", tk);
    }
    bus.emit("server:schedules", { items });
    bus.emit("server:memory", { facts });
    panelsLoaded = true;
    state.synced = true;
  } catch (err) {
    console.warn(err);
  }
}

export function init() {
  listenEvents();
}
