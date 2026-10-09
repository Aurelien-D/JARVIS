"""End-to-end harness: the real app on a free port in a background thread,
driven in Chromium by Playwright, with fakes for everything that would leave
this PC (WebRTC, speech recognition, OpenAI, Claude Code).

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


def _no_network(request):
    raise httpx.ConnectError("pas de réseau dans les tests", request=request)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    """JARVIS with a fake OpenAI (every minted session is recorded) and a fake claude."""
    import uvicorn

    import server
    from jarvis import config, health, info, realtime, tasks

    data = tmp_path_factory.mktemp("jarvis-data")
    fake_claude = data / "fake_claude.py"
    fake_claude.write_text(FAKE_CLAUDE, encoding="utf-8")
    sessions = []

    def fake_mint(recent=""):
        sessions.append(realtime.session_payload(recent))
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

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True, name="jarvis-e2e")
    thread.start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "le serveur JARVIS n'a pas démarré"
    loop = srv.servers[0].get_loop()

    def drop_connections():
        """Cut every open connection (the page's event stream included), as a
        crashed or restarted server would."""
        def close_all():
            for conn in list(srv.server_state.connections):
                conn.transport.close()
        loop.call_soon_threadsafe(close_all)

    yield SimpleNamespace(url=f"http://127.0.0.1:{port}/", sessions=sessions,
                          drop_connections=drop_connections)
    srv.should_exit = True
    thread.join(10)
    mp.undo()


@pytest.fixture(autouse=True)
def _stop_leftover_tasks():
    """A task still running must not report into the next test's page."""
    yield
    from jarvis import inbox, tasks
    for task in tasks.running():
        tasks.cancel(task["id"])
    # Nor a message it left unheard: the next page would tell it on load.
    for item in inbox.pending():
        inbox.ack(item["id"])


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


@pytest.fixture
def reload_jarvis(jarvis):
    """Reload the page and wait until it is ready again."""
    def reload():
        jarvis.reload()
        wait_ready(jarvis)
    return reload
