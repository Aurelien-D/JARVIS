"""The Siri endpoint (spec 5): POST /api/raccourci through the real remote
gate with a real Siri key, OpenAI faked by an httpx.MockTransport (never the
network), the conversation kept here, the model fallback, the deadline, the
cost ledger, the four tools, and the key hand-off routes.
Fictitious names only: the repository is public."""
import json
import threading
import time
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from remote_helpers import REMOTE_HOST, paired_client, remote_client

import server
from jarvis import (
    audit,
    config,
    confirm,
    devices,
    events,
    raccourci,
    remote,
    scheduler,
    security,
    store,
    tasks,
    usage,
)

AUTH = {"X-Jarvis-Token": security.TOKEN}
USAGE = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 0},
         "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 1100}


def text_reply(text: str, usage_obj=None) -> dict:
    return {"id": "resp_test", "object": "response", "status": "completed",
            "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}],
            "usage": USAGE if usage_obj is None else usage_obj}


def call_reply(name: str, args: dict, call_id: str = "call_1", reasoning: bool = False) -> dict:
    output = []
    if reasoning:
        output.append({"id": "rs_test", "type": "reasoning", "summary": [], "encrypted_content": "chiffré"})
    output.append({"id": f"fc_{call_id}", "type": "function_call", "status": "completed", "call_id": call_id,
                   "name": name, "arguments": json.dumps(args, ensure_ascii=False)})
    return {"id": "resp_call", "object": "response", "status": "completed", "output": output, "usage": USAGE}


def refused(model: str, status: int = 404) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": f"The model `{model}` does not exist or you do not "
                                                             "have access to it.", "type": "invalid_request_error",
                                                  "param": None, "code": "model_not_found"}})


class FakeOpenAI:
    """The Responses API: answers in order (a dict, an httpx.Response, or a
    function of the request body), then « D'accord. »."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []
        self.headers = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == raccourci.RESPONSES_URL
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(dict(request.headers))
        reply = self.replies.pop(0) if self.replies else text_reply("D'accord.")
        if callable(reply):
            reply = reply(body)
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    @property
    def models(self) -> list:
        return [b["model"] for b in self.bodies]


@pytest.fixture
def openai(monkeypatch):
    """Install a FakeOpenAI; call it with the replies."""
    def install(*replies):
        fake = FakeOpenAI(*replies)
        monkeypatch.setattr(raccourci, "TRANSPORT", httpx.MockTransport(fake))
        return fake
    return install


@pytest.fixture
def siri(monkeypatch):
    """A paired iPhone with one Siri key, through the real gate: (siri client, key_id, device, phone)."""
    phone, device, _ = paired_client(monkeypatch)
    key_id, secret = devices.add_siri_key(device["id"])
    client = siri_client(key_id, secret)
    yield client, key_id, device, phone
    assert raccourci._wait_workers(10)


def siri_client(key_id: str, secret: str, **kw) -> TestClient:
    """The Shortcut: no Origin, no cookie, the Siri key as a bearer."""
    return remote_client(origin=False, extra={"Authorization": f"Bearer jv_siri_{key_id}.{secret}"}, **kw)


@pytest.fixture
def pc():
    return TestClient(server.app, base_url="http://127.0.0.1:8788", headers=AUTH)


def say(client, text, **kw):
    return client.post("/api/raccourci", json={"text": text}, **kw)

# ---------------------------------------------------------------- replies


def test_plain_text_reply_by_default(siri, openai):
    client, key_id, _, _ = siri
    fake = openai(text_reply("Il est **dix** heures : https://exemple.test voilà."))
    r = say(client, "Quelle heure est-il ?")
    assert r.status_code == 200 and r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.text == "Il est dix heures : voilà."  # no markup, no web address: it is spoken
    body = fake.bodies[0]
    assert body["model"] == "gpt-6-luna" and body["store"] is False
    assert body["reasoning"] == {"effort": "none"} and body["include"] == ["reasoning.encrypted_content"]
    assert body["max_output_tokens"] == 300
    assert [t["name"] for t in body["tools"]] == ["rappel", "recherche", "mes_taches", "annuler_tache"]
    assert all(t["type"] == "function" for t in body["tools"])
    assert body["instructions"].startswith(raccourci.INSTRUCTIONS) and "Nous sommes le " in body["instructions"]
    assert body["input"] == [{"role": "user", "content": "Quelle heure est-il ?"}]
    assert fake.headers[0]["authorization"] == f"Bearer {config.OPENAI_API_KEY}"
    assert confirm.session_origin(raccourci._CONVOS[key_id]["sid"]) == f"siri:{key_id}"


def test_json_reply_with_accept_or_format(siri, openai):
    client, _, _, _ = siri
    openai(text_reply("Bonjour monsieur."), text_reply("Bonjour monsieur."), text_reply("À votre service."))
    r = say(client, "Bonjour", headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json() == {"speech": "Bonjour monsieur.", "end": False}
    r = client.post("/api/raccourci?format=json", json={"text": "Bonjour"})
    assert r.json() == {"speech": "Bonjour monsieur.", "end": False}
    # Monsieur closes the exchange: end, and the next one starts afresh.
    r = say(client, "Merci beaucoup Jarvis.", headers={"Accept": "application/json"})
    assert r.json() == {"speech": "À votre service.", "end": True}
    assert raccourci._CONVOS == {}


def test_conversation_continues_within_five_minutes_and_resets_after(siri, openai, monkeypatch):
    client, key_id, _, _ = siri
    now = {"t": time.time()}
    monkeypatch.setattr(raccourci, "_now", lambda: now["t"])
    fake = openai(text_reply("Lyon, 18 degrés."), text_reply("Demain, 20 degrés."), text_reply("Oui ?"))
    assert say(client, "Quel temps à Lyon ?").text == "Lyon, 18 degrés."
    sid = raccourci._CONVOS[key_id]["sid"]
    now["t"] += 299
    assert say(client, "Et demain ?").text == "Demain, 20 degrés."
    assert fake.bodies[1]["input"] == [{"role": "user", "content": "Quel temps à Lyon ?"},
                                       {"role": "assistant", "content": "Lyon, 18 degrés."},
                                       {"role": "user", "content": "Et demain ?"}]
    assert raccourci._CONVOS[key_id]["sid"] == sid
    now["t"] += 301  # five idle minutes: a new conversation, a new session
    say(client, "Et après-demain ?")
    assert fake.bodies[2]["input"] == [{"role": "user", "content": "Et après-demain ?"}]
    assert raccourci._CONVOS[key_id]["sid"] != sid


def test_only_the_last_twelve_messages_are_kept(siri, openai):
    client, key_id, _, _ = siri
    fake = openai(*[text_reply(f"Réponse {n}.") for n in range(8)])
    for n in range(8):
        remote.reset_memory()  # past the gate's 6 a minute
        assert say(client, f"Question {n}").status_code == 200
    assert len(raccourci._CONVOS[key_id]["items"]) == 12
    assert fake.bodies[-1]["input"][0] == {"role": "user", "content": "Question 1"}
    assert len(fake.bodies[-1]["input"]) == 13


def test_bad_requests_are_refused_without_calling_openai(siri, openai):
    client, _, _, _ = siri
    fake = openai()
    for body, status, text in (("pas du json", 400, raccourci.T.bad_request),
                               ('{"texte": "x"}', 400, raccourci.T.bad_request),
                               ('["x"]', 400, raccourci.T.bad_request),
                               ('{"text": 3}', 400, raccourci.T.bad_request),
                               ('{"text": "   "}', 400, raccourci.T.empty),
                               (json.dumps({"text": "a" * 601}), 400, raccourci.T.too_long)):
        r = client.post("/api/raccourci", content=body, headers={"Content-Type": "application/json"})
        assert (r.status_code, r.text) == (status, text), body
    assert fake.bodies == []
    remote.reset_memory()  # past the gate's 6 a minute
    assert say(client, "a" * 600).status_code == 200


def test_the_pc_page_cannot_use_the_siri_endpoint(pc, openai):
    fake = openai()
    r = pc.post("/api/raccourci", json={"text": "bonjour"})
    assert r.status_code == 403 and r.text == raccourci.T.siri_only and fake.bodies == []


def test_openai_errors_are_spoken_in_french(siri, openai, monkeypatch):
    client, _, _, _ = siri
    monkeypatch.setattr(raccourci, "TRANSPORT", httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("pas de réseau", request=request))))
    assert (say(client, "Bonjour").status_code, say(client, "Bonjour").text) == (502, raccourci.T.network)
    openai(httpx.Response(401, json={"error": {"message": "Incorrect API key", "code": "invalid_api_key"}}),
           httpx.Response(429, json={"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}),
           httpx.Response(500, json={"error": {"message": "oops"}}))
    assert say(client, "Bonjour").text == raccourci.T.unauthorized
    assert say(client, "Bonjour").text == raccourci.T.quota
    assert say(client, "Bonjour").text == raccourci.T.server
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    r = say(client, "Bonjour")
    assert r.status_code == 503 and r.text == raccourci.T.no_key

# ---------------------------------------------------------------- the model and its fallback


def test_a_refused_model_falls_back_once_and_is_remembered(siri, openai):
    client, _, _, _ = siri
    fake = openai(refused("gpt-6-luna"), text_reply("Première."), text_reply("Deuxième."))
    assert say(client, "Bonjour").text == "Première."
    assert fake.models == ["gpt-6-luna", "gpt-5.6-luna"]
    assert say(client, "Encore").text == "Deuxième."
    assert fake.models[-1] == "gpt-5.6-luna"  # remembered: no failed round trip any more
    assert "gpt-6-luna" in raccourci._REFUSED


def test_the_configured_model_comes_first_then_the_chain(siri, openai, monkeypatch):
    client, _, _, _ = siri
    monkeypatch.setattr(config, "SIRI_MODEL", "gpt-modele-inconnu")
    fake = openai(refused("gpt-modele-inconnu", 400), text_reply("Repli."))
    assert say(client, "Bonjour").text == "Repli."
    assert fake.models == ["gpt-modele-inconnu", "gpt-6-luna"]
    assert "reasoning" not in fake.bodies[0]  # a model of unknown settings gets its own defaults


def test_a_model_without_reasoning_gets_no_reasoning_settings(siri, openai, monkeypatch):
    client, _, _, _ = siri
    monkeypatch.setattr(config, "SIRI_MODEL", "gpt-4.1-nano")
    fake = openai(text_reply("Oui."))
    say(client, "Bonjour")
    assert fake.models == ["gpt-4.1-nano"] and "reasoning" not in fake.bodies[0] and "include" not in fake.bodies[0]


def test_no_model_left_says_so(siri, openai):
    client, _, _, _ = siri
    fake = openai(*[refused(m) for m in raccourci.MODELS])
    r = say(client, "Bonjour")
    assert fake.models == ["gpt-6-luna", "gpt-5.6-luna"]  # one retry per call
    assert r.status_code == 503 and r.text == raccourci.T.no_model
    r = say(client, "Bonjour")
    assert fake.models[2:] == ["gpt-5.4-nano", "gpt-4.1-nano"] and r.text == raccourci.T.no_model
    r = say(client, "Bonjour")
    assert len(fake.bodies) == 4 and r.text == raccourci.T.no_model
    assert r.text == "Le modèle de Siri est indisponible : vérifiez JARVIS_SIRI_MODEL."

# ---------------------------------------------------------------- the deadline


def test_the_deadline_answers_and_the_late_reply_joins_the_conversation(siri, openai, monkeypatch):
    client, key_id, _, _ = siri
    monkeypatch.setattr(raccourci, "DEADLINE_S", 0.3)
    release = threading.Event()

    def slow(body):
        release.wait(10)
        return text_reply("La réponse tardive.")
    openai(slow)
    began = time.monotonic()
    r = say(client, "Une question longue")
    assert r.status_code == 200 and r.text == "Je m'en occupe, je vous préviens sur l'iPhone."
    assert time.monotonic() - began < 3
    release.set()
    assert raccourci._wait_workers(10)
    assert raccourci._CONVOS[key_id]["items"][-1] == {"role": "assistant", "content": "La réponse tardive."}

# ---------------------------------------------------------------- cost


def test_usage_is_recorded_through_add_text(siri, openai):
    client, _, _, _ = siri
    openai(text_reply("Oui."))
    before = usage.spent_today()
    say(client, "Bonjour")
    expected = (800 * 0.10 + 200 * 0.01 + 100 * 0.50) / 1e6
    assert usage.text_spent_today() == pytest.approx(expected)
    assert usage.spent_today() == pytest.approx(before + expected)
    day = usage.summary(1)["today"]
    assert day["text_usd"] == pytest.approx(expected, abs=1e-6) and day["total_usd"] >= day["text_usd"]


def test_every_model_call_is_counted_with_its_model(siri, openai, monkeypatch):
    client, _, _, _ = siri
    seen = []
    monkeypatch.setattr(usage, "add_text", lambda model, u: seen.append((model, u)) or 0.0)
    openai(call_reply("mes_taches", {}), text_reply("Rien en cours."))
    say(client, "Quoi de neuf ?")
    assert seen == [("gpt-6-luna", USAGE), ("gpt-6-luna", USAGE)]


def test_no_daily_cap_or_a_reached_one_refuses_before_any_call(siri, openai, monkeypatch):
    client, _, _, _ = siri
    fake = openai()
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    r = say(client, "Bonjour")
    assert r.status_code == 403 and r.text == "Fixez un plafond de dépense par jour sur le PC pour utiliser Siri."
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 1.0)
    usage.add_claude(1.5)
    r = say(client, "Bonjour", headers={"Accept": "application/json"})
    assert r.status_code == 403 and r.json() == {"speech": "Plafond du jour atteint.", "end": True}
    assert fake.bodies == []


def test_the_cap_is_checked_again_before_each_call(siri, openai, monkeypatch):
    client, _, _, _ = siri
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.5)

    def spend(body):
        usage.add_claude(0.6)  # a task finished meanwhile
        return call_reply("mes_taches", {})
    fake = openai(spend, text_reply("jamais"))
    r = say(client, "Quoi de neuf ?")
    assert r.status_code == 403 and r.text == raccourci.T.capped and len(fake.bodies) == 1

# ---------------------------------------------------------------- tools


def test_a_reminder_round_trip_passes_reasoning_and_calls_back(siri, openai):
    client, key_id, _, _ = siri
    fake = openai(call_reply("rappel", {"texte": "sortir le pain", "quand": "dans 20 minutes"}, reasoning=True),
                  text_reply("C'est noté, dans vingt minutes."))
    r = say(client, "Rappelle-moi de sortir le pain dans 20 minutes")
    assert r.text == "C'est noté, dans vingt minutes."
    [item] = scheduler.items()
    assert item["kind"] == "reminder" and item["text"] == "sortir le pain" and item["via"] == f"siri:{key_id}"
    second = fake.bodies[1]["input"]
    assert [i.get("type") for i in second[1:]] == ["reasoning", "function_call", "function_call_output"]
    assert second[1]["encrypted_content"] == "chiffré" and second[3]["call_id"] == "call_1"
    assert json.loads(second[3]["output"])["ok"] is True
    # Across turns only the text stays.
    assert raccourci._CONVOS[key_id]["items"] == [
        {"role": "user", "content": "Rappelle-moi de sortir le pain dans 20 minutes"},
        {"role": "assistant", "content": "C'est noté, dans vingt minutes."}]


def test_reminders_are_bounded_to_a_year(siri, openai):
    client, _, _, _ = siri
    far = (datetime.now() + timedelta(days=400)).strftime("%Y-%m-%dT09:00")
    fake = openai(call_reply("rappel", {"texte": "anniversaire", "quand": far}), text_reply("Non."),
                  call_reply("rappel", {"texte": "x"}), text_reply("Quand ?"))
    say(client, "Rappelle-moi dans plus d'un an")
    assert json.loads(fake.bodies[1]["input"][-1]["output"]) == {"ok": False, "error": raccourci.T.too_far}
    say(client, "Rappelle-moi")
    assert json.loads(fake.bodies[3]["input"][-1]["output"]) == {"ok": False, "error": raccourci.T.need_when}
    assert scheduler.items() == []


def test_tool_rounds_stop_at_three(siri, openai):
    client, _, _, _ = siri
    fake = openai(*[call_reply("mes_taches", {}, call_id=f"c{n}") for n in range(5)])
    r = say(client, "Quoi de neuf ?")
    assert len(fake.bodies) == 4
    assert [b["tool_choice"] for b in fake.bodies] == ["auto", "auto", "auto", "none"]
    assert r.text == raccourci.T.unfinished


def test_research_launches_a_web_task_from_this_key(siri, openai, monkeypatch):
    client, key_id, _, _ = siri
    made = []

    def create_task(title, prompt, profile=None, complexity="normale", continue_task=None, origin="voix",
                    voice_session=None, allowed_tools=None, *, via="pc"):
        made.append({"title": title, "prompt": prompt, "profile": profile, "via": via, "continue": continue_task})
        return {"id": "t1", "status": "running", "profile": profile, "model": "sonnet"}
    monkeypatch.setattr(tasks, "create_task", create_task)
    openai(call_reply("recherche", {"titre": "Aspirateurs", "consigne": "Compare les aspirateurs robots",
                                    "profile": "complet", "continue_task": "latest"}),
           text_reply("C'est lancé, je vous préviens."))
    assert say(client, "Cherche les meilleurs aspirateurs").text == "C'est lancé, je vous préviens."
    assert made == [{"title": "Aspirateurs", "prompt": "Compare les aspirateurs robots", "profile": "recherche",
                     "via": f"siri:{key_id}", "continue": None}]
    # Audited by its title only, never its instruction.
    [line] = [x for x in audit.tail(50) if x.get("kind") == "tool"]
    assert (line["caller"], line["key"], line["tool"], line["profile"], line["title"]) == \
        ("siri", key_id, "recherche", "recherche", "Aspirateurs")
    assert "Compare" not in json.dumps(audit.tail(50), ensure_ascii=False)


def test_my_tasks_lists_this_keys_tasks_and_taints(siri, openai, monkeypatch):
    client, key_id, _, _ = siri
    now = time.time()
    tasks.TASKS.update({
        "a1": {"id": "a1", "title": "Veille", "status": "done", "output": "R" * 400, "started": now - 60,
               "via": f"siri:{key_id}", "profile": "recherche"},
        "a2": {"id": "a2", "title": "PC", "status": "done", "output": "notes du PC", "started": now - 60,
               "via": "pc", "profile": "lecture"},
        "a3": {"id": "a3", "title": "Vieille", "status": "done", "output": "x", "started": now - 2 * 86400,
               "via": f"siri:{key_id}", "profile": "recherche"}})
    fake = openai(call_reply("mes_taches", {}), text_reply("Une veille terminée."))
    say(client, "Quoi de neuf ?")
    out = json.loads(fake.bodies[1]["input"][-1]["output"])
    assert out == {"ok": True, "taches": [{"id": "a1", "titre": "Veille", "statut": "terminée", "resume": "R" * 300}]}
    assert confirm.is_tainted(raccourci._CONVOS[key_id]["sid"])
    tasks.TASKS.clear()


def test_cancel_goes_through_the_siri_rules(siri, openai, monkeypatch):
    client, key_id, _, _ = siri
    cancelled = []
    monkeypatch.setattr(tasks, "cancel", lambda task_id="latest": cancelled.append(task_id) or {"ok": True})
    tasks.TASKS.update({"s1": {"id": "s1", "status": "running", "via": f"siri:{key_id}", "started": time.time()},
                        "p1": {"id": "p1", "status": "running", "via": "pc", "started": time.time()}})
    fake = openai(call_reply("annuler_tache", {"id": "p1"}), text_reply("Non."),
                  call_reply("annuler_tache", {"id": "s1"}), text_reply("Annulée."))
    say(client, "Annule la tâche du PC")
    assert json.loads(fake.bodies[1]["input"][-1]["output"])["error"] == \
        "Seules les tâches lancées depuis Siri s'annulent ici."
    say(client, "Annule ma recherche")
    assert cancelled == ["s1"]
    tasks.TASKS.clear()

# ---------------------------------------------------------------- the key hand-off


def test_a_key_is_created_on_the_pc_and_collected_once_by_its_iphone(monkeypatch, pc, openai):
    phone, device, _ = paired_client(monkeypatch)
    r = pc.post(f"/api/remote/devices/{device['id']}/siri-key")
    assert r.status_code == 200 and set(r.json()) == {"key_id", "handoff_until"}
    key_id = r.json()["key_id"]
    assert r.json()["handoff_until"] == pytest.approx(time.time() + 600, abs=5)
    got = phone.get("/api/remote/siri-key")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    body = got.json()
    assert body["key"].startswith(f"jv_siri_{key_id}.") and body["header"] == "Authorization"
    assert body["url"] == f"https://{REMOTE_HOST}/api/raccourci" and body["value"] == f"Bearer {body['key']}"
    again = phone.get("/api/remote/siri-key")
    assert again.status_code == 404 and again.json()["detail"] == raccourci.T.no_handoff
    # The collected value opens the endpoint.
    openai(text_reply("Bonjour monsieur."))
    shortcut = remote_client(origin=False, extra={"Authorization": body["value"]})
    assert say(shortcut, "Bonjour").text == "Bonjour monsieur."
    assert raccourci._wait_workers(10)


def test_hand_off_routes_refuse_the_wrong_callers(monkeypatch, pc):
    phone, device, _ = paired_client(monkeypatch)
    other, other_device, _ = paired_client(monkeypatch, name="iPad de test")
    assert pc.post(f"/api/remote/devices/{device['id']}/siri-key").status_code == 200
    r = pc.get("/api/remote/siri-key")
    assert r.status_code == 403 and r.json()["detail"] == "Réservé à l'iPhone."
    assert other.get("/api/remote/siri-key").status_code == 404  # another device's app
    assert phone.post(f"/api/remote/devices/{device['id']}/siri-key").status_code == 403  # PC only
    assert phone.delete("/api/remote/siri-keys/k_0000000000000000").status_code == 403
    assert pc.post("/api/remote/devices/d_0000000000000000/siri-key").status_code == 404
    assert phone.get("/api/remote/siri-key").status_code == 200
    assert other_device["id"] != device["id"]


def test_at_most_two_keys_and_an_uncollected_one_is_replaced(monkeypatch, pc):
    phone, device, _ = paired_client(monkeypatch)
    first = pc.post(f"/api/remote/devices/{device['id']}/siri-key").json()["key_id"]
    second = pc.post(f"/api/remote/devices/{device['id']}/siri-key").json()["key_id"]
    assert devices.siri_key_revoked(first)  # never collected: nobody can hold it
    assert phone.get("/api/remote/siri-key").json()["key"].startswith(f"jv_siri_{second}.")
    third = pc.post(f"/api/remote/devices/{device['id']}/siri-key").json()["key_id"]
    assert phone.get("/api/remote/siri-key").status_code == 200
    r = pc.post(f"/api/remote/devices/{device['id']}/siri-key")
    assert r.status_code == 409 and "2 clés Siri au plus" in r.json()["detail"]
    assert not devices.siri_key_revoked(second) and not devices.siri_key_revoked(third)


def test_a_hand_off_lapses_after_ten_minutes(monkeypatch, pc):
    phone, device, _ = paired_client(monkeypatch)
    now = {"t": time.time()}
    monkeypatch.setattr(raccourci, "_now", lambda: now["t"])
    key_id = pc.post(f"/api/remote/devices/{device['id']}/siri-key").json()["key_id"]
    now["t"] += 601
    assert phone.get("/api/remote/siri-key").status_code == 404
    assert devices.siri_key_revoked(key_id)


def test_revoking_a_key_on_the_pc_ends_it(siri, openai, pc):
    client, key_id, device, _ = siri
    openai(text_reply("Oui."))
    say(client, "Bonjour")
    assert key_id in raccourci._CONVOS
    r = pc.delete(f"/api/remote/siri-keys/{key_id}")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert devices.siri_key_revoked(key_id) and key_id in store.load(devices.DEVICES_FILE, {})["revoked_ids"]
    assert key_id not in raccourci._CONVOS
    r = say(client, "Bonjour")
    assert r.status_code == 401 and r.text == "Clé Siri refusée : recréez-la sur le PC."
    assert pc.delete("/api/remote/siri-keys/k_0000000000000000").status_code == 404
    assert not devices.get(device["id"])["revoked"]


def test_creating_a_key_alerts_the_pc_without_the_secret(monkeypatch, pc):
    phone, device, _ = paired_client(monkeypatch)
    seen = []
    monkeypatch.setattr(audit, "alert", lambda kind, text, caller=None, **f: seen.append((kind, text, f)))
    pushed = []
    monkeypatch.setattr(events, "publish_pc", lambda kind, data: pushed.append((kind, data)))
    key_id = pc.post(f"/api/remote/devices/{device['id']}/siri-key").json()["key_id"]
    assert seen == [("siri_key", "Clé Siri créée pour « iPhone de test ».", {"device": device["id"], "key": key_id})]
    assert ("remote", {"kind": "devices"}) in pushed
