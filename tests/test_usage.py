"""Costs and the daily cap (WP18): the per-day ledger in data/usage.json, the
OpenAI prices it applies, the Claude estimate, the routes, the cap as
/api/config and the task API see it."""
import json
import math
import sys
import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from test_tasks import FAKE_CLAUDE, wait

import server
from jarvis import config, events, realtime, security, settings, store, tasks, usage

AUTH = {"X-Jarvis-Token": security.TOKEN}
TODAY = date(2026, 10, 10)

# The response.done usage of OpenAI's own documentation (realtime server events).
DOC_USAGE = {"total_tokens": 253, "input_tokens": 132, "output_tokens": 121,
             "input_token_details": {"text_tokens": 119, "audio_tokens": 13, "image_tokens": 0,
                                     "cached_tokens": 64,
                                     "cached_tokens_details": {"text_tokens": 64, "audio_tokens": 0,
                                                               "image_tokens": 0}},
             "output_token_details": {"text_tokens": 30, "audio_tokens": 91}}


def audio_out(n):
    return {"output_tokens": n, "output_token_details": {"audio_tokens": n, "text_tokens": 0}}


@pytest.fixture(autouse=True)
def frozen_day(monkeypatch):
    """A fixed local date (never the real clock), no cap, and no push to pages."""
    day = {"today": TODAY}
    monkeypatch.setattr(usage, "_today", lambda: day["today"])
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1")
    return day


@pytest.fixture
def pushed(monkeypatch):
    seen = []
    monkeypatch.setattr(events, "publish", lambda kind, data: seen.append({"type": kind, **data}))
    return seen


@pytest.fixture
def client():
    return TestClient(server.app, base_url="http://127.0.0.1:8788", headers=AUTH)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MODELS", {"simple": "haiku", "normale": "sonnet", "complexe": "opus"})
    monkeypatch.setattr(config, "PERMISSION_MODE", "auto")
    monkeypatch.setattr(config, "SAFE_MODE", "auto")
    monkeypatch.setattr(config, "RESTRICTED", "auto")
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "MCP_CONFIG", "")
    yield script
    for task in tasks.running():
        tasks.cancel(task["id"])


# ---------------------------------------------------------------- prices (acceptance)

def test_a_million_audio_output_tokens_on_gpt_realtime_2_1_add_64_usd(client):
    r = client.post("/api/usage", json={"usage": audio_out(1_000_000), "model": "gpt-realtime-2.1"})
    assert r.status_code == 200
    assert r.json()["today"]["realtime_usd"] == 64.0
    assert usage.realtime_spent_today() == 64.0
    day = store.load("usage.json", {})[TODAY.isoformat()]
    assert day["realtime"]["audio_out"] == 1_000_000 and day["realtime"]["usd"] == 64.0


def test_the_verified_price_table():
    p = usage.PRICES
    for model in ("gpt-realtime", "gpt-realtime-2.1"):
        assert (p[model]["audio_in"], p[model]["audio_cached"], p[model]["audio_out"]) == (32.0, 0.40, 64.0)
        assert (p[model]["text_in"], p[model]["text_cached"], p[model]["image_in"]) == (4.0, 0.40, 5.0)
    assert p["gpt-realtime"]["text_out"] == 16.0 and p["gpt-realtime-2.1"]["text_out"] == 24.0
    mini = p["gpt-realtime-2.1-mini"]
    assert (mini["audio_in"], mini["audio_cached"], mini["audio_out"]) == (10.0, 0.30, 20.0)
    assert (mini["text_in"], mini["text_cached"], mini["text_out"], mini["image_in"]) == (0.6, 0.06, 2.4, 0.8)
    assert usage.TRANSCRIBE_PER_MINUTE["gpt-4o-mini-transcribe"] == 0.003
    assert usage.TRANSCRIBE_PER_MINUTE["whisper-1"] == 0.006


def test_cached_input_is_a_subset_billed_at_the_cached_rate():
    classes = usage.realtime_classes(DOC_USAGE)
    assert classes == {"text_in": 55, "text_cached": 64, "audio_in": 13, "audio_cached": 0, "image_in": 0,
                       "image_cached": 0, "text_out": 30, "audio_out": 91}
    expected = (55 * 4 + 64 * 0.40 + 13 * 32 + 30 * 24 + 91 * 64) / 1e6
    assert math.isclose(usage.realtime_cost(classes, "gpt-realtime-2.1"), expected)
    assert math.isclose(usage.add_realtime(DOC_USAGE, "gpt-realtime-2.1"), expected)


def test_tokens_the_details_leave_out_count_as_text():
    # gpt-realtime-2.x reasoning: output tokens beyond the text and audio details.
    classes = usage.realtime_classes({"input_tokens": 10, "output_tokens": 50,
                                      "output_token_details": {"text_tokens": 5, "audio_tokens": 20}})
    assert classes["text_out"] == 30 and classes["audio_out"] == 20 and classes["text_in"] == 10


def test_models_snapshots_and_unknown_models():
    assert usage.resolve_model("gpt-realtime-2.1-mini") == "gpt-realtime-2.1-mini"
    assert usage.resolve_model("gpt-realtime-mini-2025-10-06") == "gpt-realtime-mini"
    assert usage.resolve_model("gpt-realtime-2025-08-28") == "gpt-realtime"
    assert usage.resolve_model("gpt-realtime-2.1") == "gpt-realtime-2.1"
    # Unknown or hostile names are priced as the dearest model, never as free.
    for name in ("", "gpt-live-1", "<img src=x>", "gpt-realtime-2.1x", "gpt-realtime-3", "x-2025-08-28"):
        assert usage.resolve_model(name) == "gpt-realtime-2.1"
    assert usage.add_realtime(audio_out(1_000_000), "gpt-realtime-2.1-mini") == 20.0
    assert usage.add_realtime(audio_out(1_000_000), "un-modèle-inconnu") == 64.0


def test_an_empty_model_is_the_configured_one(monkeypatch):
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1-mini")
    assert usage.add_realtime({"output_tokens": 1_000_000, "output_token_details": {"text_tokens": 1_000_000}}) == 2.4


def test_transcription_by_the_minute_and_by_the_token(client, monkeypatch):
    assert usage.add_transcription({"type": "duration", "seconds": 120}, "whisper-1") == pytest.approx(0.012)
    # The page sends no model: the server knows which one the session uses.
    monkeypatch.setattr(realtime, "transcribe_model", lambda: "gpt-4o-mini-transcribe")
    r = client.post("/api/usage", json={"model": "", "usage": {
        "type": "tokens", "input_tokens": 1_000_000, "output_tokens": 100_000,
        "input_token_details": {"audio_tokens": 1_000_000, "text_tokens": 0}}})
    assert r.status_code == 200
    day = store.load("usage.json", {})[TODAY.isoformat()]["realtime"]
    assert day["transcribe_seconds"] == 120 and day["transcribe_in"] == 1_000_000
    assert day["usd"] == pytest.approx(0.012 + 1.25 + 0.5)
    # A model priced per minute only: its audio tokens say how long monsieur spoke.
    assert usage.add_transcription({"type": "tokens", "input_tokens": 600,
                                    "input_token_details": {"audio_tokens": 600}},
                                   "gpt-transcribe") == pytest.approx(0.0045)


@pytest.mark.parametrize("bad", [
    {"output_tokens": -5},
    {"output_tokens": True},
    {"output_tokens": "1000"},
    {"output_tokens": 10**12},
    {"input_token_details": "beaucoup"},
    {"input_token_details": {"audio_tokens": float("inf")}},
    {"type": "duration", "seconds": 10**9},
    {"type": "inconnu"},
])
def test_a_bad_report_is_refused_and_counts_nothing(client, bad):
    body = json.dumps({"usage": bad, "model": "gpt-realtime-2.1"}, allow_nan=True)
    r = client.post("/api/usage", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)
    assert usage.spent_today() == 0
    assert not (config.DATA_DIR / "usage.json").exists()


def test_post_needs_a_usage_object(client):
    assert client.post("/api/usage", json={"model": "gpt-realtime-2.1"}).status_code == 422
    assert client.post("/api/usage", json={"usage": [1, 2], "model": "x"}).status_code == 422

# ---------------------------------------------------------------- Claude


def test_claude_costs_add_up_with_their_task_count():
    usage.add_claude(0.25)
    usage.add_claude(0.10)
    usage.add_claude(0)  # nothing reported: nothing counted
    assert usage.claude_spent_today() == pytest.approx(0.35)
    assert store.load("usage.json", {})[TODAY.isoformat()]["claude"] == {"usd": 0.35, "tasks": 2}
    with pytest.raises(ValueError):
        usage.add_claude(float("nan"))
    with pytest.raises(ValueError):
        usage.add_claude(-1)


def test_a_finished_task_counts_its_cost_for_real(fake_claude, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    wait(tasks.create_task("X", "x"))  # the fake claude reports total_cost_usd 0.01
    assert usage.claude_spent_today() == pytest.approx(0.01)
    assert store.load("usage.json", {})[TODAY.isoformat()]["claude"]["tasks"] == 1


def test_concurrent_additions_are_never_lost():
    threads = [threading.Thread(target=usage.add_claude, args=(0.01,)) for _ in range(20)]
    threads += [threading.Thread(target=usage.add_realtime, args=(audio_out(1000), "gpt-realtime-2.1"))
                for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert usage.claude_spent_today() == pytest.approx(0.20)
    assert usage.realtime_spent_today() == pytest.approx(20 * 1000 * 64 / 1e6)

# ---------------------------------------------------------------- the cap (acceptance)


def test_over_daily_cap_counts_voice_and_claude_together(monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    assert usage.over_daily_cap() is False
    usage.add_realtime(audio_out(10_000), "gpt-realtime-2.1")  # 0.64 $
    assert usage.over_daily_cap() is False
    usage.add_claude(0.30)  # 0.94 $
    assert usage.over_daily_cap() is False
    usage.add_claude(0.06)  # 1.00 $: reached
    assert usage.over_daily_cap() is True


def test_no_cap_when_the_budget_is_zero_whatever_was_spent(monkeypatch):
    usage.add_realtime(audio_out(10_000_000), "gpt-realtime-2.1")
    usage.add_claude(500)
    for cap in (0, 0.0, -3, float("nan"), float("inf"), None, "5"):
        monkeypatch.setattr(config, "DAILY_BUDGET_USD", cap)
        assert usage.over_daily_cap() is False, cap


def test_yesterday_never_counts_today(frozen_day, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    usage.add_claude(5.0)
    assert usage.over_daily_cap() is True
    frozen_day["today"] = TODAY + timedelta(days=1)
    assert usage.spent_today() == 0 and usage.over_daily_cap() is False


def test_config_says_usage_capped_at_100_percent(client, monkeypatch):
    monkeypatch.setattr(settings, "versions", lambda: {})
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 2.0)
    usage.add_claude(1.5)
    assert client.get("/api/config").json()["usage_capped"] is False
    client.post("/api/usage", json={"usage": audio_out(10_000), "model": "gpt-realtime-2.1"})  # +0.64 $
    assert client.get("/api/config").json()["usage_capped"] is True
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    assert client.get("/api/config").json()["usage_capped"] is False


def test_the_voice_alone_reaching_the_cap_stops_new_tasks(client, fake_claude, monkeypatch):
    """One cap for voice and tasks (Réglages › Coûts): the keyboard's /tâche too."""
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    usage.add_realtime(audio_out(20_000), "gpt-realtime-2.1")  # 1.28 $ of voice, no Claude at all
    assert usage.claude_spent_today() == 0
    with pytest.raises(ValueError, match="Plafond du jour atteint"):
        tasks.create_task("X", "x")
    r = client.post("/api/tasks", json={"prompt": "x", "profile": "lecture"})
    assert r.status_code == 400 and "Plafond du jour atteint (1,00 $)" in r.json()["detail"]
    assert not tasks.TASKS
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)  # raised in Réglages: tasks start again
    task = tasks.create_task("X", "x")
    assert task["status"] in tasks.ACTIVE
    wait(task)  # finished here, not during a later test (its events would land there)

# ---------------------------------------------------------------- what the page reads


def test_get_usage_gives_the_last_days_oldest_first(client, frozen_day, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 3.0)
    frozen_day["today"] = TODAY - timedelta(days=2)
    usage.add_claude(1.0)
    frozen_day["today"] = TODAY
    usage.add_realtime(audio_out(5_000), "gpt-realtime-2.1")  # 0.32 $
    body = client.get("/api/usage?days=30").json()
    assert len(body["days"]) == 30
    assert body["days"][-1]["date"] == TODAY.isoformat()
    assert body["days"][0]["date"] == (TODAY - timedelta(days=29)).isoformat()
    assert body["days"][-3] == {"date": (TODAY - timedelta(days=2)).isoformat(), "realtime_usd": 0,
                                "claude_usd": 1.0, "claude_tasks": 1, "total_usd": 1.0}
    assert body["today"] == body["days"][-1] and body["today"]["realtime_usd"] == 0.32
    assert body["budget"] == 3.0 and body["capped"] is False and body["warn_ratio"] == 0.8
    assert body["note"] == "Claude : estimation (équivalent API)"
    assert body["prices"]["gpt-realtime-2.1"]["audio_out"] == 64.0 and body["model"] == "gpt-realtime-2.1"
    assert client.get("/api/usage").json()["days"][-1]["date"] == TODAY.isoformat()
    for days in (0, 91, -1, "x"):
        assert client.get(f"/api/usage?days={days}").status_code == 422


def test_each_change_is_pushed_to_the_pages(pushed, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    usage.add_claude(0.4)
    usage.add_realtime(audio_out(10_000), "gpt-realtime-2.1")
    pushed[:] = [p for p in pushed if p["type"] == "usage"]  # only this test's own pushes
    assert len(pushed) == 2
    assert pushed[0]["today"]["total_usd"] == 0.4 and pushed[0]["capped"] is False
    assert pushed[1]["today"]["total_usd"] == pytest.approx(1.04) and pushed[1]["capped"] is True
    assert pushed[1]["budget"] == 1.0


def test_only_90_days_are_kept(frozen_day):
    for back in (120, 90, 89, 0):
        frozen_day["today"] = TODAY - timedelta(days=back)
        usage.add_claude(1.0)
    frozen_day["today"] = TODAY
    data = store.load("usage.json", {})
    store.save("usage.json", {**data, "nimporte": {"claude": {"usd": 1}}, "9999-99-99x": {}})
    usage.add_claude(1.0)
    kept = sorted(store.load("usage.json", {}))
    assert kept == [(TODAY - timedelta(days=89)).isoformat(), TODAY.isoformat()]


def test_a_hand_edited_ledger_never_breaks_the_counts():
    store.save("usage.json", {TODAY.isoformat(): {"realtime": {"usd": "beaucoup", "audio_out": None},
                                                   "claude": "rien"}, "nimporte": 3})
    assert usage.spent_today() == 0
    usage.add_claude(0.5)
    assert usage.claude_spent_today() == 0.5 and usage.realtime_spent_today() == 0
    usage.add_claude(0.5)
    (config.DATA_DIR / "usage.json").write_text("{pas du json", encoding="utf-8")
    assert usage.spent_today() == 0.5  # the .bak copy (the save before) is restored


def test_the_ledger_lives_in_the_data_folder_under_a_fixed_name():
    usage.add_claude(0.1)
    assert (config.DATA_DIR / "usage.json").is_file()
    assert usage.FILE == "usage.json"

# ---------------------------------------------------------------- a paired iPhone's reports (spec 4.8)

PHONE_ID = "d_0123456789abcdef"


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_760_000_000.0}
    monkeypatch.setattr(usage, "_now", lambda: now["t"])
    return now


@pytest.fixture
def phone(monkeypatch):
    """POST /api/usage as a paired iPhone (its caller stamped by the gate)."""
    from remote_helpers import as_caller, remote_client

    from jarvis import remote
    as_caller(monkeypatch, remote.Caller(kind="app", device_id=PHONE_ID, ip="100.101.102.103",
                                         login="monsieur@example.com", name="iPhone de test"))
    return remote_client()


def test_remote_mints_are_kept_in_the_day_entry(clock):
    assert usage.remote_mints(PHONE_ID) == []
    usage.note_remote_mint(PHONE_ID, clock["t"])
    usage.note_remote_mint(PHONE_ID, clock["t"] + 60)
    usage.note_remote_mint("../evil", clock["t"])  # not a device id: ignored
    assert usage.remote_mints(PHONE_ID) == [clock["t"], clock["t"] + 60]
    stored = store.load(usage.FILE, {})[TODAY.isoformat()]
    assert stored["remote"] == {PHONE_ID: {"usd": 0.0, "mints": [clock["t"], clock["t"] + 60]}}
    for _ in range(120):
        usage.note_remote_mint(PHONE_ID, clock["t"])
    assert len(usage.remote_mints(PHONE_ID)) == usage.REMOTE_MAX_MINTS


def test_the_remote_part_of_a_day_is_sanitised():
    many = {f"d_{n:016x}": {"usd": 1.0, "mints": [1.0]} for n in range(15)}
    store.save(usage.FILE, {TODAY.isoformat(): {"remote": {
        **many, "../evil": {"usd": 9}, PHONE_ID: {"usd": float("nan"), "mints": [1.0, "x", True, -5, None, 2.0]},
        "d_ffffffffffffffff": "rien"}}})
    entry = usage._entry(store.load(usage.FILE, {})[TODAY.isoformat()])
    assert len(entry["remote"]) == usage.REMOTE_MAX_DEVICES
    assert all(usage._DEVICE_ID.fullmatch(k) for k in entry["remote"])
    store.save(usage.FILE, {TODAY.isoformat(): {"remote": {PHONE_ID: {"usd": float("inf"), "mints": [1.0, "x", 2.0]}}}})
    entry = usage._entry(store.load(usage.FILE, {})[TODAY.isoformat()])
    assert entry["remote"] == {PHONE_ID: {"usd": 0.0, "mints": [1.0, 2.0]}}
    # A day with nothing remote keeps the shape the PC always had.
    assert set(usage._entry({})) == {"realtime", "claude"}


def test_remote_reports_are_bounded_by_the_time_since_each_session(phone, clock):
    one_dollar = audio_out(15_000)  # 0.96 $ at gpt-realtime-2.1
    # No voice session opened today: nothing to report, nothing recorded.
    assert phone.post("/api/usage", json={"usage": one_dollar, "model": "gpt-realtime-2.1"}).status_code == 200
    assert usage.realtime_spent_today() == 0
    usage.note_remote_mint(PHONE_ID, clock["t"])
    clock["t"] += 30  # 0.15 $ of room
    phone.post("/api/usage", json={"usage": one_dollar, "model": "gpt-realtime-2.1"})
    entry = usage._day(TODAY.isoformat())
    assert entry["realtime"]["usd"] == pytest.approx(0.15)
    # Clamped: the token classes are scaled alike, so the ledger stays whole.
    assert entry["realtime"]["audio_out"] == pytest.approx(15_000 * 0.15 / 0.96)
    assert entry["remote"][PHONE_ID]["usd"] == pytest.approx(0.15)
    # Each session's allowance stops at 6 $; two sessions, twice that.
    clock["t"] += 3600
    for _ in range(10):
        phone.post("/api/usage", json={"usage": one_dollar, "model": "gpt-realtime-2.1"})
    assert usage.realtime_spent_today() == pytest.approx(6.0)
    usage.note_remote_mint(PHONE_ID, clock["t"])
    clock["t"] += 10
    phone.post("/api/usage", json={"usage": one_dollar, "model": "gpt-realtime-2.1"})
    assert usage.realtime_spent_today() == pytest.approx(6.05)


def test_a_remote_post_above_one_dollar_is_refused(phone, clock):
    usage.note_remote_mint(PHONE_ID, clock["t"] - 3600)
    r = phone.post("/api/usage", json={"usage": audio_out(20_000), "model": "gpt-realtime-2.1"})
    assert r.status_code == 400 and r.json()["detail"] == "Relevé de consommation invalide."
    # Priced like the PC's: a cheaper model name never lowers it.
    r = phone.post("/api/usage", json={"usage": audio_out(20_000), "model": "gpt-realtime-mini"})
    assert r.status_code == 400
    r = phone.post("/api/usage", json={"usage": {"type": "duration", "seconds": 60}, "model": "whisper-1"})
    assert r.status_code == 200 and usage.realtime_spent_today() == pytest.approx(0.006)
    assert usage._day(TODAY.isoformat())["realtime"]["transcribe_seconds"] == 60


def test_the_pc_path_is_unchanged(client, clock):
    r = client.post("/api/usage", json={"usage": audio_out(15_000), "model": "gpt-realtime-2.1"})
    assert r.status_code == 200 and usage.realtime_spent_today() == pytest.approx(0.96)
    r = client.post("/api/usage", json={"usage": audio_out(20_000), "model": "gpt-realtime-2.1"})
    assert r.status_code == 200 and usage.realtime_spent_today() == pytest.approx(0.96 + 1.28)
    assert "remote" not in store.load(usage.FILE, {})[TODAY.isoformat()]


# ---------------------------------------------------------------- Siri's text model (B3)

TEXT_USAGE = {"input_tokens": 10_000, "input_tokens_details": {"cached_tokens": 2_000, "cache_write_tokens": 1_000},
              "output_tokens": 1_000, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 11_000}


def test_the_text_price_table_and_its_unknown_models():
    assert usage.TEXT_PRICES["gpt-6-luna"] == {"in": 0.10, "cached": 0.01, "cache_write": 0.125, "out": 0.50}
    classes = usage.text_classes(TEXT_USAGE)
    assert classes == {"in": 7_000, "cached": 2_000, "cache_write": 1_000, "out": 1_000}
    luna = (7_000 * 0.10 + 2_000 * 0.01 + 1_000 * 0.125 + 1_000 * 0.50) / 1e6
    assert usage.text_cost(classes, "gpt-6-luna") == pytest.approx(luna)
    assert usage.resolve_text_model("gpt-6-luna-2026-09-01") == "gpt-6-luna"
    # A model missing from the table costs the dearest entry.
    dearest = max(usage.TEXT_PRICES.values(), key=lambda p: (p["out"], p["in"]))
    assert usage.TEXT_PRICES[usage.resolve_text_model("gpt-inconnu")] == dearest
    assert usage.text_cost(classes, "gpt-inconnu") >= max(usage.text_cost(classes, m) for m in usage.TEXT_PRICES)
    # Details claiming more than the input never count twice.
    assert usage.text_classes({"input_tokens": 10, "input_tokens_details": {"cached_tokens": 50,
                                                                             "cache_write_tokens": 5}}) == \
        {"in": 0, "cached": 10, "cache_write": 0, "out": 0}
    for bad in ({"input_tokens": -1}, {"output_tokens": "x"}, {"input_tokens": float("inf")}, "x"):
        with pytest.raises(ValueError):
            usage.text_classes(bad)


def test_the_text_ledger_counts_in_spent_today_and_the_cap(monkeypatch, pushed):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.01)
    assert usage.add_text("gpt-6-luna", TEXT_USAGE) == pytest.approx(0.001345)
    usage.add_text("gpt-6-luna", None)  # an answer without usage still counts as a call
    day = store.load("usage.json", {})[TODAY.isoformat()]
    assert day["text"] == {"in": 7_000, "cached": 2_000, "cache_write": 1_000, "out": 1_000, "calls": 2,
                           "usd": pytest.approx(0.001345)}
    assert usage.text_spent_today() == pytest.approx(0.001345)
    assert usage.spent_today() == pytest.approx(0.001345)
    assert not usage.over_daily_cap()
    usage.add_realtime(audio_out(100), "gpt-realtime-2.1")  # 0.0064 $
    usage.add_claude(0.0023)
    assert usage.spent_today() == pytest.approx(0.010045) and usage.over_daily_cap()
    body = usage.summary(1)
    assert body["today"]["text_usd"] == pytest.approx(0.001345)
    assert body["today"]["total_usd"] == pytest.approx(0.010045) and body["capped"] is True
    assert pushed[-1]["today"]["text_usd"] == pytest.approx(0.001345) and pushed[-1]["capped"] is True


def test_siri_spending_alone_reaching_the_cap_stops_tasks(client, fake_claude, monkeypatch):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.001)
    usage.add_text("gpt-inconnu", TEXT_USAGE)
    r = client.post("/api/tasks", json={"prompt": "x"})
    assert r.status_code == 400 and "Plafond" in r.json()["detail"]
    assert client.get("/api/config").json()["usage_capped"] is True


def test_days_without_siri_keep_their_shape(frozen_day):
    usage.add_claude(1.0)
    assert set(usage._entry(store.load("usage.json", {})[TODAY.isoformat()])) == {"realtime", "claude"}
    assert "text_usd" not in usage.summary(1)["today"]
    usage.add_text("gpt-6-luna", TEXT_USAGE)
    assert "text" in usage._entry(store.load("usage.json", {})[TODAY.isoformat()])
    # A hand edit never breaks the counts.
    store.save("usage.json", {TODAY.isoformat(): {"text": {"usd": "x", "calls": True, "in": float("nan")}}})
    assert usage.text_spent_today() == 0 and usage.spent_today() == 0
    store.save("usage.json", {TODAY.isoformat(): {"text": "rien"}})
    assert usage.text_spent_today() == 0

