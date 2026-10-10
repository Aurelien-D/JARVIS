"""Helpers for the remote-access tests (spec 3.17): the headers Tailscale Serve
adds, a TestClient that arrives on the Serve port, a fake gate that stamps a
caller, remote access switched on and a paired iPhone through the real gate.
Fictitious names only: the repository is public."""
import re
import time

from fastapi.testclient import TestClient

import server
from jarvis import config, devices, listener, remote, store

REMOTE_HOST, LOGIN, IP = "jarvis-pc.tail0000.ts.net", "monsieur@example.com", "100.101.102.103"


def remote_headers(*, token=None, login=LOGIN, ip=IP, host=REMOTE_HOST, origin=True, extra=None) -> dict:
    """The headers Tailscale Serve adds (Host, X-Forwarded-For/Proto/Host, Tailscale-User-Login),
    plus Origin (https://<host>) when origin is True and X-Jarvis-Token when token is given."""
    headers = {"Host": host, "X-Forwarded-For": ip, "X-Forwarded-Proto": "https",
               "X-Forwarded-Host": host, "Tailscale-User-Login": login}
    if origin:
        headers["Origin"] = f"https://{host}"
    if token is not None:
        headers["X-Jarvis-Token"] = token
    headers.update(extra or {})
    return headers


def remote_client(**kw) -> TestClient:
    """TestClient(server.app, base_url=f"https://127.0.0.1:{config.REMOTE_PORT}", headers=remote_headers(**kw)).
    scope["server"] is then the remote port (arrival classification) and the cookie jar sends
    Secure __Host- cookies back (https base URL)."""
    return TestClient(server.app, base_url=f"https://127.0.0.1:{config.REMOTE_PORT}", headers=remote_headers(**kw))


def as_caller(monkeypatch, caller) -> None:
    """Patch remote.remote_guard to stamp this Caller and call_next: A2 and A3 HTTP tests before A1."""
    async def guard(request, call_next):
        request.state.caller = caller
        return await call_next(request)
    monkeypatch.setattr(remote, "remote_guard", guard)


def enable_remote(monkeypatch, *, host=REMOTE_HOST, logins=(LOGIN,), cap=5.0) -> list:
    """Remote access switched on as the PC would leave it: READY, an enabled
    remote.json with this Serve name and these logins, a daily cap, and a fake
    Serve listener (nothing is bound). Returns the listener calls ("start", "stop")."""
    calls = []
    monkeypatch.setattr(remote, "READY", True)
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", cap)
    monkeypatch.setattr(listener, "start", lambda: calls.append("start") or
                        {"running": True, "port": config.REMOTE_PORT, "error": ""})
    monkeypatch.setattr(listener, "stop", lambda: calls.append("stop"))
    store.save(remote.REMOTE_FILE, {"enabled": True, "host": host, "logins": list(logins), "paused_until": 0,
                                    "complet_until": 0, "published": False, "changed_at": time.time(),
                                    "changed_by": "pc"})
    return calls


def page_token(html: str) -> str:
    """The page token a served page carries ('' when none)."""
    match = re.search(r'<meta name="jarvis-token" content="([^"]*)">', html)
    return match.group(1) if match else ""


def paired_client(monkeypatch, name="iPhone de test", ip=IP, *, login=LOGIN, **kw):
    """A paired iPhone: a device made with devices.add (bound to ip and login),
    its cookie in the jar, the page loaded once. Returns (client, device,
    page_token); the client already sends that page token (X-Jarvis-Token).
    Switches remote access on first (enable_remote) when it is off."""
    if not remote.is_enabled():
        enable_remote(monkeypatch)
    device, secret = devices.add(name, ip=ip, login=login, os="iOS", host_name="iphone-de-test", ips=(ip,))
    client = remote_client(ip=ip, login=login, **kw)
    client.cookies.set(remote.COOKIE, f"{device['id']}.{secret}", domain="127.0.0.1", path="/")
    r = client.get("/", headers={"Sec-Fetch-Site": "none", "Sec-Fetch-Dest": "document"})
    assert r.status_code == 200, (r.status_code, r.text[:200])
    token = page_token(r.text)
    assert token, "page sans jeton"
    client.headers["X-Jarvis-Token"] = token
    return client, device, token
