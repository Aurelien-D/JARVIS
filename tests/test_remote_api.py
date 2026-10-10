"""The remote-access routes (spec 3.14 and 4.6 to 4.7): state for the PC and for
the phone, the switch, pairing from both ends, devices, forget, the opt-in,
the audit trail, and the iPhone-only routes refusing the PC."""
import pytest
from fastapi.testclient import TestClient
from remote_helpers import (
    IP,
    LOGIN,
    REMOTE_HOST,
    enable_remote,
    paired_client,
    remote_client,
)

import server
from jarvis import (
    audit,
    config,
    devices,
    events,
    listener,
    remote,
    security,
    store,
    tailscale,
)

AUTH = {"X-Jarvis-Token": security.TOKEN}
PC_KEYS = {"enabled", "ready", "paused_until", "host", "host_source", "logins", "logins_source", "port", "listener",
           "cap_usd", "cap_ok", "complet_until", "pairing_until", "published", "devices", "requests", "tailscale",
           "serve_command", "url"}


@pytest.fixture
def pc():
    return TestClient(server.app, base_url="http://127.0.0.1:8788", headers=AUTH)


@pytest.fixture
def remote_events(monkeypatch):
    """The PC-only 'remote' events Réglages refreshes on."""
    seen = []
    real = events.publish_pc
    monkeypatch.setattr(events, "publish_pc", lambda kind, data: seen.append((kind, data)) or real(kind, data))
    return seen


def pair(pc, monkeypatch, *, ip=IP, name="iPhone de test"):
    """The whole reverse pairing, as the PC and the phone do it; returns the
    phone's client (device cookie in its jar) and the device."""
    if not remote.pairing_until():
        assert pc.post("/api/remote/pairing", json={"open": True}).status_code == 200
    phone = remote_client(ip=ip)
    asked = phone.post("/api/remote/pair-request", json={"name": name})
    assert asked.status_code == 200, asked.text
    rid = asked.json()["request_id"]
    waiting = [r for r in remote.pairing_requests() if r["status"] == "waiting"]
    body = {"code": asked.json()["code"]} if len(waiting) > 1 else {}
    assert pc.post(f"/api/remote/pair-requests/{rid}/allow", json=body).status_code == 200
    status = phone.get("/api/remote/pair-status")
    assert status.json()["status"] == "approved", status.text
    (device,) = [d for d in devices.active() if d["ip"] == ip and d["name"] == devices.clean_label(name, "iPhone")]
    return phone, device

# ---------------------------------------------------------------- state


def test_state_for_the_pc_has_every_field(pc, monkeypatch):
    enable_remote(monkeypatch)
    body = pc.get("/api/remote/state").json()
    assert set(body) == PC_KEYS
    assert body["enabled"] is True and body["ready"] is True and body["paused_until"] == 0
    assert body["host"] == REMOTE_HOST and body["host_source"] == "saved"
    assert body["logins"] == [LOGIN] and body["logins_source"] == "saved"
    assert body["port"] == config.REMOTE_PORT and set(body["listener"]) == {"running", "port", "error"}
    assert body["cap_usd"] == 5.0 and body["cap_ok"] is True
    assert body["serve_command"] == f"tailscale serve --bg --https=443 http://127.0.0.1:{config.REMOTE_PORT}"
    assert body["url"] == f"https://{REMOTE_HOST}/"
    assert set(body["tailscale"]) == {"installed", "running", "dns_name", "login"}
    assert body["devices"] == [] and body["requests"] == []


def test_state_sources_env_saved_detected_none(pc, monkeypatch):
    monkeypatch.setattr(tailscale, "self_info", lambda: {"installed": True, "running": True, "ips": [],
                                                         "dns_name": "jarvis-pc.tail0000.ts.net.",
                                                         "login": "Monsieur@Example.com"})
    body = remote.state_for(remote.PC)
    assert (body["host"], body["host_source"]) == (REMOTE_HOST, "detected")
    assert (body["logins"], body["logins_source"]) == ([LOGIN], "detected")
    assert body["tailscale"]["installed"] is True and body["enabled"] is False and body["ready"] is False
    enable_remote(monkeypatch, host="maison.tail0000.ts.net", logins=("autre@example.com",))
    body = remote.state_for(remote.PC)
    assert (body["host"], body["host_source"]) == ("maison.tail0000.ts.net", "saved")
    assert (body["logins"], body["logins_source"]) == (["autre@example.com"], "saved")
    monkeypatch.setattr(config, "REMOTE_HOST", REMOTE_HOST)
    monkeypatch.setattr(config, "REMOTE_LOGINS", f"{LOGIN}, Second@Example.com")
    body = remote.state_for(remote.PC)
    assert (body["host"], body["host_source"]) == (REMOTE_HOST, "env")
    assert (body["logins"], body["logins_source"]) == ([LOGIN, "second@example.com"], "env")
    assert remote.host() == REMOTE_HOST and remote.logins() == [LOGIN, "second@example.com"]  # env wins
    monkeypatch.setattr(tailscale, "self_info", lambda: {"installed": False, "running": False, "ips": []})
    monkeypatch.setattr(config, "REMOTE_HOST", "")
    monkeypatch.setattr(config, "REMOTE_LOGINS", "")
    store.save(remote.REMOTE_FILE, {"enabled": False})
    body = remote.state_for(remote.PC)
    assert (body["host"], body["host_source"], body["logins"], body["logins_source"], body["url"]) == \
        ("", "none", [], "none", "")


def test_state_for_the_phone_is_its_own(monkeypatch):
    phone, device, _ = paired_client(monkeypatch)
    body = phone.get("/api/remote/state").json()
    assert body == {"enabled": True, "paused_until": 0, "device": {"id": device["id"], "name": "iPhone de test"},
                    "complet_until": 0, "url": f"https://{REMOTE_HOST}/"}
    assert remote.state_for(remote.Caller(kind="unpaired", ip=IP)) == {}

# ---------------------------------------------------------------- the switch


def test_the_switch_from_the_pc(pc, monkeypatch, remote_events):
    monkeypatch.setattr(remote, "READY", True)
    started = []
    monkeypatch.setattr(listener, "start", lambda: started.append(1) or {"running": True, "port": config.REMOTE_PORT,
                                                                       "error": ""})
    monkeypatch.setattr(listener, "stop", lambda: None)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    r = pc.post("/api/remote/state", json={"enabled": True, "host": REMOTE_HOST, "login": LOGIN})
    assert r.status_code == 400 and r.json()["detail"].startswith("Fixez d'abord un plafond")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 3.0)
    r = pc.post("/api/remote/state", json={"enabled": True, "host": "Jarvis-PC.tail0000.ts.net.", "login": LOGIN})
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["host"] == REMOTE_HOST
    assert started == [1] and remote.is_enabled() and remote.logins() == [LOGIN]
    assert ("remote", {"kind": "state"}) in remote_events
    r = pc.post("/api/remote/state", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and not remote.is_enabled()
    assert remote.host() == REMOTE_HOST  # kept for the next time


def test_pause_routes(pc, monkeypatch):
    phone, _, _ = paired_client(monkeypatch)
    assert phone.post("/api/remote/pause", json={"hours": 3}).status_code == 400
    assert pc.post("/api/remote/pause", json={"hours": 25}).status_code == 400
    until = pc.post("/api/remote/pause", json={"hours": 2}).json()["paused_until"]
    assert until == pytest.approx(remote._now() + 7200, abs=5) and remote.paused_until() == until
    assert pc.post("/api/remote/pause", json={"hours": 0}).json() == {"paused_until": 0}
    assert remote.paused_until() == 0

# ---------------------------------------------------------------- pairing


def test_pairing_window_route(pc, monkeypatch, remote_events):
    assert pc.post("/api/remote/pairing", json={"open": True}).status_code == 409  # remote access is off
    enable_remote(monkeypatch)
    r = pc.post("/api/remote/pairing", json={"open": True}).json()
    assert r["open"] is True and r["until"] == pytest.approx(remote._now() + 600, abs=5)
    assert r["url"] == f"https://{REMOTE_HOST}/"
    assert pc.post("/api/remote/pairing", json={"open": False}).json() == {
        "open": False, "until": 0, "url": f"https://{REMOTE_HOST}/"}
    assert remote_events.count(("remote", {"kind": "state"})) == 2


def test_pairing_from_request_to_device(pc, monkeypatch, remote_events):
    enable_remote(monkeypatch)
    monkeypatch.setattr(tailscale, "whois", lambda ip: {"node_id": "nNODE1", "addresses": [IP, "fd7a:115c:a1e0::1234"],
                                                        "host_name": "iphone<b>-de-test", "os": "iOS",
                                                        "login": LOGIN})
    pc.post("/api/remote/pairing", json={"open": True})
    phone = remote_client()
    asked = phone.post("/api/remote/pair-request", json={"name": "<i>Mon iPhone</i>"},
                       headers={"User-Agent": "Mozilla/5.0 (iPhone)"})
    assert asked.status_code == 200
    body = asked.json()
    assert set(body) == {"request_id", "code", "expires_in"} and len(body["code"]) == 4 and body["code"].isdigit()
    assert 590 <= body["expires_in"] <= 600
    (listed,) = pc.get("/api/remote/pair-requests").json()
    assert set(listed) == {"id", "code", "name", "ip", "login", "os", "host_name", "created", "expires_in", "status"}
    assert listed == {**listed, "id": body["request_id"], "code": body["code"], "name": "iMon iPhonei", "ip": IP,
                      "login": LOGIN, "os": "iOS", "host_name": "iphoneb-de-test", "status": "waiting"}
    assert any(k == "remote" and d["kind"] == "pair_request" and d["request"]["code"] == body["code"]
               for k, d in remote_events)
    assert ("warning", {"kind": "remote", "plain": True,
                        "text": f"Demande d'association : code {body['code']} (iOS, {IP})."}) in remote_events
    assert phone.get("/api/remote/pair-status").json() == {"status": "waiting", "code": body["code"]}
    assert devices.active() == []
    allowed = pc.post(f"/api/remote/pair-requests/{body['request_id']}/allow", json={"name": "Téléphone"}).json()
    assert allowed["ok"] is True and allowed["request"]["status"] == "approved"
    assert allowed["request"]["name"] == "Téléphone"
    assert devices.active() == []  # nothing exists before the phone collects its secret
    assert phone.get("/api/remote/pair-status").json() == {"status": "approved", "code": body["code"]}
    (device,) = devices.active()
    assert device["name"] == "Téléphone" and device["node_id"] == "nNODE1"
    assert device["ips"] == [IP, "fd7a:115c:a1e0::1234"] and device["os"] == "iOS"
    assert device["ua"] == "Mozilla/5.0 (iPhone)" and device["paired_seq"] > 0
    assert ("remote", {"kind": "pair_done"}) in remote_events
    assert phone.get("/api/remote/pair-status").json() == {"status": "done"}
    assert pc.get("/api/remote/devices").json() == [devices.public(devices.get(device["id"]))]
    assert pc.get("/api/remote/pair-requests").json()[0]["status"] == "done"
    assert phone.get("/").status_code == 200


def test_allow_and_deny_rules(pc, monkeypatch):
    enable_remote(monkeypatch)
    pc.post("/api/remote/pairing", json={"open": True})
    first = remote_client(ip="100.64.0.1").post("/api/remote/pair-request").json()
    second = remote_client(ip="100.64.0.2").post("/api/remote/pair-request").json()
    assert first["code"] != second["code"]
    # Two waiting: the code shown on the iPhone must be typed.
    for body in ({}, {"code": second["code"]}, {"code": "x"}):
        r = pc.post(f"/api/remote/pair-requests/{first['request_id']}/allow", json=body)
        assert r.status_code == 409 and r.json()["detail"] == remote.T_CODE
    assert pc.post(f"/api/remote/pair-requests/{first['request_id']}/allow",
                   json={"code": first["code"]}).status_code == 200
    assert pc.post(f"/api/remote/pair-requests/{first['request_id']}/allow").status_code == 409  # already
    assert pc.post(f"/api/remote/pair-requests/{second['request_id']}/deny").json() == {"ok": True}
    denied = remote_client(ip="100.64.0.2")
    assert pc.post("/api/remote/pair-requests/r_000000000000/allow").status_code == 404
    assert pc.post("/api/remote/pair-requests/r_000000000000/deny").status_code == 404
    assert denied.get("/api/remote/pair-status").status_code == 401  # this client has no pairing cookie


def test_devices_rename_revoke_and_forget(pc, monkeypatch, remote_events):
    phone, device, _ = paired_client(monkeypatch)
    renamed = pc.patch(f"/api/remote/devices/{device['id']}", json={"name": "<b>Cuisine</b>"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "bCuisineb"
    assert "secret_sha256" not in renamed.text
    assert pc.patch("/api/remote/devices/d_0000000000000000", json={"name": "x"}).status_code == 404
    assert pc.delete("/api/remote/devices/d_0000000000000000").status_code == 404
    assert ("remote", {"kind": "devices"}) in remote_events
    # The phone forgets itself: revoked at once, its cookie cleared.
    r = phone.post("/api/remote/forget")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "__Host-jarvis=; Max-Age=0" in r.headers["set-cookie"]
    assert devices.is_revoked(device["id"])
    other, second, _ = paired_client(monkeypatch, name="Second")
    assert pc.delete(f"/api/remote/devices/{second['id']}").json() == {"ok": True, "cancelled_tasks": 0}
    assert other.get("/api/config").status_code == 401


def test_complet_route_and_audit_route(pc, monkeypatch):
    enable_remote(monkeypatch)
    until = pc.post("/api/remote/complet", json={"duration": "24h"}).json()["complet_until"]
    assert until == pytest.approx(remote._now() + 86400, abs=5) and remote.complet_allowed()
    assert pc.post("/api/remote/complet", json={"duration": "1an"}).status_code == 400
    assert pc.post("/api/remote/complet", json={"duration": "never"}).json() == {"complet_until": 0}
    assert not remote.complet_allowed()
    for n in range(5):
        audit.event(remote.PC, "state", count=n)
    lines = pc.get("/api/remote/audit", params={"limit": 3}).json()["lines"]
    assert [line.get("count") for line in lines] == [4, 3, 2]
    assert pc.get("/api/remote/audit", params={"limit": 0}).status_code == 422
    assert pc.get("/api/remote/audit", params={"limit": 201}).status_code == 422
    assert len(pc.get("/api/remote/audit").json()["lines"]) <= 50

# ---------------------------------------------------------------- who may call what


def test_iphone_only_routes_refuse_the_pc(pc, monkeypatch):
    enable_remote(monkeypatch)
    pc.post("/api/remote/pairing", json={"open": True})
    for method, path in (("POST", "/api/remote/pair-request"), ("GET", "/api/remote/pair-status"),
                         ("POST", "/api/remote/forget")):
        r = pc.request(method, path, json={} if method == "POST" else None)
        assert r.status_code == 403 and r.json()["detail"] == "Réservé à l'iPhone.", path
    assert remote.pairing_requests() == [] and devices.active() == []


def test_pc_only_handlers_refuse_a_remote_caller_even_past_the_table(monkeypatch):
    """Defence in depth: should the route table ever let one through, the handler says no."""
    from remote_helpers import as_caller
    phone = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip=IP, login=LOGIN)
    as_caller(monkeypatch, phone)
    client = remote_client()
    for method, path, body in (("POST", "/api/remote/state", {"enabled": True}),
                               ("POST", "/api/remote/pairing", {"open": True}),
                               ("GET", "/api/remote/pair-requests", None),
                               ("POST", "/api/remote/pair-requests/r_000000000000/allow", {}),
                               ("POST", "/api/remote/pair-requests/r_000000000000/deny", None),
                               ("GET", "/api/remote/devices", None),
                               ("PATCH", "/api/remote/devices/d_0123456789abcdef", {"name": "x"}),
                               ("DELETE", "/api/remote/devices/d_0123456789abcdef", None),
                               ("POST", "/api/remote/complet", {"duration": "7d"}),
                               ("GET", "/api/remote/audit", None)):
        r = client.request(method, path, json=body)
        assert r.status_code == 403 and r.json()["detail"] == "Réservé au PC.", path
    assert not remote.complet_allowed()


def test_the_pairing_page_can_say_why_while_off_paused_or_refused(monkeypatch):
    """The guard answers off, paused and a refused login with pair.html: the four
    files that page loads are served then (public, no credential read), and
    nothing else is."""
    enable_remote(monkeypatch)

    def check(client, state):
        page = client.get("/")
        assert page.status_code == 403 and f'content="{state}"' in page.text, state
        for path in remote.PAIR_ASSETS:
            r = client.get(path)
            assert r.status_code == 200 and r.headers["content-security-policy"] == remote.CSP, (state, path)
        for path in ("/static/js/main.js", "/static/js/core.js", "/healthz", "/api/config"):
            assert client.get(path).status_code == 403, (state, path)

    check(remote_client(login="autre@example.com"), "refused")
    remote.pause(1, by=remote.PC)
    check(remote_client(), "paused")
    remote.set_enabled(False)
    check(remote_client(), "off")
    # The Serve name, the forwarded scheme and the tailnet address still apply to them.
    for kw in ({"host": "evil.example"}, {"ip": "8.8.8.8"}):
        assert remote_client(**kw).get(remote.PAIR_ASSETS[0]).status_code == 403, kw
