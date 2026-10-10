"""The phone harness (spec 3.17): remote_page is the paired iPhone, reaching the
real server through the listener that plays Tailscale Serve, while the PC page
stays the PC's. The real classifier decides, by the port the request came in on."""
import httpx
import pytest
from starlette.requests import Request

pytestmark = pytest.mark.e2e


def test_remote_page_is_the_paired_iphone(remote_page):
    state = remote_page.evaluate("({remote: __jarvis.state.remote, origin: __jarvis.state.origin})")
    assert state == {"remote": True, "origin": "app:d_e2e0000000000001"}
    # The server says the same in /api/config, and a phone never listens for the wake word.
    config = remote_page.evaluate("__jarvis.state.config")
    assert config["remote"] is True and config["origin"] == "app:d_e2e0000000000001"
    assert config["wake_word"] is False
    assert remote_page.evaluate("navigator.userAgent").find("iPhone") > 0


def test_the_pc_page_stays_the_pcs(jarvis):
    state = jarvis.evaluate("({remote: __jarvis.state.remote, origin: __jarvis.state.origin})")
    assert state == {"remote": False, "origin": "pc"}
    assert jarvis.evaluate("__jarvis.state.config.remote") is False
    assert jarvis.evaluate("__jarvis.state.config.origin") == "pc"


def test_the_serve_listener_is_remote_by_its_port(app_server, monkeypatch):
    from jarvis import config, remote, security

    seen = []
    gate = remote.remote_guard

    async def watching(request, call_next):
        # The same request stripped of every header is still remote: the port decides.
        bare = Request({**request.scope, "headers": []})
        seen.append((request.scope["server"][1], remote.is_remote_request(bare)))
        return await gate(request, call_next)

    monkeypatch.setattr(remote, "remote_guard", watching)
    with httpx.Client(trust_env=False, timeout=10) as client:  # never through a proxy
        for path in ("", "api/config"):
            r = client.get(app_server.remote_url + path, headers={"X-Jarvis-Token": security.TOKEN})
            # The gate of this version refuses every remote request, and never hands out the PC token.
            assert r.status_code == 403 and security.TOKEN not in r.text, path
        assert client.get(app_server.url + "healthz").json() == {"app": "jarvis"}  # the PC port: local
    assert seen == [(config.REMOTE_PORT, True)] * 2
