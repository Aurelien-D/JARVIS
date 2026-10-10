"""The launcher: Quit, Ctrl+C and a second launch."""
import json
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn

import server
from jarvis import security


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_quit_is_quick_with_the_page_open(monkeypatch):
    # The page keeps its event stream open forever: Quit must not wait for it.
    port = _free_port()
    srv = server.JarvisServer(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                             log_level="warning", timeout_graceful_shutdown=3))
    monkeypatch.setattr(server, "SERVER", srv)
    run = threading.Thread(target=srv.run, daemon=True)
    run.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started
    url = f"http://127.0.0.1:{port}"  # trust_env=False: never through a proxy
    lines = []

    def listen():
        try:
            with httpx.stream("GET", f"{url}/api/events?token={security.TOKEN}", timeout=20,
                              trust_env=False) as r:
                for line in r.iter_lines():
                    lines.append(line)
        except httpx.HTTPError as exc:  # the stream was cut instead of ended
            lines.append(repr(exc))

    page = threading.Thread(target=listen, daemon=True)
    page.start()
    while not lines and time.time() < deadline:
        time.sleep(0.05)
    assert lines[0] == "retry: 3000"
    started = time.time()
    r = httpx.post(f"{url}/api/shutdown", headers={"X-Jarvis-Token": security.TOKEN}, trust_env=False)
    assert r.json() == {"ok": True}
    run.join(10)
    page.join(5)
    assert not run.is_alive() and not page.is_alive()
    assert all(not line.startswith(("RemoteProtocolError", "ReadError")) for line in lines)
    assert time.time() - started < 2.5  # streams ended, not cut after timeout_graceful_shutdown
    # The page was told first: it ends its voice session and closes its window.
    assert any(line.startswith("data: ") and json.loads(line[6:]).get("type") == "shutdown" for line in lines)


@pytest.fixture
def launcher(monkeypatch):
    """main() with no real server, window or tray: records what it did."""
    done = []
    monkeypatch.setattr(server, "SERVER", None)
    monkeypatch.setattr(server.shell, "start", lambda url, on_quit: done.append("tray"))
    monkeypatch.setattr(server.shell, "stop", lambda: done.append("tray off"))
    monkeypatch.setattr(server.desktop, "show_app_window", lambda url: done.append("show"))
    monkeypatch.setattr(server.JarvisServer, "run", lambda self: done.append("run"))

    def launch(*argv, running=False):
        monkeypatch.setattr(sys, "argv", ["server.py", *argv])
        monkeypatch.setattr(server, "_already_running", lambda url: running)
        server.main()
        return done
    return launch


def test_ctrl_c_stops_quietly(launcher, monkeypatch):
    def interrupted(self):
        raise KeyboardInterrupt  # what uvicorn re-raises after a Ctrl+C
    monkeypatch.setattr(server.JarvisServer, "run", interrupted)
    try:
        done = launcher()
    except KeyboardInterrupt:  # caught here, or it would stop pytest itself
        pytest.fail("Ctrl+C sort de main() en traceback")
    assert done == ["tray", "tray off"]
    assert server.SERVER.config.timeout_graceful_shutdown == 3


def test_second_launch_brings_the_window_back(launcher):
    assert launcher("--app", running=True) == ["show"]  # and no second server
    assert server.SERVER is None


def test_second_console_launch_just_says_so(launcher, capsys):
    assert launcher(running=True) == []
    assert "tourne déjà" in capsys.readouterr().out


def test_first_launch_runs_the_server_with_its_tray(launcher):
    assert launcher() == ["tray", "run", "tray off"]
