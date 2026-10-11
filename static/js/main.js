/* Entry point (the page's only script): loads every module, starts them in a
   fixed order, and exposes window.__jarvis for the end-to-end tests, so they
   can drive the page without global variables. */
import * as core from "./core.js";
import * as strings from "./strings-fr.js";
import * as audioFx from "./audio-fx.js";
import * as orb from "./orb.js";
import * as hud from "./hud.js";
import * as keys from "./keys.js";
import * as composer from "./composer.js";
import * as panels from "./panels.js";
import * as taskview from "./taskview.js";
import * as report from "./report.js";
import * as confirm from "./confirm.js";
import * as journal from "./journal.js";
import * as settingsUi from "./settings.js";
import * as onboarding from "./onboarding.js";
import * as usage from "./usage.js";
import * as ares from "./ares.js";
import * as remarques from "./remarques.js";
import * as delivery from "./delivery.js";
import * as remoteSettings from "./remote-settings.js";
import * as notifySettings from "./notify-settings.js";
import * as siriUi from "./siri-ui.js";
import * as ios from "./ios.js";
import * as voice from "./voice.js";
import * as sse from "./sse.js";
import * as wake from "./wake.js";

// core first (it loads the server config); sse once every listener is on the
// bus; wake last, as it settles the page into standby. The iPhone modules
// (remote access, notifications, Siri, iOS) start after Réglages and delivery.
const MODULES = [core, strings, audioFx, orb, hud, keys, composer, panels, taskview, report,
                 confirm, journal, settingsUi, onboarding, usage, ares, remarques, delivery,
                 remoteSettings, notifySettings, siriUi, ios, voice, sse, wake];

window.__jarvis = { state: core.state, bus: core.bus, api: core.api, voice, settings: core.settings };

// « Quitter JARVIS » (the tray, /api/shutdown): the voice session ends now (it
// goes straight to OpenAI and would run on, billed, with every tool failing),
// the wake word lets the microphone go, and the window closes (Chrome allows
// it for the --app window; elsewhere the chip says JARVIS is closed). The
// paired iPhone keeps its page: the chip says JARVIS is closed on the PC.
core.bus.on("server:shutdown", () => {
  wake.shutDown();
  try { voice.sleep(); } catch (err) { reportError(err); }
  if (core.state.remote) {
    sse.serverClosed({ remote: true });
    return;
  }
  sse.serverClosed();
  try { window.close(); } catch { /* not this page's to close */ }
});

for (const mod of MODULES) {
  try {
    await mod.init();
  } catch (err) {
    reportError(err); // one broken module must not take the whole page down
  }
}
core.state.ready = true;
