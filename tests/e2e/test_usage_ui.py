"""Costs and the daily cap in Chromium (WP18): the top-bar chip that follows
each response.done at once, the posts to /api/usage (every 30 s, on sleep, at
once when a threshold is crossed), the warning at 80 %, the cap that stops the
wake word and asks before any other conversation while Claude tasks stay
refused, and Réglages › Coûts with its lazy chart. The server runs in this
process: tests seed its ledger (a fixed date, never the real clock) and its cap."""
import time
from datetime import date, timedelta

import pytest

import voice_helpers
from voice_helpers import emit, go_live

from jarvis import config, events, store, tasks, usage

clock_jarvis = voice_helpers.clock_jarvis  # the page whose clock the test controls (a fixture)

pytestmark = pytest.mark.e2e

TODAY = date(2026, 10, 10)
NBSP, NNBSP = " ", " "
AXE_FILE = __import__("pathlib").Path(__file__).resolve().parent / "vendor" / "axe.min.js"

# 0,4324 $ on gpt-realtime-2.1: 1000 text in + 1000 cached, 3000 audio in, 5000 audio out, 500 text out.
USAGE = {"total_tokens": 10500, "input_tokens": 5000, "output_tokens": 5500,
         "input_token_details": {"text_tokens": 2000, "audio_tokens": 3000, "image_tokens": 0,
                                 "cached_tokens": 1000,
                                 "cached_tokens_details": {"text_tokens": 1000, "audio_tokens": 0, "image_tokens": 0}},
         "output_token_details": {"text_tokens": 500, "audio_tokens": 5000}}
USAGE_USD = (1000 * 4 + 1000 * 0.40 + 3000 * 32 + 5000 * 64 + 500 * 24) / 1e6  # 0.4324


@pytest.fixture(autouse=True)
def ledger(app_server, monkeypatch):
    """An empty ledger on a fixed date, no cap, the default voice model; all put back."""
    monkeypatch.setattr(usage, "_today", lambda: TODAY)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1")

    def clear():
        for name in ("usage.json", "usage.json.bak"):
            (config.DATA_DIR / name).unlink(missing_ok=True)
    clear()
    yield
    clear()


def seed(**days):
    """seed(d0=(voice, claude), d2=(...)): today minus N days -> figures."""
    data = {}
    for key, (voice, claude) in days.items():
        day = (TODAY - timedelta(days=int(key[1:]))).isoformat()
        data[day] = {"realtime": {"usd": voice}, "claude": {"usd": claude, "tasks": 1 if claude else 0}}
    store.save("usage.json", data)


def response_done(u=USAGE):
    return {"type": "response.done", "response": {"status": "completed", "output": [], "usage": u}}


def chip(page):
    return page.text_content("#costChip button") or ""


def until(page, condition, timeout=5.0):
    end = time.time() + timeout
    while not condition():
        if time.time() > end:
            raise AssertionError("condition jamais remplie")
        page.wait_for_timeout(50)


def usage_posts(page):
    posts = []
    page.on("request", lambda r: posts.append(r.post_data_json)
            if r.method == "POST" and r.url.endswith("/api/usage") else None)
    return posts


def reload(page):
    page.reload()
    page.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")

# ---------------------------------------------------------------- acceptance


def test_response_done_moves_the_chip_within_a_second_and_posts_within_30_s(clock_jarvis):
    page = clock_jarvis
    page.wait_for_function(f"document.querySelector('#costChip button')?.textContent === \"Aujourd'hui ≈ 0,00{NBSP}$\"")
    posts = usage_posts(page)
    go_live(page)
    emit(page, response_done())
    page.wait_for_function(f"document.querySelector('#costChip button').textContent === \"Aujourd'hui ≈ 0,43{NBSP}$\"",
                           timeout=1000)
    assert posts == []  # added up in the page first
    emit(page, response_done())  # a second turn: same post
    page.clock.fast_forward(30_000)
    until(page, lambda: usage.realtime_spent_today() > 0)
    assert len(posts) == 1 and posts[0]["model"] == "gpt-realtime-2.1"
    assert posts[0]["usage"]["output_token_details"]["audio_tokens"] == 10_000
    assert posts[0]["usage"]["input_token_details"]["cached_tokens_details"]["text_tokens"] == 2000
    assert usage.realtime_spent_today() == pytest.approx(2 * USAGE_USD)
    # The server's figure replaces the page's estimate: the same amount.
    page.wait_for_function(f"document.querySelector('#costChip button').textContent === \"Aujourd'hui ≈ 0,86{NBSP}$\"")


def test_the_chip_says_the_claude_figure_is_an_estimate(jarvis):
    seed(d0=(0.30, 0.12))
    reload(jarvis)
    jarvis.wait_for_function("document.querySelector('#costChip button')")
    assert chip(jarvis) == f"Aujourd'hui ≈ 0,42{NBSP}$"
    tip = jarvis.get_attribute("#costChip button", "title")
    assert f"Voix ≈ 0,30{NBSP}$ · Claude ≈ 0,12{NBSP}$" in tip
    assert f"Claude{NNBSP}: estimation (équivalent API)" in tip
    assert jarvis.get_attribute("#costChip", "class") == "chip cost-chip"  # no cap: no warning colour
    jarvis.click("#costChip button")  # opens Réglages › Coûts
    jarvis.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']:has-text('Coûts')")


def test_a_warning_card_at_80_percent_of_the_cap_once_a_day(jarvis):
    config.DAILY_BUDGET_USD = 0.50
    seed(d0=(0.30, 0.12))  # 84 %
    reload(jarvis)
    card = jarvis.wait_for_selector("#card-usage-warn")
    text = card.inner_text()
    assert "Dépenses du jour" in text
    assert f"84{NBSP}% du plafond du jour{NNBSP}: environ 0,42{NBSP}$ sur 0,50{NBSP}$" in text
    assert "card warning" in card.get_attribute("class")
    assert chip(jarvis) == f"Aujourd'hui ≈ 0,42{NBSP}$ · 84{NBSP}% du plafond"
    assert "warn" in jarvis.get_attribute("#costChip", "class")
    assert jarvis.evaluate("__jarvis.state.config.usage_capped") is False
    jarvis.click("#card-usage-warn .x")
    reload(jarvis)
    jarvis.wait_for_function("document.querySelector('#costChip button')")
    jarvis.wait_for_timeout(300)
    assert jarvis.locator("#card-usage-warn").count() == 0  # told once today


def test_the_cap_stops_the_wake_word_and_asks_before_a_click(jarvis, app_server):
    config.DAILY_BUDGET_USD = 0.40
    seed(d0=(0.30, 0.12))
    reload(jarvis)
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    card = jarvis.wait_for_selector("#card-usage-cap")
    assert "Plafond du jour atteint" in card.inner_text()
    assert chip(jarvis) == f"Aujourd'hui ≈ 0,42{NBSP}$ · plafond atteint"
    minted = len(app_server.sessions)
    # The wake word: refused, and it says why.
    jarvis.wait_for_function("window.__rec && __rec.running && __jarvis.state.mode === 'standby'")
    jarvis.evaluate("__say('Jarvis, ouvre Spotify', true)")
    jarvis.wait_for_selector(".toast:has-text('Plafond de dépenses du jour atteint')")
    assert jarvis.evaluate("__jarvis.state.mode") == "standby" and jarvis.evaluate("window.__pcs || 0") == 0
    # The orb: a confirmation first, which says why; 'Annuler' opens nothing.
    jarvis.click("#orbBtn")
    jarvis.wait_for_selector("#capDialog[open]")
    assert jarvis.text_content("#capText") == (f"Environ 0,42{NBSP}$ dépensés aujourd'hui, pour un plafond de "
                                               f"0,40{NBSP}$. Cette conversation sera payante, elle aussi.")
    assert jarvis.evaluate("document.activeElement.value") == "cancel"  # never one Entrée away
    jarvis.click("#capDialog button[value='cancel']")
    jarvis.wait_for_selector("#capDialog", state="hidden")
    jarvis.wait_for_timeout(300)
    assert jarvis.evaluate("__jarvis.state.mode") == "standby" and jarvis.evaluate("window.__pcs || 0") == 0
    assert len(app_server.sessions) == minted
    # Escape is a no as well.
    jarvis.click("#orbBtn")
    jarvis.wait_for_selector("#capDialog[open]")
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_selector("#capDialog", state="hidden")
    assert jarvis.evaluate("window.__pcs || 0") == 0
    # 'Ouvrir quand même': the conversation opens.
    jarvis.click("#orbBtn")
    jarvis.click("#capDialog[open] button[value='ok']")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    assert len(app_server.sessions) == minted + 1


def test_the_hotkey_over_an_open_confirmation_asks_again(jarvis):
    """The global hotkey closes every dialog, then opens a conversation: that one is asked anew."""
    config.DAILY_BUDGET_USD = 0.40
    seed(d0=(0.30, 0.12))
    reload(jarvis)
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    jarvis.click("#orbBtn")
    jarvis.wait_for_selector("#capDialog[open]")
    jarvis.evaluate("__jarvis.bus.emit('server:hotkey', {type: 'hotkey', action: 'talk', at: Date.now() / 1000})")
    jarvis.wait_for_selector("#capDialog[open]")
    jarvis.wait_for_timeout(300)
    assert jarvis.evaluate("window.__pcs || 0") == 0 and jarvis.locator("#capDialog[open]").count() == 1
    jarvis.click("#capDialog[open] button[value='ok']")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    assert jarvis.evaluate("__pcs") == 1


def test_typed_text_waits_for_the_confirmation_and_claude_tasks_stay_refused(jarvis):
    config.DAILY_BUDGET_USD = 0.40
    seed(d0=(0.45, 0))  # the voice alone reached it
    reload(jarvis)
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    jarvis.fill("#askInput", "Quel temps fait-il ?")
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_selector("#capDialog[open]")
    jarvis.click("#capDialog button[value='cancel']")
    jarvis.wait_for_function("document.getElementById('askInput').value === 'Quel temps fait-il ?'")
    assert jarvis.evaluate("window.__pcs || 0") == 0 and jarvis.evaluate("__jarvis.state.pendingText") == ""
    # '/tâche …' goes straight to Claude: refused by the cap, said in French.
    jarvis.fill("#askInput", "/tâche cherche un aspirateur")
    jarvis.keyboard.press("Enter")
    jarvis.wait_for_selector(".toast:has-text('Plafond du jour atteint (0,40 $)')")
    assert not tasks.TASKS
    assert jarvis.locator("#capDialog[open]").count() == 0  # no conversation asked for


def test_crossing_the_cap_in_a_conversation_tells_the_server_at_once(clock_jarvis):
    page = clock_jarvis
    config.DAILY_BUDGET_USD = 0.40
    events.publish("config", {"keys": ["daily_budget_usd"], "restart": []})  # what Réglages does
    page.wait_for_function("document.querySelector('#costChip button')?.title.includes('Plafond du jour')")
    go_live(page)
    emit(page, response_done())  # 0,43 $: past the cap, no waiting for the 30 s
    until(page, lambda: usage.realtime_spent_today() > 0)
    assert usage.over_daily_cap() is True
    page.wait_for_function("__jarvis.state.config.usage_capped === true")
    page.wait_for_selector("#card-usage-cap")
    assert page.evaluate("__jarvis.state.mode") == "live"  # the conversation under way goes on


def test_going_to_sleep_posts_what_is_left(jarvis):
    posts = usage_posts(jarvis)
    go_live(jarvis)
    emit(jarvis, response_done())
    jarvis.click("#orbBtn")  # Veille
    jarvis.wait_for_function("__jarvis.state.mode !== 'live'")
    until(jarvis, lambda: usage.realtime_spent_today() > 0)
    assert len(posts) == 1 and usage.realtime_spent_today() == pytest.approx(USAGE_USD)


def test_transcription_usage_is_counted_too(jarvis, monkeypatch):
    from jarvis import realtime
    monkeypatch.setattr(realtime, "transcribe_model", lambda: "gpt-4o-mini-transcribe")
    posts = usage_posts(jarvis)
    go_live(jarvis)
    emit(jarvis, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
                  "transcript": "Bonjour", "usage": {"type": "tokens", "input_tokens": 200_000, "output_tokens": 20_000,
                                                     "input_token_details": {"audio_tokens": 200_000}}})
    jarvis.click("#orbBtn")
    until(jarvis, lambda: usage.realtime_spent_today() > 0)
    assert posts[0] == {"model": "", "usage": {"type": "tokens", "input_tokens": 200_000, "output_tokens": 20_000,
                                               "input_token_details": {"audio_tokens": 200_000}}}
    assert usage.realtime_spent_today() == pytest.approx((200_000 * 1.25 + 20_000 * 5) / 1e6)


def test_a_server_change_reaches_the_chip_and_the_cap(jarvis):
    jarvis.wait_for_function("document.querySelector('#costChip button')")
    usage.add_claude(0.25)  # a task ended: pushed to every page
    jarvis.wait_for_function(f"document.querySelector('#costChip button').textContent === \"Aujourd'hui ≈ 0,25{NBSP}$\"")
    config.DAILY_BUDGET_USD = 0.20
    events.publish("config", {"keys": ["daily_budget_usd"], "restart": []})  # what Réglages does
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    jarvis.wait_for_selector("#card-usage-cap")
    config.DAILY_BUDGET_USD = 5.0  # raised again: lifted at once
    events.publish("config", {"keys": ["daily_budget_usd"], "restart": []})
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === false")
    jarvis.wait_for_selector("#card-usage-cap", state="detached")
    assert chip(jarvis) == f"Aujourd'hui ≈ 0,25{NBSP}$"

# ---------------------------------------------------------------- Réglages › Coûts


def open_costs(page):
    page.click("#topActions button:has-text('Réglages')")
    page.wait_for_selector("#settingsDialog[open] .set-tab[aria-current='true']")
    page.click("#settingsDialog .set-tab:has-text('Coûts')")
    page.wait_for_selector("#settingsUsage .usage-today")


def test_reglages_couts_lazy_chart_and_table(jarvis):
    config.DAILY_BUDGET_USD = 2.0
    seed(d0=(0.30, 0.12), d1=(1.0, 0), d5=(0, 0.5))
    reload(jarvis)
    assert jarvis.locator("script[data-lazy='apexcharts']").count() == 0  # not before it is needed
    open_costs(jarvis)
    host = "#settingsUsage"
    assert jarvis.text_content(f"{host} .usage-today") == (f"Aujourd'hui ≈ 0,42{NBSP}$ · voix ≈ 0,30{NBSP}$ · "
                                                          f"Claude ≈ 0,12{NBSP}$")
    assert jarvis.text_content(f"{host} .usage-period") == (f"Sur 30 jours ≈ 1,92{NBSP}$ · en moyenne ≈ "
                                                           f"0,06{NBSP}$ par jour")
    assert jarvis.text_content(f"{host} .usage-cap") == f"Plafond du jour{NNBSP}: 2,00{NBSP}$."
    assert "Claude : estimation (équivalent API)" in jarvis.text_content(f"{host} .usage-note")
    jarvis.click(f"{host} .usage-details summary")
    rows = jarvis.eval_on_selector_all(f"{host} .usage-table tbody tr",
                                       "rows => rows.map(r => [...r.children].map(c => c.textContent))")
    assert rows == [["10 oct.", f"0,30{NBSP}$", f"0,12{NBSP}$", f"0,42{NBSP}$"],
                    ["9 oct.", f"1,00{NBSP}$", f"0,00{NBSP}$", f"1,00{NBSP}$"],
                    ["5 oct.", f"0,00{NBSP}$", f"0,50{NBSP}$", f"0,50{NBSP}$"]]
    assert jarvis.locator("script[data-lazy='apexcharts']").count() == 1
    jarvis.wait_for_function("['ready', 'unavailable'].includes(document.getElementById('usageChart').dataset.state)",
                             timeout=20_000)
    if jarvis.get_attribute("#usageChart", "data-state") != "ready":
        assert "Graphique indisponible" in jarvis.text_content("#usageChart")
        pytest.skip("ApexCharts (CDN) indisponible")
    assert jarvis.locator("#usageChart svg.apexcharts-svg").count() == 1
    assert jarvis.get_attribute("#usageChart svg.apexcharts-svg", "aria-label") == \
        "Dépenses des 30 derniers jours, voix et Claude"
    legend = jarvis.eval_on_selector_all("#usageChart .apexcharts-legend-text", "els => els.map(e => e.textContent)")
    assert legend == ["Voix (OpenAI)", "Claude (estimation)"]


def test_chart_labels_are_neutralised(jarvis):
    opts = jarvis.evaluate("""async () => (await import('/static/js/usage.js')).chartOptions(
      [{date: '<img src=x onerror=alert(1)>', realtime_usd: 1, claude_usd: 'beaucoup'},
       {date: '2026-10-09', realtime_usd: -2, claude_usd: 0.5}])""")
    assert opts["xaxis"]["categories"] == ["‹img src=x onerror=alert(1)›", "9 oct."]
    assert opts["series"][0]["data"] == [1, 0] and opts["series"][1]["data"] == [0, 0.5]
    assert all("<" not in s["name"] for s in opts["series"])


def test_offline_the_figures_stay_readable(jarvis):
    jarvis.route("**/apexcharts@*/**", lambda route: route.abort())
    seed(d0=(0.30, 0.12))
    reload(jarvis)
    open_costs(jarvis)
    jarvis.wait_for_selector("#usageChart[data-state='unavailable']", timeout=20_000)
    assert "les chiffres sont dans le tableau" in jarvis.text_content("#usageChart")
    jarvis.click("#settingsUsage .usage-details summary")
    assert jarvis.locator("#settingsUsage .usage-table tbody tr").count() == 1


def axe_violations(page):
    page.add_script_tag(path=str(AXE_FILE))
    return page.evaluate("""async () => {
      const r = await axe.run(document, {resultTypes: ['violations']});
      return r.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')
        .map(v => ({id: v.id, nodes: v.nodes.slice(0, 5).map(n => n.target.join(' '))}));
    }""")


@pytest.mark.parametrize("which", ["costs", "cap"])
def test_axe_has_no_serious_violation(jarvis, which):
    config.DAILY_BUDGET_USD = 0.40
    seed(d0=(0.30, 0.12), d1=(1.0, 0))
    reload(jarvis)
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    if which == "costs":
        open_costs(jarvis)
        jarvis.click("#settingsUsage .usage-details summary")
    else:
        jarvis.click("#orbBtn")
        jarvis.wait_for_selector("#capDialog[open]")
    assert axe_violations(jarvis) == []


@pytest.mark.parametrize("size", [(1024, 700), (360, 640)])
def test_cap_dialog_and_costs_fit_without_sideways_scroll(jarvis, size):
    config.DAILY_BUDGET_USD = 0.40
    seed(d0=(0.30, 0.12))
    jarvis.set_viewport_size({"width": size[0], "height": size[1]})
    reload(jarvis)
    jarvis.wait_for_function("__jarvis.state.config.usage_capped === true")
    jarvis.click("#orbBtn")
    box = jarvis.eval_on_selector("#capDialog", "e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }")
    assert box[0] >= 0 and box[1] <= size[0] + 0.5
    jarvis.keyboard.press("Escape")
    open_costs(jarvis)
    jarvis.click("#settingsUsage .usage-details summary")
    assert jarvis.eval_on_selector("#settingsDialog .set-panels", "e => e.scrollWidth <= e.clientWidth + 1")
