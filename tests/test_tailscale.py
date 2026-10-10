"""The Tailscale helper (spec 3.8, 4.14) against fakes: what `tailscale status`,
`whois` and `serve status` print (shapes of the 1.8x CLI, fictitious names),
the Serve states in their order, « Publier sur Tailscale » with a fake
process, the executable lookup, the watch and the three routes. Nothing here
runs a real Tailscale."""
import io
import json
import subprocess
import time
import types

import pytest
from fastapi.testclient import TestClient
from remote_helpers import as_caller, remote_client

import server
from jarvis import audit, config, remote, security, tailscale

REAL_EXE_PATH = tailscale.exe_path  # tests/conftest.py fakes it to "absent" before each test
EXE = r"C:\Program Files\Tailscale\tailscale.exe"
BASE = "http://127.0.0.1:8788"
AUTH = {"X-Jarvis-Token": security.TOKEN}
DNS = "jarvis-pc.tail0000.ts.net"

# `tailscale status --json` (ipnstate.Status), trimmed to what JARVIS reads.
STATUS = {
    "Version": "1.88.3-t0000000-g0000000",
    "BackendState": "Running",
    "Self": {"ID": "nJARVIS000CNTRL", "HostName": "jarvis-pc", "DNSName": f"{DNS}.", "OS": "windows",
             "UserID": 1234567890, "TailscaleIPs": ["100.100.1.2", "fd7a:115c:a1e0::5678"]},
    "MagicDNSSuffix": "tail0000.ts.net",
    "CurrentTailnet": {"Name": "monsieur@example.com", "MagicDNSSuffix": "tail0000.ts.net", "MagicDNSEnabled": True},
    "CertDomains": [DNS],
    "User": {"1234567890": {"ID": 1234567890, "LoginName": "Monsieur@Example.com", "DisplayName": "Monsieur"}},
}
STOPPED = {**STATUS, "BackendState": "Stopped"}
# `tailscale whois --json 100.101.102.103` (apitype.WhoIsResponse).
WHOIS = {
    "Node": {"ID": 4242, "StableID": "nIPHONE000CNTRL", "Name": "iphone-de-test.tail0000.ts.net.",
             "Addresses": ["100.101.102.103/32", "fd7a:115c:a1e0::1234/128"],
             "Hostinfo": {"OS": "iOS", "Hostname": "iPhone"}, "ComputedName": "iphone-de-test"},
    "UserProfile": {"ID": 1234567890, "LoginName": "monsieur@example.com", "DisplayName": "Monsieur"},
}
# `tailscale serve status --json` (ipn.ServeConfig), one per state.
OURS = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8789"}}}
SERVE = {
    "ready": {"TCP": {"443": {"HTTPS": True}}, "Web": {f"{DNS}:443": OURS}},
    "absent": {},
    "funnel": {"TCP": {"443": {"HTTPS": True}}, "Web": {f"{DNS}:443": OURS}, "AllowFunnel": {f"{DNS}:443": True}},
    "tcp": {"TCP": {"443": {"TCPForward": "127.0.0.1:8788"}}},
    "tcp_tls": {"TCP": {"5432": {"TCPForward": "127.0.0.1:8788", "TerminateTLS": DNS}}},
    "wrong_target": {"TCP": {"443": {"HTTPS": True}},
                     "Web": {f"{DNS}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8788"}}}}},
    "wrong_path": {"TCP": {"443": {"HTTPS": True}},
                   "Web": {f"{DNS}:443": {"Handlers": {"/": OURS["Handlers"]["/"],
                                                        "/admin": {"Proxy": "http://127.0.0.1:8788"}}}}},
    "other_port": {"TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
                   "Web": {f"{DNS}:443": OURS, f"{DNS}:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8788"}}}}},
    "foreground_funnel": {"Foreground": {"a1b2": {"TCP": {"443": {"HTTPS": True}}, "Web": {f"{DNS}:443": OURS},
                                                  "AllowFunnel": {f"{DNS}:443": True}}}},
    "funnel_and_tcp": {"TCP": {"443": {"TCPForward": "127.0.0.1:8788"}}, "AllowFunnel": {f"{DNS}:443": True}},
}


class FakeTailscale:
    """tailscale.RUN: answers like the CLI and records every argument list."""

    def __init__(self, status=STATUS, serve="ready", whois=WHOIS):
        self.status, self.serve, self.whois = status, SERVE.get(serve, serve), whois
        self.calls = []
        self.off_code = 0

    def __call__(self, args, timeout):
        self.calls.append((list(args), timeout))
        code, out = 0, ""
        if args == ["status", "--json"]:
            out = json.dumps(self.status)
        elif args == ["serve", "status", "--json"]:
            if isinstance(self.serve, Exception):
                raise self.serve
            out = json.dumps(self.serve)
        elif args[:2] == ["whois", "--json"]:
            code, out = (0, json.dumps(self.whois)) if self.whois else (1, "")
        elif args == ["serve", "--https=443", "off"]:
            code = self.off_code
            if code == 0:
                self.serve = {}
        return subprocess.CompletedProcess(args, code, out, "error: https://evil.example/x" if code else "")


@pytest.fixture
def ts(monkeypatch):
    fake = FakeTailscale()
    monkeypatch.setattr(tailscale, "RUN", fake)
    monkeypatch.setattr(tailscale, "exe_path", lambda: EXE)
    tailscale.reset_memory()
    yield fake
    tailscale.reset_memory()


class FakeProcess:
    """A publish process: its output, then still running (code None) or ended."""

    def __init__(self, lines, code=None):
        self.stdout = io.StringIO("".join(lines))
        self.code = code
        self.killed = False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("tailscale", timeout)
        return self.code

    def kill(self):
        self.killed = True
        self.code = -9


def fake_subprocess(monkeypatch, *processes):
    """tailscale's own `subprocess` name: Popen hands out the given processes and
    records each command line with its keywords."""
    seen = []
    queue = list(processes)

    def popen(cmd, **kw):
        seen.append((cmd, kw))
        return queue.pop(0)
    ns = types.SimpleNamespace(**{k: getattr(subprocess, k) for k in (
        "PIPE", "STDOUT", "DEVNULL", "TimeoutExpired", "SubprocessError", "CompletedProcess")})
    ns.Popen = popen
    monkeypatch.setattr(tailscale, "subprocess", ns)
    return seen


CONSENT_LINES = ["\n", "Serve is not enabled on your tailnet.\n", "To enable, visit:\n", "\n",
                 "         https://login.tailscale.com/f/serve?node=nJARVIS000CNTRL\n"]

# ---------------------------------------------------------------- reading


def test_self_info_reads_status_json(ts):
    info = tailscale.self_info()
    assert info == {"installed": True, "running": True, "dns_name": DNS, "login": "monsieur@example.com",
                    "tailnet": "tail0000.ts.net", "magicdns": True, "https": True, "ip": "100.100.1.2",
                    "ips": ["100.100.1.2", "fd7a:115c:a1e0::5678"]}
    tailscale.self_info()
    assert [c[0] for c in ts.calls] == [["status", "--json"]]  # cached 30 s


def test_self_info_without_tailscale_or_with_garbage(monkeypatch, ts):
    monkeypatch.setattr(tailscale, "exe_path", lambda: None)
    assert tailscale.self_info() == {"installed": False, "running": False, "ips": []}
    assert ts.calls == []
    tailscale.reset_memory()
    monkeypatch.setattr(tailscale, "exe_path", lambda: EXE)
    monkeypatch.setattr(tailscale, "RUN", lambda args, timeout: subprocess.CompletedProcess(args, 1, "{oops", ""))
    assert tailscale.self_info() == {"installed": True, "running": False, "ips": []}


def test_whois_reads_the_node_and_its_owner(ts):
    assert tailscale.whois("100.101.102.103") == {
        "node_id": "nIPHONE000CNTRL", "addresses": ["100.101.102.103", "fd7a:115c:a1e0::1234"],
        "host_name": "iphone-de-test", "os": "iOS", "login": "monsieur@example.com"}
    assert tailscale.whois("fd7a:115c:a1e0::1234")["node_id"] == "nIPHONE000CNTRL"
    tailscale.whois("100.101.102.103")
    runs = [c for c in ts.calls if c[0][0] == "whois"]
    assert [c[0] for c in runs] == [["whois", "--json", "100.101.102.103"], ["whois", "--json", "fd7a:115c:a1e0::1234"]]
    assert all(timeout == 5 for _, timeout in runs)


@pytest.mark.parametrize("ip", ["8.8.8.8", "127.0.0.1", "100.101.102.103; calc", "--json", "", None,
                                "100.101.102.103/32", "fd00::1", "2001:db8::1"])
def test_whois_validates_the_address_first(ts, ip):
    assert tailscale.whois(ip) == {}
    assert ts.calls == []


def test_whois_of_an_unknown_node_is_empty(ts):
    ts.whois = None
    assert tailscale.whois("100.101.102.104") == {}

# ---------------------------------------------------------------- serve status


@pytest.mark.parametrize("config_name, state", [
    ("ready", "ready"), ("absent", "absent"), ("funnel", "funnel"), ("tcp", "tcp"), ("tcp_tls", "tcp"),
    ("wrong_target", "wrong_target"), ("wrong_path", "wrong_target"), ("other_port", "wrong_target"),
    ("foreground_funnel", "funnel"), ("funnel_and_tcp", "funnel"),
])
def test_serve_status_states(ts, config_name, state):
    ts.serve = SERVE[config_name]
    out = tailscale.serve_status()
    assert out["state"] == state, config_name
    assert out["url"] == f"https://{DNS}/" and out["detail"]


def test_serve_status_wrong_target_names_the_pc_port(ts):
    ts.serve = SERVE["wrong_target"]
    detail = tailscale.serve_status()["detail"]
    assert "Cible inattendue" in detail and "8789" in detail


def test_serve_status_stopped_and_no_tailscale(monkeypatch, ts):
    ts.status = STOPPED
    assert tailscale.serve_status()["state"] == "stopped"
    assert ["serve", "status", "--json"] not in [c[0] for c in ts.calls]
    monkeypatch.setattr(tailscale, "exe_path", lambda: None)
    assert tailscale.serve_status() == {"state": "no_tailscale", "detail": "Tailscale n'est pas installé sur ce PC.",
                                        "url": ""}


def test_serve_status_unreadable_is_unknown(ts):
    ts.serve = subprocess.TimeoutExpired("tailscale", 10)
    assert tailscale.serve_status()["state"] == "unknown"


def test_serve_status_follows_the_remote_port(monkeypatch, ts):
    monkeypatch.setattr(config, "REMOTE_PORT", 8790)
    assert tailscale.serve_status()["state"] == "wrong_target"  # Serve still aims at 8789
    assert tailscale.manual_command() == "tailscale serve --bg --https=443 http://127.0.0.1:8790"

# ---------------------------------------------------------------- publish


def test_publish_returns_the_consent_link_and_keeps_waiting_in_the_background(monkeypatch, ts):
    process = FakeProcess(CONSENT_LINES)
    seen = fake_subprocess(monkeypatch, process)
    out = tailscale.publish()
    assert out == {"ok": False, "state": "consent", "consent_url": "https://login.tailscale.com/f/serve?node=nJARVIS000CNTRL",
                   "error": ""}
    assert not process.killed  # still waiting for monsieur's consent
    assert tailscale._proc["timer"] is not None and tailscale._proc["timer"].interval == 600
    cmd, kw = seen[0]
    assert cmd == [EXE, "serve", "--bg", "--https=443", "http://127.0.0.1:8789"]
    assert kw["shell"] is False and kw["stdin"] is subprocess.DEVNULL
    tailscale.reset_memory()
    assert process.killed


def test_a_second_publish_while_one_runs_is_busy(monkeypatch, ts):
    seen = fake_subprocess(monkeypatch, FakeProcess(CONSENT_LINES), FakeProcess([], code=0))
    assert tailscale.publish()["state"] == "consent"
    out = tailscale.publish()
    assert out["state"] == "busy" and out["ok"] is False and len(seen) == 1


def test_publish_success_checks_serve(monkeypatch, ts):
    ts.serve = SERVE["absent"]
    done = FakeProcess(["Available within your tailnet:\n", f"https://{DNS}/\n",
                        "|-- / proxy http://127.0.0.1:8789\n"], code=0)
    fake_subprocess(monkeypatch, done)
    ts.serve = SERVE["ready"]
    assert tailscale.publish() == {"ok": True, "state": "ready", "consent_url": "", "error": ""}
    # Finished: the next one may run.
    fake_subprocess(monkeypatch, FakeProcess([], code=0))
    assert tailscale.publish()["state"] == "ready"


@pytest.mark.parametrize("url", [
    "https://login.tailscale.com.evil.example/f/serve", "https://evil.example/login.tailscale.com/f",
    "http://login.tailscale.com/f/serve", "https://login.tailscale.com/f/<script>", "https://LOGIN.tailscale.com/x",
    "javascript:alert(1)//https://login.tailscale.com/",
])
def test_publish_never_shows_another_link(monkeypatch, ts, url):
    fake_subprocess(monkeypatch, FakeProcess(["To enable, visit:\n", f"   {url}\n"], code=1))
    out = tailscale.publish()
    assert out["state"] == "error" and out["consent_url"] == ""
    assert "://" not in out["error"] and url not in json.dumps(out)


def test_publish_error_says_the_last_line(monkeypatch, ts):
    fake_subprocess(monkeypatch, FakeProcess(["un\n", "Access denied: serve config denied\n"], code=1))
    out = tailscale.publish()
    assert out == {"ok": False, "state": "error", "consent_url": "", "error": "Access denied: serve config denied"}
    fake_subprocess(monkeypatch, FakeProcess(["x" * 500 + "\n"], code=1))
    assert len(tailscale.publish()["error"]) == 200


def test_publish_that_never_answers_is_stopped(monkeypatch, ts):
    monkeypatch.setattr(tailscale, "PUBLISH_WAIT", 0.3)

    class Silent(FakeProcess):
        def __init__(self):
            super().__init__([])
            self.stdout = iter(lambda: time.sleep(1), None)  # a quiet pipe: nothing for a second, then the end
    process = Silent()
    fake_subprocess(monkeypatch, process)
    out = tailscale.publish()
    assert out["state"] == "error" and process.killed


def test_publish_and_unpublish_without_tailscale(monkeypatch, ts):
    monkeypatch.setattr(tailscale, "exe_path", lambda: None)
    assert tailscale.publish()["state"] == "no_tailscale"
    assert tailscale.unpublish()["state"] == "no_tailscale"
    assert ts.calls == []


def test_unpublish_runs_serve_off(ts):
    assert tailscale.unpublish() == {"ok": True, "state": "absent", "error": ""}
    assert ["serve", "--https=443", "off"] in [c[0] for c in ts.calls]
    ts.off_code = 1
    out = tailscale.unpublish()
    assert out["ok"] is False and out["state"] == "error" and "://" not in out["error"]


def test_every_run_is_an_argument_list_without_a_shell(monkeypatch):
    """The real _run: [exe, *args], shell=False, stdin closed, no console window."""
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, json.dumps(STATUS), "")
    ns = types.SimpleNamespace(run=run, DEVNULL=subprocess.DEVNULL, CREATE_NO_WINDOW=0x08000000)
    monkeypatch.setattr(tailscale, "subprocess", ns)
    monkeypatch.setattr(tailscale, "RUN", None)
    monkeypatch.setattr(tailscale, "exe_path", lambda: EXE)
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    assert tailscale.self_info()["running"] is True
    cmd, kw = seen[0]
    assert cmd == [EXE, "status", "--json"]
    assert kw["shell"] is False and kw["stdin"] is subprocess.DEVNULL and kw["timeout"] == 10
    assert kw["creationflags"] == 0x08000000 and kw["capture_output"] is True


def test_manual_command_forms():
    assert tailscale.manual_command() == "tailscale serve --bg --https=443 http://127.0.0.1:8789"
    assert tailscale.manual_command(True) == \
        '& "C:\\Program Files\\Tailscale\\tailscale.exe" serve --bg --https=443 http://127.0.0.1:8789'

# ---------------------------------------------------------------- the executable


def test_exe_path_prefers_program_files_and_never_a_cmd(monkeypatch):
    monkeypatch.setattr(tailscale, "exe_path", REAL_EXE_PATH)
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    present = set()
    monkeypatch.setattr(tailscale, "_exists", lambda path: path in present)
    which = {"value": r"C:\Users\monsieur\bin\tailscale.cmd"}
    monkeypatch.setattr(tailscale.shutil, "which", lambda name: which["value"])
    assert tailscale.exe_path() is None  # a .cmd on PATH is never run
    which["value"] = r"C:\Users\monsieur\bin\tailscale.bat"
    assert tailscale.exe_path() is None
    which["value"] = r"C:\Outils\Tailscale\TAILSCALE.EXE"
    assert tailscale.exe_path() == r"C:\Outils\Tailscale\TAILSCALE.EXE"
    present.add(r"C:\Program Files (x86)\Tailscale\tailscale.exe")
    assert tailscale.exe_path() == r"C:\Program Files (x86)\Tailscale\tailscale.exe"
    present.add(r"C:\Program Files\Tailscale\tailscale.exe")
    assert tailscale.exe_path() == r"C:\Program Files\Tailscale\tailscale.exe"


def test_exe_path_elsewhere_uses_path(monkeypatch):
    monkeypatch.setattr(tailscale, "exe_path", REAL_EXE_PATH)
    monkeypatch.setattr(config, "IS_WINDOWS", False)
    monkeypatch.setattr(tailscale.sys, "platform", "linux")
    monkeypatch.setattr(tailscale.shutil, "which", lambda name: "/usr/bin/tailscale" if name == "tailscale" else None)
    assert tailscale.exe_path() == "/usr/bin/tailscale"

# ---------------------------------------------------------------- the watch


@pytest.fixture
def alerts(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "alert", lambda kind, text, caller=None, **kw: seen.append((kind, text)))
    return seen


@pytest.mark.parametrize("config_name", ["funnel", "tcp", "wrong_target"])
def test_watch_raises_serve_misconfig_even_while_remote_is_off(ts, alerts, config_name):
    ts.serve = SERVE[config_name]
    assert remote.is_enabled() is False
    tailscale._watch_once()
    assert [k for k, _ in alerts] == ["serve_misconfig"]
    assert "Réglages › Accès à distance" in alerts[0][1]


def test_watch_is_quiet_when_serve_is_fine_or_tailscale_absent(monkeypatch, ts, alerts):
    for name in ("ready", "absent"):
        ts.serve = SERVE[name]
        tailscale._watch_once()
    monkeypatch.setattr(tailscale, "exe_path", lambda: None)
    ts.serve = SERVE["funnel"]
    tailscale._watch_once()
    assert alerts == []


def test_watch_flags_a_pc_login_change_while_remote_is_on(monkeypatch, ts, alerts):
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr(remote, "logins", lambda: ["monsieur@example.com"])
    tailscale._watch_once()
    assert alerts == []
    monkeypatch.setattr(remote, "logins", lambda: ["autre@example.com"])
    tailscale._watch_once()
    assert [k for k, _ in alerts] == ["pc_login_change"]
    monkeypatch.setattr(remote, "is_enabled", lambda: False)
    tailscale._watch_once()
    assert len(alerts) == 1


def test_watch_runs_after_30_s_then_every_10_minutes(monkeypatch):
    assert (tailscale.WATCH_FIRST, tailscale.WATCH_EVERY) == (30, 600)
    runs = []
    monkeypatch.setattr(tailscale, "WATCH_FIRST", 0.05)
    monkeypatch.setattr(tailscale, "WATCH_EVERY", 0.05)
    monkeypatch.setattr(tailscale, "_watch_once", lambda: runs.append(time.monotonic()))
    start = time.monotonic()
    tailscale.start_watch()
    tailscale.start_watch()  # twice: still one thread
    try:
        end = time.time() + 5
        while len(runs) < 3 and time.time() < end:
            time.sleep(0.02)
        assert len(runs) >= 3 and runs[0] - start >= 0.04
        thread = tailscale._watch["thread"]
        assert thread.daemon and thread.name == "jarvis-tailscale-watch"
    finally:
        tailscale.stop_watch()
    assert not thread.is_alive()
    count = len(runs)
    time.sleep(0.2)
    assert len(runs) == count

# ---------------------------------------------------------------- routes


@pytest.fixture
def api():
    return TestClient(server.app, base_url=BASE, headers=AUTH)


def test_get_serve_route(api, ts):
    r = api.get("/api/remote/serve")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ready" and body["url"] == f"https://{DNS}/"
    assert body["command"] == tailscale.manual_command() and body["command_full"] == tailscale.manual_command(True)


def test_publish_route_needs_remote_access_on(monkeypatch, api, ts):
    calls = []
    monkeypatch.setattr(tailscale, "publish", lambda: calls.append(1) or {"ok": True, "state": "ready",
                                                                          "consent_url": "", "error": ""})
    r = api.post("/api/remote/serve/publish")
    assert r.status_code == 409 and "Activez d'abord" in r.json()["detail"] and calls == []


@pytest.mark.parametrize("state, noted", [("ready", [True]), ("consent", [True]), ("error", []), ("busy", [])])
def test_publish_route_notes_the_publication(monkeypatch, api, state, noted):
    notes = []
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr(remote, "note_published", notes.append)
    result = {"ok": state == "ready", "state": state, "consent_url": "", "error": ""}
    monkeypatch.setattr(tailscale, "publish", lambda: result)
    r = api.post("/api/remote/serve/publish")
    assert r.status_code == 200 and r.json() == result and notes == noted


def test_unpublish_route_notes_it(monkeypatch, api, ts):
    notes = []
    monkeypatch.setattr(remote, "note_published", notes.append)
    r = api.post("/api/remote/serve/unpublish")
    assert r.status_code == 200 and r.json()["ok"] is True and notes == [False]


def test_serve_routes_refuse_a_remote_caller(monkeypatch, ts):
    calls = []
    monkeypatch.setattr(tailscale, "publish", lambda: calls.append("publish"))
    monkeypatch.setattr(tailscale, "unpublish", lambda: calls.append("unpublish"))
    as_caller(monkeypatch, remote.Caller(kind="app", device_id="d_0123456789abcdef", ip="100.101.102.103",
                                         login="monsieur@example.com"))
    phone = remote_client(token="jeton")
    assert phone.get("/api/remote/serve").status_code == 403
    assert phone.post("/api/remote/serve/publish").status_code == 403
    assert phone.post("/api/remote/serve/unpublish").json()["detail"] == "Réservé au PC."
    assert calls == []
