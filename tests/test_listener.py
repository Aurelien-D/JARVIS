"""The Serve listener (spec 4.13): a second loopback listener on its own
pre-bound socket, started and stopped with remote access, a clash detected at
once. Free ports only: never a fixed one."""
import socket
import time

import httpx
import pytest

import server  # noqa: F401 - builds the app and hands it to listener.configure
from jarvis import audit, config, events, listener, remote


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def can_bind(port: int) -> bool:
    s = socket.socket()
    try:
        if not config.IS_WINDOWS:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@pytest.fixture
def port(monkeypatch):
    p = free_port()
    monkeypatch.setattr(config, "REMOTE_PORT", p)
    yield p
    listener.stop()


@pytest.fixture
def alerts(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "alert", lambda kind, text, caller=None, **f: seen.append((kind, text)))
    return seen


def get(port: int, path: str = "/healthz"):
    with httpx.Client(trust_env=False, timeout=5) as client:  # never through a proxy
        return client.get(f"http://127.0.0.1:{port}{path}")


def test_start_serves_the_app_on_the_serve_port_then_stop_frees_it(port):
    state = listener.start()
    assert state == {"running": True, "port": port, "error": ""}
    assert listener.state() == {"running": True, "port": port, "error": ""}
    # The same app, classified remote by its port: off, so the gate refuses.
    r = get(port)
    assert r.status_code == 403 and r.json()["detail"] == remote.T_OFF
    assert r.headers["content-security-policy"] == remote.CSP
    listener.stop()
    assert listener.state()["running"] is False
    with pytest.raises(httpx.ConnectError):
        get(port)
    assert can_bind(port)


def test_restart_makes_a_new_server_each_time(port):
    first = listener.start()
    server_one = listener._run["server"]
    assert listener.start() == first  # already running: the same state, nothing new
    assert listener._run["server"] is server_one
    listener.stop()
    again = listener.start()
    assert again["running"] is True and listener._run["server"] is not server_one
    assert get(port).status_code == 403
    listener.stop()
    assert listener.state()["running"] is False


def test_a_port_clash_is_detected_at_once(monkeypatch, alerts):
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    busy = taken.getsockname()[1]
    monkeypatch.setattr(config, "REMOTE_PORT", busy)
    try:
        started = time.monotonic()
        state = listener.start()
        assert time.monotonic() - started < 2  # synchronous: no waiting on a thread
    finally:
        taken.close()
    assert state == {"running": False, "port": busy,
                     "error": f"Port {busy} déjà utilisé : choisissez un autre JARVIS_REMOTE_PORT."}
    assert listener.state()["running"] is False
    assert alerts == [("listener_error", f"Accès à distance : le port {busy} est déjà utilisé.")]


def test_stop_when_not_running_is_harmless():
    assert listener.state()["running"] is False
    listener.stop()
    listener.stop()
    assert listener.state() == {"running": False, "port": config.REMOTE_PORT, "error": ""}


def test_stop_closes_the_remote_streams_first(port, monkeypatch):
    matches = []
    real = events.close_streams
    monkeypatch.setattr(events, "close_streams", lambda match=None: matches.append(match) or real(match))
    listener.start()
    listener.stop()
    (match,) = matches
    phone = remote.Caller(kind="app", device_id="d_0123456789abcdef")
    assert match(phone) is True and match(remote.Caller(kind="siri", key_id="k_0123456789abcdef")) is True
    assert match(None) is False and match(remote.PC) is False  # the PC's pages keep theirs


def test_start_if_enabled_follows_the_switch(port, monkeypatch):
    listener.start_if_enabled()
    assert listener.state()["running"] is False  # off (and READY False)
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    listener.start_if_enabled()
    assert listener.state()["running"] is True
    listener.stop()
