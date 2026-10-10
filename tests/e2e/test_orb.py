"""The orb and the earcons (design spec §4, §7, WP05): a distinct look per
state from a pure, time-based orbParams(), a frugal drawing loop, reduced
motion, and earcons that follow the phases and respect quiet hours."""
import json
import math

import pytest

pytestmark = pytest.mark.e2e


def orb(page, expr):
    """Evaluate `expr` with orb.js as O."""
    return page.evaluate(f"async () => {{ const O = await import('/static/js/orb.js'); return ({expr}); }}")


def fx(page, expr):
    """Evaluate `expr` with audio-fx.js as A."""
    return page.evaluate(f"async () => {{ const A = await import('/static/js/audio-fx.js'); return ({expr}); }}")


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def set_phase(page, phase, label=""):
    page.evaluate("([p, l]) => { __jarvis.state.phase = p; __jarvis.bus.emit('phase', {phase: p, label: l}); }",
                  [phase, label])


def draws_during(page, ms):
    orb(page, "O.stats.draws = 0")
    page.wait_for_timeout(ms)
    return orb(page, "O.stats.draws")


# ---------------------------------------------------------------- orbParams: pure and time-based

STATES = [["off", None], ["standby", None], ["connecting", None], ["live", "listening"], ["live", "user"],
          ["live", "thinking"], ["live", "tool"], ["live", "speaking"], ["live", "confirm"]]


def test_rotation_after_one_second_is_the_same_at_60_and_144_hz(jarvis):
    out = orb(jarvis, f"""{json.dumps(STATES)}.map(([m, p]) => {{
      let a, b;
      for (let i = 0; i <= 60; i++) a = O.orbParams(m, p, {{tasks: 2}}, i / 60, 0.3, 0.3);
      for (let i = 0; i <= 144; i++) b = O.orbParams(m, p, {{tasks: 2}}, i / 144, 0.3, 0.3);
      return [m + '-' + (p || ''), a.rotation, b.rotation, JSON.stringify(a) === JSON.stringify(b)];
    }})""")
    for name, a, b, same in out:
        assert a == b and same, name
    rot = {name: a for name, a, _, _ in out}
    assert rot["off-"] == 0 and rot["live-confirm"] == 0  # still
    assert rot["connecting-"] == pytest.approx(2 * math.pi / 1.2)  # one turn per 1.2 s
    assert rot["standby-"] == pytest.approx(rot["live-listening"] * 0.1)


def test_each_state_has_its_own_look(jarvis):
    p = orb(jarvis, f"""Object.fromEntries({json.dumps(STATES)}.map(([m, ph]) =>
      [ph || m, O.orbParams(m, ph, {{}}, 1.7, 0.5, 0.5)]))""")
    palettes = {name: str(p[name]["palette"]) for name in ["off", "standby", "listening", "thinking", "speaking"]}
    assert len(set(palettes.values())) == 5, palettes
    assert p["off"]["palette"]["ring"] == [120, 140, 160] and p["off"]["glow"] == 0 and not p["off"]["animated"]
    assert p["standby"]["palette"]["ring"] == [30, 126, 163]
    assert p["listening"]["palette"]["ring"] == [64, 220, 255]
    assert p["thinking"]["palette"]["ring"] == [255, 179, 71] and len(p["thinking"]["arcs"]) == 3
    assert p["tool"]["arcs"] and p["tool"]["sweep"] is not None and p["thinking"]["sweep"] is None
    assert p["speaking"]["palette"]["ring"] == [232, 246, 255] and p["speaking"]["waveform"]
    assert p["connecting"]["segments"] and not p["listening"]["segments"]
    assert p["user"]["fill"] > 0 and p["listening"]["fill"] == 0
    assert p["confirm"]["palette"]["ring"] == [255, 179, 71] and not p["confirm"]["arcs"] and not p["confirm"]["animated"]


def test_listening_follows_the_mic_and_speaking_the_voice_only(jarvis):
    r = orb(jarvis, """({
      micOnly: O.orbParams('live', 'listening', {}, 1, 1, 0).radius,
      voiceOnly: O.orbParams('live', 'listening', {}, 1, 0, 1).radius,
      silent: O.orbParams('live', 'listening', {}, 1, 0, 0).radius,
      waveVoice: O.orbParams('live', 'speaking', {}, 1, 0, 1).waveform.amp,
      waveMic: O.orbParams('live', 'speaking', {}, 1, 1, 0).waveform.amp,
      fillLoud: O.orbParams('live', 'user', {}, 1, 1, 0).fill,
      fillQuiet: O.orbParams('live', 'user', {}, 1, 0.1, 0).fill,
    })""")
    assert r["micOnly"] > r["silent"] == r["voiceOnly"]
    assert r["waveVoice"] > 0 and r["waveMic"] == 0
    assert r["fillLoud"] > r["fillQuiet"] > 0


def test_standby_breathes_at_a_quarter_hertz(jarvis):
    radii = orb(jarvis, "[0, 1, 2, 3, 4].map(t => O.orbParams('standby', null, {}, t).radius)")
    assert radii[1] == pytest.approx(0.2 * 1.04) and radii[3] == pytest.approx(0.2 * 0.96)  # ±4 %
    assert radii[0] == pytest.approx(0.2) and radii[4] == pytest.approx(0.2)  # period 4 s


def test_overlays(jarvis):
    o = orb(jarvis, """({
      muted: O.orbParams('live', 'listening', {muted: true}, 1),
      err: [0, 0.25, 0.5, 0.75, 1, 1.5, 1.99, 2.01].map(t => O.orbParams('standby', null, {errorAt: 10}, 10 + t).error),
      errReduced: O.orbParams('standby', null, {errorAt: 10}, 10.2, 0, 0, true).error,
      sats: [0, 1, 3, 5].map(n => O.orbParams('live', 'listening', {tasks: n}, 1).satellites.length),
      countdown: O.orbParams('live', 'listening', {countdown: 0.4}, 1).countdown,
      flash: [0.05, 0.16].map(t => O.orbParams('live', 'tool', {captureAt: 3}, 3 + t).flash),
      flashReduced: O.orbParams('live', 'tool', {captureAt: 3}, 3.05, 0, 0, true).flash,
      transient: O.orbParams('off', null, {errorAt: 0}, 0.5).transient,
    })""")
    assert o["muted"]["alpha"] == 0.5 and o["muted"]["mutedBar"]
    # the error pulse: 1 Hz, twice, then gone (never more than 3 flashes a second)
    assert [round(e, 2) for e in o["err"]] == [0, 0.5, 1, 0.5, 0, 1, 0, 0]
    assert o["errReduced"] == 1  # reduced motion: steady red, no pulse
    assert o["sats"] == [0, 1, 3, 3]
    assert o["countdown"] == 0.4
    assert o["flash"][0] > 0 and o["flash"][1] == 0 and o["flashReduced"] == 0
    assert o["transient"]  # an error keeps even the still 'off' orb drawing for 2 s


def test_reduced_motion_freezes_everything_but_brightness(jarvis):
    p = orb(jarvis, f"""{json.dumps(STATES)}.map(([m, ph]) => [
      JSON.stringify(O.orbParams(m, ph, {{tasks: 2}}, 0, 0.4, 0.4, true)),
      JSON.stringify(O.orbParams(m, ph, {{tasks: 2}}, 7.3, 0.4, 0.4, true)),
      O.orbParams(m, ph, {{}}, 7.3, 0, 0, true).glow, O.orbParams(m, ph, {{}}, 7.3, 1, 1, true).glow])""")
    for (m, ph), (at_0, at_7) in zip(STATES, [row[:2] for row in p], strict=True):
        assert at_0 == at_7, (m, ph)  # nothing depends on time
    glow = {ph or m: row[2:] for (m, ph), row in zip(STATES, p, strict=True)}  # [silent, loud]
    assert glow["listening"][1] > glow["listening"][0]  # brightness still follows the voice
    assert glow["speaking"][1] > glow["speaking"][0]
    assert orb(jarvis, "O.orbParams('live', 'speaking', {}, 1, 0, 1, true).waveform") is None


# ---------------------------------------------------------------- the loop

def test_standby_draws_at_most_20_fps_and_nothing_when_hidden(jarvis):
    n = draws_during(jarvis, 2000)
    assert 10 <= n <= 44, n
    jarvis.evaluate("""Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
      document.dispatchEvent(new Event('visibilitychange'))""")
    jarvis.wait_for_timeout(100)
    assert draws_during(jarvis, 1000) == 0
    assert not orb(jarvis, "O.stats.running")
    jarvis.evaluate("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
    assert draws_during(jarvis, 500) > 0


def test_live_draws_every_frame(jarvis):
    standby = draws_during(jarvis, 1000)
    go_live(jarvis)
    live = draws_during(jarvis, 1000)
    assert live > standby * 1.4, (standby, live)


def test_a_still_orb_is_drawn_once_then_left_alone(jarvis):
    jarvis.click("#wakeBtn")  # off: grey and still
    jarvis.wait_for_function("__jarvis.state.mode === 'off'")
    jarvis.wait_for_timeout(200)
    assert not orb(jarvis, "O.stats.running")
    assert draws_during(jarvis, 600) == 0
    # an error pulses even then, for two seconds, and stops again
    jarvis.evaluate("__jarvis.bus.emit('error', {kind: 'lost', message: 'perdu'})")
    assert draws_during(jarvis, 500) > 5
    jarvis.wait_for_function("async () => !(await import('/static/js/orb.js')).stats.running", timeout=4000)


def test_standby_without_focus_stops_after_a_while(jarvis):
    orb(jarvis, "O.tuning.unfocusedStopMs = 300")
    jarvis.evaluate("document.hasFocus = () => false")
    jarvis.wait_for_timeout(900)
    assert not orb(jarvis, "O.stats.running")
    assert draws_during(jarvis, 400) == 0
    jarvis.evaluate("delete document.hasFocus; window.dispatchEvent(new Event('focus'))")
    assert draws_during(jarvis, 400) > 0
    orb(jarvis, "O.tuning.unfocusedStopMs = 60000")


SNAPSHOT = "document.getElementById('orb').toDataURL()"


def test_reduced_motion_standby_is_a_still_image(jarvis):
    a = jarvis.evaluate(SNAPSHOT)
    jarvis.wait_for_timeout(400)
    assert jarvis.evaluate(SNAPSHOT) != a  # it breathes normally
    jarvis.emulate_media(reduced_motion="reduce")
    jarvis.wait_for_timeout(300)
    a = jarvis.evaluate(SNAPSHOT)
    jarvis.wait_for_timeout(400)
    assert jarvis.evaluate(SNAPSHOT) == a
    # live, it stays at 10 frames a second at most
    go_live(jarvis)
    assert draws_during(jarvis, 1000) <= 12


def test_no_css_filter_on_the_orb(jarvis):
    assert jarvis.evaluate("getComputedStyle(document.getElementById('orb')).filter") == "none"
    assert jarvis.evaluate("getComputedStyle(document.getElementById('orbBtn')).filter") == "none"


def test_canvas_matches_the_screen_pixels(jarvis):
    size = "(() => { const c = document.getElementById('orb'), r = c.getBoundingClientRect();" \
           " return [c.width, c.height, r.width * devicePixelRatio, r.height * devicePixelRatio]; })()"
    w, h, cw, ch = jarvis.evaluate(size)
    assert abs(w - cw) <= 1 and abs(h - ch) <= 1
    jarvis.set_viewport_size({"width": 1024, "height": 700})
    jarvis.wait_for_function(f"(([w, h, cw, ch]) => Math.abs(w - cw) <= 1 && Math.abs(h - ch) <= 1 && w !== {w})({size})")


def test_level_reuses_its_buffers(jarvis):
    out = orb(jarvis, """(() => {
      const seen = new Set();
      const fake = {frequencyBinCount: 128, getByteFrequencyData(buf) { seen.add(buf); buf.fill(255); }};
      const a = O.level(fake, 0), b = O.level(fake, 0), c = O.level(fake, 1);
      return [a, b, c, seen.size, O.level(null)];
    })()""")
    assert out == [1, 1, 1, 2, 0]  # two preallocated buffers, no new array per frame


# ---------------------------------------------------------------- the state on <body>

def test_body_state_follows_the_phase(jarvis):
    go_live(jarvis)
    for phase in ["user", "thinking", "speaking", "listening", "tool", "confirm"]:
        set_phase(jarvis, phase)
        assert jarvis.evaluate("document.body.dataset.state") == f"live-{phase}"
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("document.body.dataset.state === 'standby-idle'")


def test_body_state_follows_the_realtime_events(jarvis):
    """End to end with voice.js's phase machine (WP02)."""
    go_live(jarvis)
    jarvis.evaluate("window.__phases = []; __jarvis.bus.on('phase', d => __phases.push(d.phase)); 0")
    jarvis.evaluate("__emit({type: 'input_audio_buffer.speech_started'}); __emit({type: 'input_audio_buffer.speech_stopped'})")
    if not jarvis.evaluate("__phases.length"):
        pytest.skip("voice.js n'émet pas encore les phases (WP02)")
    assert jarvis.evaluate("document.body.dataset.state") == "live-thinking"
    jarvis.evaluate("__emit({type: 'response.created'}); __emit({type: 'output_audio_buffer.started'})")
    assert jarvis.evaluate("document.body.dataset.state") == "live-speaking"


# ---------------------------------------------------------------- earcons

# Every note as it starts: frequency, wave type, start and stop times.
NOTES = """(() => {
  window.__notes = [];
  for (const [name, kind] of [['createOscillator', 'tone'], ['createBufferSource', 'noise']]) {
    const create = AudioContext.prototype[name];
    AudioContext.prototype[name] = function () {
      const node = create.call(this), start = node.start.bind(node), stop = node.stop.bind(node);
      const rec = {kind};
      node.start = (at) => { Object.assign(rec, {f: node.frequency ? node.frequency.value : 0, type: node.type || '', at});
                             __notes.push(rec); return start(at); };
      node.stop = (at) => { rec.end = at; return stop(at); };
      return node;
    };
  }
})()"""

QUIET_NOW = """(() => { const pad = n => String(n).padStart(2, '0'), now = Date.now();
  const a = new Date(now - 3600e3), b = new Date(now + 3600e3);
  __jarvis.state.config.quiet_hours = `${pad(a.getHours())}:${pad(a.getMinutes())}-${pad(b.getHours())}:${pad(b.getMinutes())}`; })()"""


def test_quiet_hours_silence_the_earcons(jarvis):
    jarvis.evaluate(NOTES)
    jarvis.evaluate(QUIET_NOW)
    assert jarvis.evaluate("__jarvis.state.mode") == "standby"
    assert fx(jarvis, "A.earcon('alert')") is False
    assert jarvis.evaluate("__notes.length") == 0  # no oscillator started
    # what monsieur just did still answers
    assert fx(jarvis, "A.earcon('alert', {userInitiated: true})") is True
    assert jarvis.evaluate("__notes.length") == 2
    # Do Not Disturb, from the setting or the state overlay
    jarvis.evaluate("__notes.length = 0; __jarvis.state.config.quiet_hours = ''; __jarvis.settings.set('dndUntil', Date.now() + 3600e3)")
    assert fx(jarvis, "A.earcon('alert')") is False
    jarvis.evaluate("__jarvis.settings.set('dndUntil', 0); __jarvis.state.quiet = true")
    assert fx(jarvis, "A.earcon('alert')") is False
    jarvis.evaluate("__jarvis.state.quiet = false")
    assert fx(jarvis, "A.earcon('alert')") is True
    # volume 0: nothing at all
    jarvis.evaluate("__notes.length = 0; __jarvis.settings.set('earconVolume', 0)")
    assert fx(jarvis, "A.earcon('online', {userInitiated: true})") is False
    assert jarvis.evaluate("__notes.length") == 0
    jarvis.evaluate("__jarvis.settings.set('earconVolume', 1)")


def test_quiet_hours_parsing(jarvis):
    out = fx(jarvis, """[['22:30-07:30', 23, 0], ['22:30-07:30', 7, 29], ['22:30-07:30', 7, 30],
      ['22:30-07:30', 12, 0], ['22h-7h', 2, 0], ['9-17', 12, 0], ['9-17', 18, 0], ['', 23, 0],
      ['n\\'importe', 23, 0], ['8:00-8:00', 8, 0]]
      .map(([spec, h, m]) => A.inQuietHours(spec, new Date(2026, 9, 9, h, m)))""")
    assert out == [True, True, False, False, True, True, False, False, False, False]


def test_earcons_are_distinct_harmonic_and_spaced(jarvis):
    jarvis.evaluate(NOTES)
    kinds = fx(jarvis, "Object.keys(A.EARCONS)")
    assert {"wake", "online", "sleep", "alert", "error", "working", "mute", "capture"} <= set(kinds)
    shapes = {}
    for kind in kinds:
        jarvis.evaluate("__notes.length = 0")
        assert fx(jarvis, f"A.earcon('{kind}', {{userInitiated: true}})") is True
        notes = jarvis.evaluate("__notes")
        assert notes, kind
        for n in notes:
            assert n["end"] - n["at"] >= 0.08, kind  # notes of at least 80 ms
            if n["kind"] == "tone":
                assert n["type"] == "custom", kind  # fundamental + 2nd + 3rd harmonics
                assert 125 <= n["f"] <= 5000, kind
        for a, b in zip(notes, notes[1:], strict=False):
            assert b["at"] - (a["end"] - 0.01) == pytest.approx(0.1, abs=1e-6), kind  # 100 ms gaps
        shapes[kind] = tuple((n["kind"], round(n["end"] - n["at"], 3), n["f"]) for n in notes)
    assert len(set(shapes.values())) == len(shapes)  # each earcon sounds different


def test_earcons_follow_the_phases(jarvis):
    jarvis.evaluate(NOTES)
    go_live(jarvis)
    tones = fx(jarvis, "Object.fromEntries(Object.entries(A.EARCONS).map(([k, n]) => [k, n.map(x => x.f)]))")

    def heard():
        out = jarvis.evaluate("__notes.map(n => n.f)")
        jarvis.evaluate("__notes.length = 0")
        return out
    heard()
    set_phase(jarvis, "user")
    set_phase(jarvis, "thinking")  # speech_stopped: a soft tick
    assert heard() == tones["tick"]
    set_phase(jarvis, "confirm")
    assert heard() == tones["alert"]
    set_phase(jarvis, "tool", "Je fais le point…")
    assert heard() == []  # nothing for a tool shorter than a second
    jarvis.wait_for_timeout(1150)
    assert heard() == tones["working"]
    set_phase(jarvis, "speaking")
    jarvis.wait_for_timeout(1100)
    assert heard() == []  # the ticks stop with the tool
    jarvis.keyboard.press("Control+m")
    assert heard() == tones["mute"]
    jarvis.keyboard.press("Control+m")
    assert heard() == tones["unmute"]
    jarvis.evaluate("__jarvis.bus.emit('tool:start', {name: 'look_at_screen', callId: 'c1', label: ''})")
    assert jarvis.evaluate("__notes.map(n => n.kind)") == ["noise", "noise"]  # the shutter


def test_the_mic_is_muted_while_an_earcon_plays_outside_a_session(jarvis):
    jarvis.evaluate("""async () => {
      const A = await import('/static/js/audio-fx.js');
      window.__track = {enabled: true, readyState: 'live', addEventListener() {}};
      A.registerMic(__track);
      A.earcon('online', {userInitiated: true});
    }""")
    assert jarvis.evaluate("__track.enabled") is False
    jarvis.wait_for_function("__track.enabled === true", timeout=3000)
    # during a conversation the echo canceller handles it: no muting
    go_live(jarvis)
    fx(jarvis, "A.earcon('alert')")
    assert jarvis.evaluate("__track.enabled") is True
