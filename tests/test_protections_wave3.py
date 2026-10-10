"""Defensive checks of wave 3 (WP17 reminders, routines, the local briefing
and the remarques; WP18 costs and the daily cap): each test proves that one
protection holds, whatever the page, the voice model or an outside source
(the A.R.E.S agenda, a weather place name) tries.

1. Every new /api route (schedules POST/PATCH/snooze, remarques, usage) sits
   behind the token, Host and Origin guard, and test_security.py's sweep knows it.
2. A full-access routine never comes into existence without monsieur's "oui":
   not from the page (POST), not by editing (PATCH can't change a kind or a
   profile, nor a full-access routine's instruction or frequency), not by
   snoozing (a copy is always a plain reminder, a routine is never re-armed);
   and the confirmation card names the frequency that will really run.
3. The local briefing and its optional news task never put the agenda, the
   reminders, the memory or the city into a web-enabled (recherche) prompt.
5. The daily cap can't be pushed away by the page: usage reports only ever
   add, are validated, and are never priced below the configured models.
   Past the cap no Claude task starts (one queued before included) and every
   refusal is said in French.
6. usage.json, remarques.json and the briefing's state use fixed names under DATA_DIR.
7. No secret in the logs.
(4, untrusted text on the new screens, and the paid voice sessions refused
past the cap, are proven in Chromium: tests/e2e/test_protections_wave3_ui.py.)
"""
import json
import logging
import math
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from fake_ares import FakeAres
from fastapi.testclient import TestClient
from test_protections_wave2 import HOSTILE, SOURCES, _reset_ares, asgi_status
from test_security import FOREIGN_ORIGINS, KNOWN_API, served_routes

import server
from jarvis import (api_schedules, ares, briefing, config, confirm, events, inbox, info, memory, realtime,
                    remarques, scheduler, security, settings, store, tasks, tools, tools_agenda, usage)

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}
KEY = "sk-proj-WAVE3SENTINEL0123456789abcdefKEY1"
ARES_TOKEN = "ares-WAVE3SENTINEL-token-0123456789"
TODAY = date(2026, 10, 10)
# A day far ahead, whatever the real clock says: nothing of it is ever past.
FAR = datetime(2099, 3, 2, 8, 0)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Nothing pending, no voice session, a fixed spending day, no cap, the
    sentinel key, no native notification; tasks stopped afterwards."""
    confirm.PENDING.clear()
    confirm.SESSIONS.clear()
    monkeypatch.setattr(config, "CONFIRM_COMPLET", True)
    monkeypatch.setattr(config, "PENDING_TTL", 90)
    monkeypatch.setattr(config, "OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setattr(config, "ARES_TOKEN", "")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1")
    monkeypatch.setattr(usage, "_today", lambda: TODAY)
    monkeypatch.setattr(inbox, "notify_offline", lambda *a, **k: False)
    remarques._voiced.clear()
    remarques._last["items"] = None
    yield
    for task in tasks.running():
        tasks.cancel(task["id"])
    end = time.time() + 10
    while (tasks._active or tasks._waiting) and time.time() < end:
        time.sleep(0.05)
    confirm.PENDING.clear()
    confirm.SESSIONS.clear()


@pytest.fixture
def client():
    return TestClient(server.app, base_url=BASE, headers=AUTH)


def ctx(sid=None):
    return tools.ToolCtx(session_id=sid)


def until(probe, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        found = probe()
        if found:
            return found
        time.sleep(0.02)
    raise AssertionError("condition jamais remplie")


# A `claude` that writes down what it was given (arguments and prompt), and
# whose task "ATTENDS" waits for a flag file, then reports COST dollars.
RECORDING_CLAUDE = r'''
import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.300 (Claude Code)")
    sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    sys.exit(0)
sys.stdin.reconfigure(encoding="utf-8")
prompt = sys.stdin.read()
with open(RECORD, "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "prompt": prompt}) + "\n")
def emit(obj):
    print(json.dumps(obj), flush=True)
emit({"type": "system", "subtype": "init", "session_id": "s-rec", "tools": []})
slow = "ATTENDS" in prompt
if slow:
    end = time.time() + 20
    while not os.path.exists(FLAG) and time.time() < end:
        time.sleep(0.05)
emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "s-rec",
      "total_cost_usd": COST if slow else 0.01, "duration_ms": 10, "num_turns": 1, "result": "ok"})
'''


@pytest.fixture
def recording_claude(tmp_path, monkeypatch):
    """Claude Code replaced by RECORDING_CLAUDE; .records() lists what each run received."""
    record, flag = tmp_path / "claude-runs.jsonl", tmp_path / "go.flag"
    script = tmp_path / "recording_claude.py"
    script.write_text(f"RECORD = {str(record)!r}\nFLAG = {str(flag)!r}\nCOST = 1.5\n" + RECORDING_CLAUDE,
                      encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(tasks, "claude_version", lambda refresh=False: (2, 1, 300))
    monkeypatch.setattr(config, "WORKDIR", str(tmp_path / "travail"))
    monkeypatch.setattr(config, "MODELS", {"simple": "haiku", "normale": "sonnet", "complexe": "opus"})
    monkeypatch.setattr(config, "PERMISSION_MODE", "auto")
    monkeypatch.setattr(config, "SAFE_MODE", "auto")
    monkeypatch.setattr(config, "RESTRICTED", "auto")
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 3)
    monkeypatch.setattr(config, "MCP_CONFIG", "")

    class Runs:
        path, go = record, flag

        @staticmethod
        def records():
            if not record.exists():
                return []
            return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]
    return Runs


@pytest.fixture
def fake_ares(monkeypatch):
    """A stand-in for A.R.E.S's local MCP server (tests/fake_ares.py)."""
    server_ = FakeAres()
    _reset_ares()
    monkeypatch.setattr(config, "ARES", "auto")
    monkeypatch.setattr(config, "ARES_URL", server_.url)
    yield server_
    if ares._refresher:
        ares._refresher.join(5)
    server_.stop()
    _reset_ares()


def store_items(*items):
    """Straight into schedules.json, as the scheduler keeps them."""
    with store.LOCK:
        store.save(scheduler.FILE, store.load(scheduler.FILE, []) + list(items))


def item(item_id, kind="reminder", title="Rappel", text=None, due=None, repeat="none", profile="recherche"):
    return {"id": item_id, "kind": kind, "title": title, "text": text if text is not None else title,
            "due": time.time() - 1 if due is None else due, "repeat": repeat, "profile": profile,
            "complexity": "normale", "created": time.time()}


def complet_items():
    return [i for i in scheduler.items() if str(i.get("profile", "")).strip().lower() == "complet"]

# ---------------------------------------------------------------- 1. the new routes


# Every /api route wave 3 added or changed, with its methods.
WAVE3_API = {
    "/api/schedules": {"GET", "POST"}, "/api/schedules/{item_id}": {"PATCH", "DELETE"},
    "/api/schedules/{item_id}/snooze": {"POST"}, "/api/remarques": {"GET"},
    "/api/remarques/{key}/dismiss": {"POST"}, "/api/usage": {"GET", "POST"},
}


def test_every_wave3_route_is_guarded_holds(monkeypatch):
    """Without the token, with a foreign Host or from another site's page, no
    new route runs: no reminder added, edited, snoozed or removed, no remark
    dismissed, no spending counted, nothing written."""
    hit = []

    def spy(name):
        return lambda *a, **k: hit.append(name) or {"ok": True}

    with monkeypatch.context() as patch:  # the spies go before the positive check below
        for owner, name in ((scheduler, "add"), (scheduler, "update"), (scheduler, "snooze"), (scheduler, "remove"),
                            (scheduler, "cancel"), (scheduler, "items"), (remarques, "items"), (remarques, "dismiss"),
                            (usage, "add_realtime"), (usage, "add_transcription"), (usage, "summary"),
                            (store, "save")):
            patch.setattr(owner, name, spy(name))

        served = served_routes()
        assert set(WAVE3_API) <= KNOWN_API, sorted(set(WAVE3_API) - KNOWN_API)
        # The sweep of test_security.py walks exactly the served routes: none unnamed there.
        assert {p for p in served if p.startswith("/api/")} == KNOWN_API, \
            sorted({p for p in served if p.startswith("/api/")} ^ KNOWN_API)
        for path, methods in WAVE3_API.items():
            assert methods <= served.get(path, set()), (path, served.get(path))
        for module in (api_schedules, usage):  # nothing of theirs outside /api/, where no token is asked
            for route in module.router.routes:
                assert route.path.startswith("/api/"), (module.__name__, route.path)

        raw = TestClient(server.app, base_url=BASE)
        body = {"kind": "task", "profile": "complet", "text": "rm -rf", "title": "x", "at": "08:00",
                "repeat": "daily", "minutes": 10, "usage": {"output_tokens": 5}, "model": "gpt-realtime-2.1"}
        for path, methods in sorted(WAVE3_API.items()):
            url = re.sub(r"\{[^}]+\}", "x", path)
            for method in sorted(methods):
                kw = {} if method in ("GET", "DELETE") else {"json": body}
                where = (method, path)
                assert raw.request(method, url, **kw).status_code == 401, where
                assert raw.request(method, url, headers={"X-Jarvis-Token": "nope"}, **kw).status_code == 401, where
                assert raw.request(method, url, headers={"X-Jarvis-Token": ""}, **kw).status_code == 401, where
                assert raw.request(method, f"{url}?token=nope", **kw).status_code == 401, where
                for host in ("evil.example:8788", "evil.example", "127.0.0.1.evil.example:8788"):
                    r = raw.request(method, url, headers={**AUTH, "Host": host}, **kw)
                    assert r.status_code == 403, (*where, host)
                for origin in FOREIGN_ORIGINS:
                    r = raw.request(method, url, headers={**AUTH, "Origin": origin}, **kw)
                    assert r.status_code == 403, (*where, origin)
                    r = raw.request(method, url, headers={"Origin": origin}, **kw)
                    assert r.status_code == 403, (*where, origin)
                # A simple cross-site form post (text/plain, no preflight) is refused too.
                if method == "POST":
                    r = raw.post(url, content=json.dumps(body), headers={"Content-Type": "text/plain",
                                                                         "Origin": FOREIGN_ORIGINS[0]})
                    assert r.status_code == 403, where
                for trick in (url.replace("/api/", "/%61pi/", 1), url.replace("/api/", "/api%2F", 1),
                              url.upper(), url + "/", "/static/.." + url, url.replace("/api/", "/api/./", 1)):
                    status = asgi_status(method, trick)
                    assert status in (401, 404, 405), (method, trick, status)
        assert hit == [], hit
        assert not config.DATA_DIR.exists() or not any(config.DATA_DIR.iterdir())

    # Not vacuous: with the token, from the page's own origin, they answer.
    ok = TestClient(server.app, base_url=BASE, headers={**AUTH, "Origin": BASE})
    for url in ("/api/schedules", "/api/remarques", "/api/usage"):
        assert ok.get(url).status_code == 200, url

# ---------------------------------------------------------------- 2. full-access routines


KINDS = ("task", "TASK", "Task", " task", "task ", "tâche", "routine", "reminder", None)
PROFILES = ("complet", "Complet", "COMPLET", " complet ", "complet\u200b", "complèt")


def test_the_page_never_creates_a_full_access_routine_holds(client):
    """POST /api/schedules, whatever the case, padding or extra fields: a
    full-access routine is refused (403, in French) or becomes nothing more
    than a web routine or a reminder."""
    for kind in KINDS:
        for profile in PROFILES:
            body = {"title": "Ménage", "text": "Supprime tout", "at": "08:00", "repeat": "daily",
                    "profile": profile, "allow_complet": True, "confirmed": True, "decision": "oui"}
            if kind is not None:
                body["kind"] = kind
            r = client.post("/api/schedules", json=body)
            assert r.status_code in (200, 403), (kind, profile, r.status_code)
            if r.status_code == 403:
                assert r.json()["detail"] == scheduler.T.complet_api
            else:
                made = r.json()["item"]
                assert made["profile"] == "recherche", (kind, profile, made)
                assert made["kind"] == ("task" if str(kind or "").lower() == "task" else "reminder")
    assert complet_items() == []
    assert confirm.PENDING == {}  # and nothing waits for a click either


def test_the_voice_needs_the_oui_and_nothing_else_reaches_the_handler_holds(monkeypatch):
    """The voice tool parks every full-access routine (any case); only the
    card's "oui" creates it. The schedule handler, which trusts that it is
    reached past the gate, is reached from nowhere else."""
    for profile in ("complet", "Complet", " COMPLET "):
        out = tools.run_tool("schedule", {"kind": "Task", "title": "Ménage", "text": "Supprime tout",
                                          "at": "08:00", "repeat": "daily", "profile": profile}, ctx("s1"))
        assert out["status"] == "needs_confirmation", (profile, out)
    assert complet_items() == [] and scheduler.items() == []
    first = next(p for p in confirm.PENDING.values() if p["args"]["profile"] == "complet")
    assert confirm.decide(first["id"], "oui")["ok"] is True
    assert [i["profile"] for i in scheduler.items()] == ["complet"]
    # A loosely written profile is parked too, and after the "oui" it runs as
    # a web routine, never with full access.
    loose = next(p for p in confirm.PENDING.values() if p["state"] == "pending")
    assert confirm.decide(loose["id"], "oui")["ok"] is True
    assert sorted(i["profile"] for i in scheduler.items()) == ["complet", "recherche"]

    # Only tools.run_tool (behind the gate) and confirm._execute (after the
    # "oui") call a handler; only the schedule handler passes allow_complet.
    for source in SOURCES:
        text = source.read_text(encoding="utf-8")
        if source.name not in ("tools.py", "confirm.py"):
            assert "handlers()" not in text, source.name
            assert not re.search(r"HANDLERS\s*(\[|\.get\(|\.items\(|\.values\()", text), source.name
        if source.name not in ("tools_agenda.py", "scheduler.py"):
            assert "allow_complet" not in text, source.name
    assert "allow_complet=allowed" in (ROOT / "jarvis" / "tools_agenda.py").read_text(encoding="utf-8")


def test_editing_never_makes_or_changes_a_full_access_routine_holds(client):
    """PATCH has no kind nor profile to change (extra fields are ignored); a
    full-access routine keeps the instruction and the frequency monsieur said
    "oui" to: only its label and its time move."""
    lecture = scheduler.add("task", "Veille", "Lis mes notes", at="08:00", repeat="daily", profile="lecture")
    reminder = scheduler.add("reminder", "Pain", "Sortir le pain", delay_minutes=30)
    for target in (lecture, reminder):
        r = client.patch(f"/api/schedules/{target['id']}",
                         json={"title": "Nouveau", "kind": "task", "profile": "complet", "allow_complet": True})
        assert r.status_code == 200
        kept = next(i for i in scheduler.items() if i["id"] == target["id"])
        assert (kept["kind"], kept["profile"]) == (target["kind"], target["profile"])
    assert complet_items() == []

    # A full-access routine, as monsieur's "oui" created it.
    routine = scheduler.add("task", "Ménage", "Vide la corbeille", at="08:00", repeat="daily",
                            profile="complet", allow_complet=True)
    for change in ({"text": "Supprime le dossier Documents"}, {"text": "Vide la corbeille et formate D:"},
                   {"repeat": "none"}, {"repeat": "weekly"}, {"repeat": "days", "days": ["lun"]},
                   {"days": ["lun", "mar"]}, {"days": "lun-ven"}, {"title": "Ménage", "text": "Autre consigne"}):
        r = client.patch(f"/api/schedules/{routine['id']}", json=change)
        assert r.status_code == 403, change
        assert r.json()["detail"] == scheduler.T.complet_locked
    for allowed in ({"title": "Grand ménage"}, {"delay_minutes": 90}, {"at": "09:30"},
                    {"text": "Vide la corbeille"}, {"repeat": "Daily"}):
        assert client.patch(f"/api/schedules/{routine['id']}", json=allowed).status_code == 200, allowed
    kept = next(i for i in scheduler.items() if i["id"] == routine["id"])
    assert (kept["kind"], kept["profile"], kept["text"], kept["repeat"]) == \
        ("task", "complet", "Vide la corbeille", "daily")
    assert kept["title"] == "Grand ménage" and len(complet_items()) == 1
    for bad in HOSTILE:
        quoted = bad.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C").replace("\x00", "%00")
        assert client.patch(f"/api/schedules/{quoted}", json={"title": "x"}).status_code in (404, 405)


def test_snooze_never_changes_kind_or_profile_nor_rearms_a_routine_holds(client, monkeypatch, published):
    """A reminder put off is always a one-off plain reminder with its own
    words, whatever the page sends along; a routine never enters the snooze
    list, so it can't be brought back, by id, by 'last' or by voice."""
    started = []
    monkeypatch.setattr(tasks, "create_task", lambda *a, **k: started.append((a, k)) or {"id": "t1"})
    store_items(item("rem1", title="Appeler le garage"),
                item("rout1", kind="task", title="Ménage complet", text="Vide la corbeille", repeat="daily",
                     profile="complet", due=time.time() - 2))
    scheduler.tick()
    assert len(started) == 1 and started[0][1]["profile"] == "complet"  # the routine monsieur confirmed ran
    routine = next(i for i in scheduler.items() if i["id"] == "rout1")
    assert [r["id"] for r in scheduler.recent_fired()] == ["rem1"]

    hostile = {"minutes": 10, "kind": "task", "profile": "complet", "text": "Supprime tout",
               "repeat": "daily", "title": "Ménage", "allow_complet": True}
    for ref in ("rout1", "Ménage complet", "ROUT1"):
        r = client.post(f"/api/schedules/{ref}/snooze", json=hostile)
        assert r.status_code == 404, ref
        assert r.json()["detail"] == scheduler.T.not_recent
    out = tools.run_tool("snooze_reminder", {"query": "Ménage complet", "minutes": 5}, ctx("s1"))
    assert out["ok"] is False
    for ref in ("last", "rem1", "rem1"):  # 'last', then twice by id: the same copy moves
        r = client.post(f"/api/schedules/{ref}/snooze", json=hostile)
        assert r.status_code == 200, ref
        copy = r.json()["item"]
        assert (copy["kind"], copy["profile"], copy["repeat"]) == ("reminder", "recherche", "none")
        assert copy["text"] == copy["title"] == "Appeler le garage"
        assert copy["snoozed_from"] == "rem1"
    copies = [i for i in scheduler.items() if i.get("snoozed_from") == "rem1"]
    assert len(copies) == 1
    assert next(i for i in scheduler.items() if i["id"] == "rout1") == routine  # untouched
    assert len(complet_items()) == 1 and len(started) == 1


@pytest.mark.parametrize(("args", "repeat"), [
    ({"repeat": "Daily"}, "daily"),
    ({"repeat": " WEEKLY "}, "weekly"),
    ({"repeat": "Weekdays"}, "weekdays"),
    ({"days": ["lun", "jeu"]}, "days"),
    ({"repeat": "none", "days": ["mar"]}, "days"),
    ({"repeat": "", "days": "lun-ven"}, "days"),
    ({"repeat": "monthly"}, "monthly"),
    ({}, "none"),
])
def test_the_confirmation_card_names_the_frequency_that_will_run_holds(args, repeat):
    """What monsieur says "oui" to is what runs: a card that said « ponctuelle »
    for a routine that then repeats every day (or on given days) would hide
    the frequency behind a harmless word."""
    call = {"kind": "task", "title": "Ménage", "text": "Vide la corbeille", "at": "08:00",
            "profile": "complet", **args}
    out = tools.run_tool("schedule", call, ctx("s1"))
    assert out["status"] == "needs_confirmation"
    assert confirm.decide(out["pending_id"], "oui")["ok"] is True
    made = complet_items()
    assert len(made) == 1 and made[0]["repeat"] == repeat
    assert out["summary"] == confirm.T.complet_routine.format(freq=confirm.FREQ[repeat], title="Ménage")

# ---------------------------------------------------------------- 3. the web-enabled prompt


PRIVATE = ("4821", "portail", "Dr Martin", "Dupont", "Biopsie", "avocat", "Saint-Quentin", "A.R.E.S",
           "rappel", "routine", "agenda")


def _private_sources(monkeypatch):
    """Monsieur's memory, his reminders and routines of the day, his A.R.E.S
    agenda and his city, all present when the briefing is built."""
    memory.remember("Le code du portail est 4821")
    store_items(item("r1", title="Rendez-vous chez le Dr Martin", due=FAR.replace(hour=10).timestamp()),
                item("r2", kind="task", title="Relire le contrat Dupont", profile="lecture",
                     due=FAR.replace(hour=18).timestamp()))
    monkeypatch.setattr(config, "CITY", "Saint-Quentin")
    monkeypatch.setattr(config, "BRIEFING_NEWS", True)
    monkeypatch.setattr(config, "QUIET_HOURS", "")
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k:
                        "• Biopsie à l'hôpital — Aujourd'hui · 14:00\n• Payer l'avocat — En retard (2 j)")
    monkeypatch.setattr(info, "weather", lambda city=None, quand="maintenant":
                        {"ok": True, "text": f"Aujourd'hui à {city} : soleil, 12 °C."})


def test_briefing_news_task_carries_nothing_private_to_the_web_holds(monkeypatch, recording_claude, published):
    """The feeds can't be read, so one Claude task looks the headlines up on
    the web: what that Claude Code process receives (prompt and arguments)
    holds the date and nothing of monsieur's, and no memory is added to it."""
    _private_sources(monkeypatch)
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": False, "error": "flux illisibles"})
    payload = briefing.run(FAR)
    # Not vacuous: the local brief (said on this PC) does hold them.
    for fragment in ("Dr Martin", "Dupont", "Saint-Quentin", "1 tâche en retard"):
        assert fragment in payload["text"], fragment
    news = until(lambda: [t for t in tasks.TASKS.values() if t["title"] == briefing.T.news_title])
    assert len(tasks.TASKS) == 1
    task = news[0]
    assert task["profile"] == "recherche" and task["origin"] == "routine"
    until(lambda: task["status"] not in tasks.ACTIVE)
    runs = recording_claude.records()
    assert len(runs) == 1
    sent = runs[0]["prompt"]
    assert sent == task["prompt"] == briefing.news_prompt(FAR)
    assert "2 mars 2099" in sent
    everything = sent + " ".join(runs[0]["args"]) + task.get("full_prompt", "")
    for fragment in PRIVATE:
        assert fragment.lower() not in everything.lower(), fragment
    assert "WebSearch" in " ".join(runs[0]["args"])  # it is the web-enabled profile


def test_briefing_with_readable_feeds_or_past_the_cap_starts_no_task_holds(monkeypatch, recording_claude,
                                                                          published):
    _private_sources(monkeypatch)
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": True, "headlines": [{"title": "Élections"}]})
    briefing.run(FAR)
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": False, "error": "flux illisibles"})
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    usage.add_claude(1.2)
    briefing.run(FAR)
    time.sleep(0.3)
    assert tasks.TASKS == {} and recording_claude.records() == []
    assert [e["capped"] for e in published if e["type"] == "briefing"] == [False, True]

# ---------------------------------------------------------------- 5. the daily cap


def _today_entry() -> dict:
    return usage._entry(store.load(usage.FILE, {}).get(TODAY.isoformat()))


def _leaves(entry: dict) -> dict:
    return {f"{part}.{k}": v for part, fields in entry.items() for k, v in fields.items()}


NUMBERS = [0, 1, 7, 1000, 10**6, 49_999_999, 50_000_001, -1, -(10**6), 1.5, -0.5, float("nan"), float("inf"),
           float("-inf"), True, False, None, "100", [], {}, 10**30]


def _fuzzed(rng: random.Random):
    """A usage report as a broken or hostile page could send it."""
    def some(keys):
        return {k: rng.choice(NUMBERS) for k in keys if rng.random() < 0.75}

    kind = rng.choice(("rt", "rt", "rt", "duration", "tokens", "junk"))
    if kind == "rt":
        u = some(("input_tokens", "output_tokens", "total_tokens"))
        details = some(("text_tokens", "audio_tokens", "image_tokens", "cached_tokens"))
        if rng.random() < 0.6:
            details["cached_tokens_details"] = some(("text_tokens", "audio_tokens", "image_tokens"))
        u["input_token_details"] = details if rng.random() < 0.9 else rng.choice(NUMBERS)
        u["output_token_details"] = some(("text_tokens", "audio_tokens")) if rng.random() < 0.9 \
            else rng.choice(NUMBERS)
    elif kind == "duration":
        u = {"type": "duration", "seconds": rng.choice(NUMBERS)}
    elif kind == "tokens":
        u = {"type": "tokens", **some(("input_tokens", "output_tokens")),
             "input_token_details": some(("audio_tokens", "text_tokens"))}
    else:
        u = {"type": rng.choice(("inconnu", "", "DURATION", 3, None)), "seconds": rng.choice(NUMBERS)}
    model = rng.choice(("", "gpt-realtime-mini", "gpt-realtime-2.1-mini", "gpt-realtime-2.1", "<img src=x>",
                        "gpt-4o-mini-transcribe", "x" * 300))
    return json.dumps({"usage": u, "model": model}, allow_nan=True)


def test_usage_reports_only_ever_add_holds(client):
    """Hundreds of broken or hostile reports (negative, NaN, infinite, huge,
    cached above seen, output below its parts, wrong types): each is refused
    or adds something; no field of today's ledger ever goes down, and no
    route lowers it."""
    rng = random.Random(20261010)
    before = _leaves(_today_entry())
    accepted = 0
    for _ in range(300):
        r = client.post("/api/usage", content=_fuzzed(rng), headers={"Content-Type": "application/json"})
        assert r.status_code in (200, 400, 422), r.text
        accepted += r.status_code == 200
        now = _leaves(_today_entry())
        for k, v in now.items():
            assert math.isfinite(v) and v >= before[k] - 1e-6, (k, before[k], v)
        before = now
    assert accepted >= 20  # not vacuous: plenty were counted
    # The classic ways to subtract: refused, or nothing taken away.
    spent = usage.spent_today()
    for lower in ({"output_tokens": -(10**6)}, {"type": "duration", "seconds": -600},
                  {"input_tokens": 0, "input_token_details": {"audio_tokens": 10,
                                                              "cached_tokens_details": {"audio_tokens": 10**7}}},
                  {"output_tokens": 0, "output_token_details": {"audio_tokens": 10, "text_tokens": 10}}):
        client.post("/api/usage", json={"usage": lower, "model": "gpt-realtime-2.1"})
        assert usage.spent_today() >= spent
        spent = usage.spent_today()
    assert served_routes()["/api/usage"] - {"HEAD", "OPTIONS"} == {"GET", "POST"}
    for days in ("0", "-1", "91", "x", "1e9"):
        assert client.get("/api/usage", params={"days": days}).status_code == 422, days


def audio_out(n):
    return {"output_tokens": n, "output_token_details": {"audio_tokens": n, "text_tokens": 0}}


def test_the_page_cannot_price_the_voice_below_the_configured_model_holds(client, monkeypatch):
    """The page names its session's model: naming a cheaper one than Réglages
    set must not make the cap look further away (one million audio tokens out
    on gpt-realtime-2.1 cost 64 $, not mini's 20 $)."""
    for claimed in ("gpt-realtime-mini", "gpt-realtime-2.1-mini", "gpt-realtime-mini-2025-10-06", "",
                    "inconnu", "gpt-realtime-2.1"):
        before = usage.spent_today()
        assert client.post("/api/usage", json={"usage": audio_out(1_000_000), "model": claimed}).status_code == 200
        assert usage.spent_today() - before == pytest.approx(64.0), claimed
    # Switched to mini in Réglages while a 2.1 session still runs: still its real price.
    monkeypatch.setattr(config, "REALTIME_MODEL", "gpt-realtime-2.1-mini")
    before = usage.spent_today()
    client.post("/api/usage", json={"usage": audio_out(1_000_000), "model": "gpt-realtime-2.1"})
    assert usage.spent_today() - before == pytest.approx(64.0)
    before = usage.spent_today()
    client.post("/api/usage", json={"usage": audio_out(1_000_000), "model": ""})
    assert usage.spent_today() - before == pytest.approx(20.0)
    # The transcription of monsieur's words: never below the configured model's rate either.
    monkeypatch.setattr(realtime, "transcribe_model", lambda: "gpt-4o-transcribe")
    for claimed in ("gpt-4o-mini-transcribe", "", "whisper-1"):
        before = usage.spent_today()
        client.post("/api/usage", json={"usage": {"type": "duration", "seconds": 600}, "model": claimed})
        assert usage.spent_today() - before == pytest.approx(0.06), claimed


def _capped(monkeypatch, cap=1.0):
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", cap)
    usage.add_claude(0.40)
    usage.add_realtime(audio_out(10_000))  # + 0,64 $ of voice
    assert usage.over_daily_cap()


def test_past_the_cap_no_claude_task_starts_and_each_refusal_is_french_holds(client, monkeypatch, published):
    """The composer, the voice (with or without full access, after the "oui"
    too), Réessayer, « JARVIS a remarqué », a routine coming due and the
    briefing: none starts Claude, and each says why in French."""
    monkeypatch.setattr(tasks, "claude_command", lambda: pytest.fail("Claude lancé malgré le plafond"))
    monkeypatch.setattr(settings, "versions", lambda: {})  # /api/config: no `claude --version` probe
    _capped(monkeypatch)
    said = "Plafond du jour atteint (1,00 $). Modifiable dans Réglages › Coûts."
    r = client.post("/api/tasks", json={"title": "x", "prompt": "Cherche un aspirateur", "profile": "lecture"})
    assert (r.status_code, r.json()["detail"]) == (400, said)
    out = tools.run_tool("delegate_to_claude", {"title": "x", "prompt": "Cherche", "profile": "recherche"}, ctx("s1"))
    assert out == {"ok": False, "error": said}
    parked = tools.run_tool("delegate_to_claude", {"title": "x", "prompt": "Range", "profile": "complet"}, ctx("s1"))
    assert parked["status"] == "needs_confirmation"
    decided = confirm.decide(parked["pending_id"], "oui")
    assert decided["ok"] is False and decided["result"] == {"ok": False, "error": said}
    tasks.TASKS["fin1"] = {"id": "fin1", "title": "Comparer", "prompt": "Compare deux aspirateurs",
                           "profile": "lecture", "complexity": "normale", "status": "error", "origin": "voix",
                           "started": time.time() - 600, "ended": time.time() - 500, "output": "", "log": []}
    r = client.post("/api/task/fin1/retry")
    assert (r.status_code, r.json()["detail"]) == (400, said)
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k: "")
    remark = next(x for x in client.get("/api/remarques").json() if x["key"] == "tache-fin1")
    assert remark["action"] is None and "Plafond du jour atteint" in remark["why"]
    store_items(item("rout1", kind="task", title="Veille", text="Actus IA", repeat="daily", profile="lecture"))
    scheduler.tick()
    warnings = [e["text"] for e in published if e["type"] == "warning"]
    assert warnings == [f"Routine « Veille » non lancée : {said}"]
    monkeypatch.setattr(config, "BRIEFING_NEWS", True)
    monkeypatch.setattr(config, "CITY", "")
    monkeypatch.setattr(info, "news", lambda n=5: {"ok": False, "error": "flux illisibles"})
    assert briefing.run(FAR)["capped"] is True
    assert [t["id"] for t in tasks.TASKS.values()] == ["fin1"]
    assert client.get("/api/config").json()["usage_capped"] is True
    assert tools.run_tool("get_status", {})["spending"]["cap_reached"] is True


def test_a_task_queued_before_the_cap_does_not_start_after_it_holds(monkeypatch, recording_claude, published):
    """One slot: a task runs, another waits its turn. The first one's cost
    reaches the cap: the waiting one is not started (no Claude process) and
    says why in French."""
    monkeypatch.setattr(config, "MAX_CONCURRENT_TASKS", 1)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    first = tasks.create_task("Premier", "ATTENDS le signal", profile="lecture")
    second = tasks.create_task("Second", "TACHE-B compare deux offres", profile="lecture")
    assert second["status"] == "en_file"
    until(lambda: recording_claude.records())
    recording_claude.go.write_text("go", encoding="utf-8")  # the first ends, reporting 1,50 $
    until(lambda: first["status"] not in tasks.ACTIVE and second["status"] not in tasks.ACTIVE)
    assert first["status"] == "done" and usage.over_daily_cap()
    assert second["status"] == "error"
    assert second["output"] == "Plafond du jour atteint (1,00 $). Modifiable dans Réglages › Coûts."
    assert [r["prompt"] for r in recording_claude.records()] == ["ATTENDS le signal"]
    assert not tasks._active and not tasks._waiting

# ---------------------------------------------------------------- 6. data paths


def test_usage_remarques_and_briefing_state_write_fixed_names_holds(client, monkeypatch, tmp_path, published):
    """Hostile model names, remark keys, reminder ids, titles and a hostile
    city go through the usage, remarques, schedules and briefing paths: the
    only files written are fixed names under DATA_DIR."""
    assert (usage.FILE, remarques.FILE, briefing.STATE_FILE, scheduler.STATE_FILE, scheduler.FILE) == \
        ("usage.json", "remarques.json", "state.json", "state.json", "schedules.json")
    data = config.DATA_DIR

    def quoted(bad):
        return bad.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C").replace("\x00", "%00")

    for bad in HOSTILE:
        client.post("/api/usage", json={"usage": audio_out(10), "model": bad})
        client.post("/api/usage", json={"usage": {"type": "duration", "seconds": 1}, "model": bad})
        client.get("/api/usage", params={"days": bad})
        assert client.post(f"/api/remarques/{quoted(bad)}/dismiss").status_code in (404, 405)
        client.post("/api/schedules", json={"title": bad, "text": bad, "delay_minutes": 5})
        client.patch(f"/api/schedules/{quoted(bad)}", json={"title": bad})
        client.post(f"/api/schedules/{quoted(bad)}/snooze", json={"minutes": 5})
        client.delete(f"/api/schedules/{quoted(bad)}")
    usage.add_claude(0.01)
    store_items(item("fired1", title=HOSTILE[0]))
    scheduler.tick()
    assert client.post("/api/schedules/fired1/snooze", json={"minutes": 5}).status_code == 200
    monkeypatch.setattr(ares, "agenda_text", lambda n=1200, **k:
                        "• ../../evil — En retard (2 j)\n• C:\\evil — En retard (3 j)")
    assert client.post("/api/remarques/ares-retard/dismiss").status_code == 200
    monkeypatch.setattr(config, "CITY", HOSTILE[0])
    monkeypatch.setattr(config, "BRIEFING_TIME", "07:30")
    monkeypatch.setattr(config, "BRIEFING_DAYS", "tous")
    monkeypatch.setattr(config, "BRIEFING_NEWS", False)
    briefing._done["day"] = None
    briefing.maybe_run(FAR.timestamp())
    assert briefing._thread is not None
    briefing._thread.join(10)
    assert store.load("state.json", {})["briefing_date"] == FAR.date().isoformat()

    allowed = re.compile(r"(usage|remarques|state|schedules|inbox|tasks)\.json(\.bak|\.tmp|\.corrompu-[\d-]+)?")
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    inside = [p for p in written if data in p.parents]
    assert {p.name for p in inside} >= {"usage.json", "remarques.json", "state.json", "schedules.json"}
    odd = [p for p in inside if not (p.parent == data and allowed.fullmatch(p.name))]
    assert odd == [], odd
    assert [p for p in written if data not in p.parents] == []
    assert not any("evil" in p.name for p in tmp_path.rglob("*"))
    assert set(store.load("state.json", {})) <= {"recent_fired", "briefing_date", "geocode", "dnd_until",
                                                 "onboarded"}
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", k) for k in store.load(usage.FILE, {}))
    assert set(store.load(remarques.FILE, {})) == {"ares-retard"}

# ---------------------------------------------------------------- 7. logs


def test_no_secret_reaches_the_logs_in_wave3_paths_holds(client, monkeypatch, caplog, capfd, fake_ares, published):
    """The usage reports (the key handed in as a model name or a value), a
    push that fails, broken schedule entries, a routine refused by the cap,
    the briefing and the remarques with A.R.E.S up (its token sent) then
    gone, the weather failing: nothing logged or printed carries a secret."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(config, "ARES_TOKEN", ARES_TOKEN)
    monkeypatch.setattr(tasks, "claude_command", lambda: pytest.fail("Claude lancé malgré le plafond"))
    assert {"usage.py", "briefing.py", "remarques.py", "scheduler.py", "api_schedules.py"} <= \
        {p.name for p in SOURCES}  # test_protections_wave2's logging-call sweep covers them

    for bad in ({"output_tokens": KEY}, {"type": "duration", "seconds": KEY}, {"type": KEY},
                {"input_token_details": KEY}):
        client.post("/api/usage", json={"usage": bad, "model": KEY})
    client.post("/api/usage", json={"usage": audio_out(10), "model": KEY})
    client.get("/api/usage")

    def broken_push(kind, data):
        raise RuntimeError("bus indisponible")
    with monkeypatch.context() as m:
        m.setattr(events, "publish", broken_push)
        usage.add_claude(0.01)

    store_items({"title": "sans id", "due": "demain"}, {"id": "x"},
                item("rout1", kind="task", title="Veille", text="Actus IA", repeat="daily", profile="lecture"))
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.001)
    scheduler.tick()
    assert any(e["type"] == "warning" for e in published)

    def failing_weather(city=None, quand="maintenant"):
        raise RuntimeError("Open-Meteo injoignable")
    monkeypatch.setattr(info, "weather", failing_weather)
    monkeypatch.setattr(config, "CITY", "Laon")
    briefing.run(FAR)
    remarques.items()
    tools_agenda.instructions_block()
    assert any(r[2].get("Authorization") == f"Bearer {ARES_TOKEN}" for r in fake_ares.requests)  # sent to A.R.E.S
    fake_ares.stop()
    _reset_ares()
    briefing.run(FAR)
    remarques.items()
    scheduler._refresh()
    tools.run_tool("get_status", {})
    tools.run_tool("snooze_reminder", {"query": KEY})

    out, err = capfd.readouterr()
    logged = caplog.text + "".join(str(rec.args) for rec in caplog.records) + out + err
    assert "JARVIS" in logged  # not vacuous: those paths did log
    for secret in (KEY, ARES_TOKEN, security.TOKEN):
        assert secret not in logged and secret[3:] not in logged, secret[:8]
