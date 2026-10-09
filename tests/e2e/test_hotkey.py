"""The global hotkey and the tray icon (WP13) in a real browser. The server
side (RegisterHotKey, pystray) is faked in tests/test_desktop.py; here the
server's 'hotkey' event reaches real pages:
- 'toggle' talks or goes back to standby, like the orb; 'talk' only starts;
  'wake' switches the wake word;
- only the page that speaks acts, and an old event replayed after a
  reconnection never opens the microphone;
- a hotkey Windows refuses is explained on screen, in French."""
import time

import pytest

from fakes import FAKE_RTC, FAKE_SR

pytestmark = pytest.mark.e2e

AWAKE = "['live', 'connecting'].includes(__jarvis.state.mode)"
ASLEEP = "['standby', 'off'].includes(__jarvis.state.mode)"
CLIENT_ID = "async () => (await import('/static/js/sse.js')).clientId()"
IS_LEADER = "async () => (await import('/static/js/delivery.js')).isLeader()"


def hotkey(action="toggle", age=0.0):
    from jarvis import events
    events.publish("hotkey", {"action": action, "at": time.time() - age})


def count_hotkeys(page):
    """Record 'server:hotkey' after keys.js has handled it (bus order)."""
    page.evaluate("window.__hk = []; __jarvis.bus.on('server:hotkey', e => __hk.push(e)); 0")


def test_hotkey_talks_then_goes_back_to_standby(jarvis):
    jarvis.wait_for_function(ASLEEP)
    hotkey()
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    hotkey()
    jarvis.wait_for_function(ASLEEP)


def test_tray_talk_only_starts(jarvis):
    jarvis.wait_for_function(ASLEEP)
    hotkey("talk")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    count_hotkeys(jarvis)
    hotkey("talk")  # already talking: stays in the conversation
    jarvis.wait_for_function("__hk.length === 1")
    assert jarvis.evaluate("__jarvis.state.mode") == "live"


def test_old_replayed_hotkey_does_nothing(jarvis):
    jarvis.wait_for_function(ASLEEP)
    count_hotkeys(jarvis)
    hotkey(age=60)  # pressed a minute ago: a stream that resumes must not open the mic
    hotkey("talk", age=-60)  # and nothing from the future either
    jarvis.wait_for_function("__hk.length === 2")
    assert jarvis.evaluate(ASLEEP)
    assert not jarvis.evaluate("__jarvis.state.wantLive")


def test_tray_switches_the_wake_word(jarvis):
    jarvis.wait_for_function(ASLEEP)
    before = jarvis.evaluate("__jarvis.settings.get('wake', __jarvis.state.config.wake_word)")
    hotkey("wake")
    jarvis.wait_for_function("w => __jarvis.settings.get('wake', true) === !w", arg=before)
    label = "Mot d'éveil : désactivé" if before else "Mot d'éveil : activé"
    jarvis.wait_for_selector(f"#toasts .toast:has-text(\"{label}\")")
    hotkey("wake")
    jarvis.wait_for_function("w => __jarvis.settings.get('wake', true) === w", arg=before)
    assert jarvis.evaluate(ASLEEP)


def test_only_the_page_that_speaks_acts(jarvis, context, app_server):
    from jarvis import events
    other = context.new_page()
    other.add_init_script(FAKE_RTC + FAKE_SR)
    other.goto(app_server.url)
    other.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")
    pages = {jarvis.evaluate(CLIENT_ID): jarvis, other.evaluate(CLIENT_ID): other}
    # Both pages know which one speaks before the key is pressed.
    deadline = time.time() + 10
    while time.time() < deadline:
        leader_id = events.leader()
        if leader_id in pages and all(p.evaluate(IS_LEADER) == (cid == leader_id) for cid, p in pages.items()):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("aucune page désignée")
    leader = pages[leader_id]
    follower = other if leader is jarvis else jarvis
    count_hotkeys(follower)
    hotkey()
    leader.wait_for_function("__jarvis.state.mode === 'live'")
    follower.wait_for_function("__hk.length === 1")
    assert follower.evaluate(ASLEEP)
    other.close()


def test_hotkey_taken_by_another_program_is_explained(jarvis, monkeypatch):
    from jarvis import shell

    class TakenApi:
        """RegisterHotKey refuses: another program holds Ctrl+Alt+Maj+J."""
        def __init__(self):
            self.quit = False

        def PeekMessageW(self, *a):
            return 0

        def GetCurrentThreadId(self):
            return 1

        def RegisterHotKey(self, *a):
            return 0

        def UnregisterHotKey(self, *a):
            return 1

        def get_last_error(self):
            return shell.ERROR_HOTKEY_ALREADY_REGISTERED

        def GetMessageW(self, *a):
            return 0  # WM_QUIT straight away: the thread ends

        def PostThreadMessageW(self, *a):
            return 1

    hk = shell.Hotkey(TakenApi(), shell.parse_hotkey("ctrl+alt+shift+j"))
    hk.start()
    hk.stop()
    card = jarvis.wait_for_selector(".card.warning:has-text('Raccourci Ctrl+Alt+Maj+J indisponible')")
    text = card.inner_text()
    assert "déjà utilisé" in text and "Réglages › Système" in text
