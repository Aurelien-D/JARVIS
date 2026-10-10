"""Helpers for the remote-access tests (spec 3.17): the headers Tailscale Serve
adds, a TestClient that arrives on the Serve port, and a fake gate that stamps
a caller. Fictitious names only: the repository is public."""
from fastapi.testclient import TestClient

import server
from jarvis import config, remote

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
