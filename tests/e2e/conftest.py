"""End-to-end harness: the real app on a free port in a background thread,
driven in Chromium by Playwright, with fakes for everything that would leave
this PC (WebRTC, speech recognition, OpenAI, Claude Code, ntfy, Tailscale).

The paired iPhone (remote_page) reaches the same app through a second listener
that plays Tailscale Serve without TLS (FakeServe): the real classifier sees
the Serve port, nothing is injected into the browser's own headers.

    pip install -r requirements-dev.txt && python -m playwright install chromium
    pytest -m e2e tests/e2e

Set JARVIS_E2E_CHROMIUM to use an already installed Chromium.
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fakes import FAKE_CLAUDE, FAKE_RTC, FAKE_SR, SDP_ANSWER

CHROMIUM = os.environ.get("JARVIS_E2E_CHROMIUM", "/opt/pw-browsers/chromium")

# Fictitious names only: the repository is public.
REMOTE_HOST, LOGIN, IP = "jarvis-pc.tail0000.ts.net", "monsieur@example.com", "100.101.102.103"
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1")
E2E_DEVICE = "d_e2e0000000000001"


def _no_network(request):
    raise httpx.ConnectError("pas de réseau dans les tests", request=request)


def _no_tailscale(args, timeout):
    raise FileNotFoundError("tailscale")


def _free_port(*taken) -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in taken:
            return port


def _reset_remote_memories():
    from jarvis import audit, notify, raccourci, remote, tailscale
    for mod in (remote, audit, notify, raccourci, tailscale):
        getattr(mod, "reset_memory", lambda: None)()


class FakeServe:
    """tailscale serve minus TLS: the Host and Origin the phone used, plus Serve's headers."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            drop = (b"host", b"origin", b"forwarded", b"via", b"x-real-ip")
            had_origin = any(k == b"origin" for k, _ in scope["headers"])
            h = [(k, v) for k, v in scope["headers"]
                 if k not in drop and not k.startswith((b"x-forwarded-", b"tailscale-"))]
            h += [(b"host", REMOTE_HOST.encode()), (b"x-forwarded-for", IP.encode()),
                  (b"x-forwarded-proto", b"https"), (b"x-forwarded-host", REMOTE_HOST.encode()),
                  (b"tailscale-user-login", LOGIN.encode())]
            if had_origin:
                h.append((b"origin", f"https://{REMOTE_HOST}".encode()))
            scope = {**scope, "headers": h}
        await self.app(scope, receive, send)


def _start(srv, name: str) -> threading.Thread:
    thread = threading.Thread(target=srv.run, daemon=True, name=name)
    thread.start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, f"le serveur {name} n'a pas démarré"
    return thread


@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    """JARVIS with a fake OpenAI (every minted session is recorded) and a fake claude."""
    import uvicorn

    import server
    from jarvis import (
        config,
        health,
        info,
        listener,
        notify,
        raccourci,
        realtime,
        tailscale,
        tasks,
    )

    data = tmp_path_factory.mktemp("jarvis-data")
    fake_claude = data / "fake_claude.py"
    fake_claude.write_text(FAKE_CLAUDE, encoding="utf-8")
    sessions = []

    def fake_mint(recent="", scope="pc"):
        sessions.append(realtime.session_payload(recent, scope=scope))
        return {"value": "ek_fake"}

    mp = pytest.MonkeyPatch()
    # Never the real key, never the real data folder, no morning briefing.
    mp.setenv("OPENAI_API_KEY", "sk-fake")
    mp.setenv("JARVIS_DATA_DIR", str(data))
    mp.setattr(config, "OPENAI_API_KEY", "sk-fake")
    mp.setattr(config, "DATA_DIR", data)
    mp.setattr(config, "WORKDIR", str(data))
    mp.setattr(config, "BRIEFING_TIME", "")
    mp.setattr(config, "QUIET_HOURS", "")  # same behaviour at any hour (tests set it when needed)
    mp.setattr(realtime, "mint", fake_mint)
    mp.setattr(tasks, "claude_command", lambda: [sys.executable, str(fake_claude)])
    # Not monsieur's real A.R.E.S nor the internet (test_journal_ui brings fakes).
    mp.setattr(config, "ARES", "off")
    mp.setattr(info, "TRANSPORT", httpx.MockTransport(_no_network))
    # An onboarded user with nothing to fix (each test has a fresh data folder),
    # or the Mise en route would open over every test and the health check would
    # reach OpenAI: test_settings_ui puts the real ones back where it needs them.
    mp.setattr(health, "onboarded", lambda: True)
    mp.setattr(health, "run_checks", lambda refresh=False: [])
    # Never ntfy, OpenAI (Siri) or a real Tailscale.
    mp.setattr(notify, "TRANSPORT", httpx.MockTransport(_no_network))
    mp.setattr(notify, "RETRY_S", ())
    mp.setattr(raccourci, "TRANSPORT", httpx.MockTransport(_no_network))
    mp.setattr(tailscale, "RUN", _no_tailscale)
    mp.setattr(tailscale, "exe_path", lambda: None)
    # The Serve port is the FakeServe listener's: a test that switches remote
    # access on never binds a second socket of its own.
    remote_port = _free_port()
    mp.setattr(config, "REMOTE_PORT", remote_port)
    mp.setattr(config, "REMOTE_HOST", REMOTE_HOST)
    mp.setattr(config, "REMOTE_LOGINS", LOGIN)
    mp.setattr(listener, "start", lambda: {"running": True, "port": remote_port, "error": ""})
    mp.setattr(listener, "stop", lambda: None)

    port = _free_port(remote_port)
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = _start(srv, "jarvis-e2e")
    loop = srv.servers[0].get_loop()
    serve = uvicorn.Server(uvicorn.Config(FakeServe(server.app), host="127.0.0.1", port=remote_port,
                                          lifespan="off", log_config=None, log_level="warning"))
    serve_thread = _start(serve, "jarvis-e2e-serve")

    def drop_connections():
        """Cut every open connection (the page's event stream included), as a
        crashed or restarted server would."""
        def close_all():
            for conn in list(srv.server_state.connections):
                conn.transport.close()
        loop.call_soon_threadsafe(close_all)

    yield SimpleNamespace(url=f"http://127.0.0.1:{port}/", remote_url=f"http://127.0.0.1:{remote_port}/",
                          remote_port=remote_port, sessions=sessions, drop_connections=drop_connections)
    serve.should_exit = True
    srv.should_exit = True
    serve_thread.join(10)
    thread.join(10)
    mp.undo()


@pytest.fixture(autouse=True)
def _serve_settings(isolated, app_server, monkeypatch):
    """tests/conftest.py pins the remote settings to the unit tests' values before
    each test (8789, no host, no login); here they are the FakeServe listener's
    again, so the real classifier sees the phone arrive on the Serve port."""
    from jarvis import config
    monkeypatch.setattr(config, "REMOTE_PORT", app_server.remote_port)
    monkeypatch.setattr(config, "REMOTE_HOST", REMOTE_HOST)
    monkeypatch.setattr(config, "REMOTE_LOGINS", LOGIN)


@pytest.fixture(autouse=True)
def _stop_leftover_tasks():
    """A task still running must not report into the next test's page."""
    yield
    from jarvis import inbox, tasks
    for task in tasks.running():
        tasks.cancel(task["id"])
    # Nor a message it left unheard (whoever it was for): the next page would tell it on load.
    for item in inbox.pending(via=None):
        inbox.ack(item["id"])
    _reset_remote_memories()


# ---------------------------------------------------------------- pytest-playwright settings

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    args = {**browser_type_launch_args,
            "args": ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]}
    if Path(CHROMIUM).exists():
        args["executable_path"] = CHROMIUM
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:  # the CDN libraries load through it; the app itself never does
        args["proxy"] = {"server": proxy, "bypass": "127.0.0.1,localhost"}
    return args


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "ignore_https_errors": True,
            "permissions": ["microphone", "camera"], "viewport": {"width": 1440, "height": 900}}


# ---------------------------------------------------------------- the page

def wait_ready(page):
    """Every module started and the live event stream connected (panels loaded)."""
    page.wait_for_function("window.__jarvis && __jarvis.state.ready && __jarvis.state.synced")


@pytest.fixture
def jarvis(request):
    """The JARVIS page with every fake in place. Fails the test on any page error."""
    pytest.importorskip("pytest_playwright", reason="pip install -r requirements-dev.txt")
    app_server = request.getfixturevalue("app_server")
    context = request.getfixturevalue("context")
    context.set_default_timeout(10_000)
    context.add_init_script(FAKE_RTC + FAKE_SR)
    context.route("https://api.openai.com/**", lambda route: route.fulfill(
        status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(app_server.url)
    wait_ready(page)
    yield page
    assert not errors, f"erreurs dans la page : {errors}"


def phone_origin() -> str:
    """The origin of the iPhone remote_page paired. Under the real gate it is the
    device devices.add created, with a random id; in fake-guard mode the
    device record remote_page wrote."""
    from jarvis import devices
    [device] = devices.active()
    return f"app:{device['id']}"


@pytest.fixture
def remote_page(request, app_server, monkeypatch):
    """A page the server treats as the paired iPhone.
    param (optional dict): {"device": "d_e2e0000000000001", "ios": True, "size": (390, 844),
                            "pair_state": None, "real_gate": True, "bypass_csp": True}

    Real-gate mode (the default since the remote core is merged) fakes nothing
    about the guard: the device is a real one (devices.add, a random id) with its
    __Host- cookie, and a pair_state is the real server state that leads to it
    (window open, closed, remote off, paused, a login Serve no longer allows,
    a locked address, a revoked device). Fake-guard mode ("real_gate": False)
    stamps the caller from the e2e_device cookie and renders pair_state as is."""
    pytest.importorskip("pytest_playwright", reason="pip install -r requirements-dev.txt")
    from fastapi.responses import HTMLResponse

    from jarvis import config, remote, store
    from jarvis import page as pages

    browser = request.getfixturevalue("browser")
    opts = {"device": E2E_DEVICE, "ios": True, "size": (390, 844), "pair_state": None, "real_gate": True,
            "bypass_csp": True, **(getattr(request, "param", None) or {})}
    device, pair_state = opts["device"], opts["pair_state"]
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)  # the real check_voice lets the phone talk
    if opts["real_gate"]:
        from jarvis import devices
        assert remote.READY is True  # since C2: only remote.json's switch (written here) opens the door
        store.save("remote.json", {"enabled": pair_state != "off", "host": REMOTE_HOST, "logins": [LOGIN],
                                   "paused_until": time.time() + 3600 if pair_state == "paused" else 0,
                                   "complet_until": 0, "published": False, "changed_at": time.time(),
                                   "changed_by": "pc"})
        cookie = None
        if pair_state in (None, "revoked"):
            dev, secret = devices.add("iPhone de test", ip=IP, login=LOGIN, os="iOS", ips=(IP,))
            cookie = {"name": "__Host-jarvis", "value": f"{dev['id']}.{secret}", "domain": "127.0.0.1",
                      "path": "/", "secure": True, "httpOnly": True, "sameSite": "Strict"}
            if pair_state == "revoked":
                devices.revoke(dev["id"])
        elif pair_state == "pair":
            remote.open_pairing()
        elif pair_state == "refused":  # Serve vouches for a login the PC does not allow
            monkeypatch.setattr(config, "REMOTE_LOGINS", "autre@example.com")
        elif pair_state == "locked":  # this address failed too often
            for _ in range(remote.FAIL_MAX):
                remote._fail(("ip", IP), IP)
    else:
        # The device record the remote core will know (it reads devices.json).
        store.save("devices.json", {"version": 1, "revoked_ids": [], "devices": [{
            "id": device, "name": "iPhone de test", "kind": "app", "secret_sha256": "0" * 64,
            "login": LOGIN, "ip": IP, "ips": [IP], "node_id": "", "os": "iOS", "host_name": "iphone-de-test",
            "ua": "", "paired_at": time.time(), "paired_seq": 0, "last_seen": None, "revoked": False,
            "revoked_at": None, "siri_keys": []}]})

        async def fake_guard(req, call_next):
            paired = req.cookies.get("e2e_device", "")
            if pair_state or not paired:
                req.state.caller = remote.Caller(kind="unpaired", ip=IP, login=LOGIN)
            else:
                req.state.caller = remote.Caller(kind="app", device_id=paired, ip=IP, login=LOGIN,
                                                 name="iPhone de test")
            return await call_next(req)

        def fake_page(req, caller):
            if caller.kind == "unpaired":
                return HTMLResponse(pages.pairing_html(pair_state), headers={"Cache-Control": "no-store"})
            return HTMLResponse(pages.index_html("e2e-remote-token", remote=True, origin=caller.origin),
                                headers={"Cache-Control": "no-store"})

        monkeypatch.setattr(remote, "remote_guard", fake_guard)
        monkeypatch.setattr(remote, "render_remote_page", fake_page)
        cookie = {"name": "e2e_device", "value": device, "domain": "127.0.0.1", "path": "/"}
    width, height = opts["size"]
    # new_context does not apply browser_context_args: everything is given here.
    # The real gate sends the remote CSP (no 'unsafe-eval', no inline script), which
    # would break Playwright's string waits (new Function) and axe's injected script,
    # this fixture's wait_ready included: a proof of the page under the CSP opens its
    # own context without the bypass and polls with page.evaluate
    # (tests/e2e/test_protections_remote_ui.py::test_remote_pages_run_under_the_csp_holds).
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=True, is_mobile=True,
                                  user_agent=IPHONE_UA if opts["ios"] else None, permissions=["microphone"],
                                  ignore_https_errors=True, bypass_csp=opts["bypass_csp"])
    context.set_default_timeout(10_000)
    context.add_init_script(FAKE_RTC + FAKE_SR)
    context.route("https://api.openai.com/**", lambda route: route.fulfill(
        status=201, body=SDP_ANSWER, headers={"Content-Type": "application/sdp"}))
    if cookie:
        context.add_cookies([cookie])  # one device per browser context
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    try:
        page.goto(app_server.remote_url)
        if pair_state:
            page.wait_for_load_state("load")
        else:
            wait_ready(page)
        yield page
    finally:
        context.close()
    assert not errors, f"erreurs dans la page : {errors}"


@pytest.fixture
def reload_jarvis(jarvis):
    """Reload the page and wait until it is ready again."""
    def reload():
        jarvis.reload()
        wait_ready(jarvis)
    return reload
