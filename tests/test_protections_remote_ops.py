"""Protections of the remote access operations on the PC (spec section 8, rows
29 and 30): the tray's kill switch does nothing but switch remote access,
« Publier sur Tailscale » runs only its fixed command without a shell, and the
Serve check flags Funnel, TCP forwards and a wrong target even while remote
access is off. Fake Tailscale, fake remote module: nothing real runs."""
import ast
import inspect
import io
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

import jarvis
from jarvis import (
    audit,
    config,
    confirm,
    health,
    remote,
    settings,
    shell,
    tailscale,
    tasks,
    tools,
)

EXE = r"C:\Program Files\Tailscale\tailscale.exe"
DNS = "jarvis-pc.tail0000.ts.net"
REAL_EXE_PATH = tailscale.exe_path
STATUS = {"BackendState": "Running", "Self": {"DNSName": f"{DNS}.", "UserID": 7, "TailscaleIPs": ["100.100.1.2"]},
          "User": {"7": {"LoginName": "monsieur@example.com"}}, "CertDomains": [DNS],
          "CurrentTailnet": {"MagicDNSSuffix": "tail0000.ts.net", "MagicDNSEnabled": True}}
OURS = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8789"}}}
DANGEROUS = {
    "funnel": {"TCP": {"443": {"HTTPS": True}}, "Web": {f"{DNS}:443": OURS}, "AllowFunnel": {f"{DNS}:443": True}},
    "tcp": {"TCP": {"443": {"TCPForward": "127.0.0.1:8788"}}},
    "tls_tcp": {"TCP": {"8443": {"TCPForward": "127.0.0.1:8788", "TerminateTLS": DNS}}},
    "wrong_target": {"TCP": {"443": {"HTTPS": True}},
                     "Web": {f"{DNS}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8788"}}}}},
}
STATE_OF = {"funnel": "funnel", "tcp": "tcp", "tls_tcp": "tcp", "wrong_target": "wrong_target"}


def _forbid(monkeypatch):
    def forbidden(name):
        def fail(*a, **k):
            raise AssertionError(f"{name} appelé par l'icône")
        return fail
    for owner, name in ((tools, "run_tool"), (tasks, "create_task"), (tasks, "approve"), (confirm, "decide"),
                        (confirm, "mark_turn"), (confirm, "mark_tainted"), (settings, "set_many")):
        monkeypatch.setattr(owner, name, forbidden(name))

# ---------------------------------------------------------------- 29. the tray's kill switch


class _Menu:
    SEPARATOR = None

    def __init__(self, *items):
        self.items = [i for i in items if i is not None]


class _Pystray:
    Menu = _Menu

    @staticmethod
    def MenuItem(text, action, **kw):  # pystray's own name
        return types.SimpleNamespace(text=text, action=action, checked=kw.get("checked"))


def test_tray_kill_switch_only_toggles_remote_access_holds(monkeypatch, caplog):
    """The tray item calls exactly remote.set_enabled(not remote.is_enabled(),
    by=remote.PC): a fake remote module that has nothing else proves it touches
    nothing else (no tool, task, decision, turn or setting)."""
    _forbid(monkeypatch)
    calls, notes = [], []
    on = {"value": False}

    def set_enabled(value, **kw):
        calls.append((value, kw))
        on["value"] = value
        return {}
    fake = types.SimpleNamespace(PC=remote.PC, RemoteError=remote.RemoteError, set_enabled=set_enabled,
                                 is_enabled=lambda: on["value"])
    monkeypatch.setattr(jarvis, "remote", fake)          # `from . import remote` reads the package attribute
    monkeypatch.setitem(sys.modules, "jarvis.remote", fake)
    monkeypatch.setattr(shell, "notify", lambda title, body: notes.append(body) or True)

    items = {i.text: i for i in shell.tray_menu(_Pystray).items}
    item = items["Accès à distance (activer ou couper)"]
    labels = list(items)
    assert labels.index("Accès à distance (activer ou couper)") == labels.index("Ne pas déranger 1 h") + 1

    def click():
        item.action(*([None] if inspect.signature(item.action).parameters else []))
    assert item.checked(item) is False
    click()
    assert calls == [(True, {"by": remote.PC})] and item.checked(item) is True
    click()
    assert calls[1] == (False, {"by": remote.PC}) and item.checked(item) is False
    assert notes == ["Accès à distance activé.", "Accès à distance coupé."]

    # Refusals are said, never worked around.
    def refuse(text):
        def set_enabled(value, **kw):
            calls.append((value, kw))
            raise remote.RemoteError(text)
        return set_enabled
    fake.set_enabled = refuse("Adresse Tailscale invalide (exemple : jarvis-pc.tail0000.ts.net).")
    click()
    assert notes[-1] == "Activez-le d'abord dans Réglages › Accès à distance."
    cap = ("Fixez d'abord un plafond de dépense par jour (Réglages › Coûts) : il protège votre crédit OpenAI "
           "quand JARVIS est utilisé à distance.")
    fake.set_enabled = refuse(cap)
    click()
    assert notes[-1] == cap
    assert calls == [(True, {"by": remote.PC}), (False, {"by": remote.PC}),
                                  (True, {"by": remote.PC}), (True, {"by": remote.PC})]
    assert "action de l'icône" not in caplog.text  # no AttributeError swallowed: nothing else was touched

    # In the source too: the tray reaches remote only through these names.
    tree = ast.parse(Path(shell.__file__).read_text(encoding="utf-8"))
    used = {n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "remote"}
    assert used == {"is_enabled", "set_enabled", "PC", "RemoteError"}, used


def test_the_tray_kill_switch_with_the_real_module_says_why_it_cannot(monkeypatch):
    """The real remote module (READY since C2) on a fresh install: the tray cannot
    switch remote access on before Réglages has an address, nor without a daily
    cap; it says why in French and changes nothing. With both, it switches it on
    and off, the PC's own switch."""
    from jarvis import config, listener, store
    notes = []
    monkeypatch.setattr(shell, "notify", lambda title, body: notes.append(body) or True)
    monkeypatch.setattr(listener, "start", lambda: {"running": True, "port": config.REMOTE_PORT, "error": ""})
    monkeypatch.setattr(listener, "stop", lambda: None)
    assert remote.READY is True
    shell._toggle_remote(None)
    assert notes == ["Activez-le d'abord dans Réglages › Accès à distance."]
    assert remote.is_enabled() is False and shell._remote_on() is False
    assert store.load(remote.REMOTE_FILE, None) is None  # nothing saved
    # The address and account confirmed in Réglages, but no daily cap: still off, and it says why.
    monkeypatch.setattr(config, "REMOTE_HOST", "jarvis-pc.tail0000.ts.net")
    monkeypatch.setattr(config, "REMOTE_LOGINS", "monsieur@example.com")
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0)
    shell._toggle_remote(None)
    assert notes[-1].startswith("Fixez d'abord un plafond de dépense par jour")
    assert remote.is_enabled() is False and shell._remote_on() is False
    # With a cap: the tray is the PC's own switch, on then off.
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 5.0)
    shell._toggle_remote(None)
    assert notes[-1] == "Accès à distance activé." and remote.is_enabled() and shell._remote_on()
    shell._toggle_remote(None)
    assert notes[-1] == "Accès à distance coupé." and remote.is_enabled() is False

# ---------------------------------------------------------------- 30. Publier sur Tailscale


class _Process:
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
        self.killed, self.code = True, -9


def test_publish_runs_only_the_fixed_command_without_a_shell_holds(monkeypatch):
    """Every Tailscale command JARVIS can run, through the real _run and _popen:
    an argument list from a fixed set, shell=False, stdin closed; never Funnel
    nor a TCP forward; one publish at a time; Program Files first and never a
    .cmd; only a login.tailscale.com consent link comes back."""
    runs, popens = [], []
    serve = {"value": {}}

    def run(cmd, **kw):
        runs.append((cmd, kw))
        args = cmd[1:]
        out = json.dumps(STATUS) if args == ["status", "--json"] else \
            json.dumps(serve["value"]) if args == ["serve", "status", "--json"] else \
            json.dumps({"Node": {"StableID": "nX"}}) if args[:2] == ["whois", "--json"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    processes = []

    def popen(cmd, **kw):
        popens.append((cmd, kw))
        return processes.pop(0)
    ns = types.SimpleNamespace(run=run, Popen=popen, **{k: getattr(subprocess, k) for k in (
        "PIPE", "STDOUT", "DEVNULL", "TimeoutExpired", "SubprocessError")})
    monkeypatch.setattr(tailscale, "subprocess", ns)
    monkeypatch.setattr(tailscale, "RUN", None)
    monkeypatch.setattr(tailscale, "exe_path", lambda: EXE)

    tailscale.self_info()
    tailscale.whois("100.101.102.103")
    tailscale.whois("100.101.102.103 && calc")  # refused before anything runs
    tailscale.serve_status()
    tailscale.unpublish()
    processes.append(_Process(["To enable, visit:\n", "https://login.tailscale.com/f/serve?node=nX\n"]))
    first = tailscale.publish()
    second = tailscale.publish()  # the first still waits for consent
    tailscale.reset_memory()
    processes.append(_Process(["Visit https://evil.example/login.tailscale.com/f to enable\n"], code=1))
    third = tailscale.publish()

    allowed = [["status", "--json"], ["whois", "--json", "100.101.102.103"], ["serve", "status", "--json"],
               ["serve", "--https=443", "off"]]
    for cmd, kw in runs:
        assert isinstance(cmd, list) and cmd[0] == EXE and cmd[1:] in allowed, cmd
        assert kw["shell"] is False and kw["stdin"] is subprocess.DEVNULL and kw["timeout"] <= 10
    assert len(popens) == 2  # the busy one never started a process
    for cmd, kw in popens:
        assert cmd == [EXE, "serve", "--bg", "--https=443", "http://127.0.0.1:8789"]
        assert kw["shell"] is False and kw["stdin"] is subprocess.DEVNULL
    every = " ".join(" ".join(c) for c, _ in runs + popens)
    for word in ("funnel", "--tcp", "--tls-terminated-tcp", "8788", "0.0.0.0", "--set-path"):
        assert word not in every, word
    assert first["state"] == "consent" and first["consent_url"] == "https://login.tailscale.com/f/serve?node=nX"
    assert second["state"] == "busy" and second["consent_url"] == ""
    assert third["state"] == "error" and third["consent_url"] == "" and "evil.example" not in json.dumps(third)

    # The executable: Program Files first, a .cmd or .bat on PATH never.
    monkeypatch.setattr(tailscale, "exe_path", REAL_EXE_PATH)
    monkeypatch.setattr(config, "IS_WINDOWS", True)
    monkeypatch.setattr(tailscale.shutil, "which", lambda name: r"C:\Users\monsieur\AppData\Local\bin\tailscale.cmd")
    monkeypatch.setattr(tailscale, "_exists", lambda path: False)
    assert tailscale.exe_path() is None
    monkeypatch.setattr(tailscale, "_exists", lambda path: True)
    assert tailscale.exe_path() == EXE

    # And in the source: no shell anywhere, every run an argument list.
    tree = ast.parse(Path(tailscale.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "shell":
            assert isinstance(node.value, ast.Constant) and node.value.value is False
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in ("system", "popen", "check_output", "getoutput", "getstatusoutput"), name
            if name in ("run", "Popen") and getattr(node.func, "value", None) is not None \
                    and getattr(node.func.value, "id", "") == "subprocess":
                assert isinstance(node.args[0], ast.List), ast.dump(node)
                assert any(k.arg == "shell" for k in node.keywords)
            if name == "_run":
                assert isinstance(node.args[0], ast.List), ast.dump(node)
                assert all(isinstance(e, (ast.Constant, ast.Name)) for e in node.args[0].elts)

# ---------------------------------------------------------------- 30. the Serve check


@pytest.mark.parametrize("name", list(DANGEROUS))
@pytest.mark.parametrize("enabled", [False, True])
def test_serve_health_flags_funnel_tcp_and_wrong_target_holds(monkeypatch, name, enabled):
    """Funnel, a TCP forward (with or without TLS) and a wrong target: an error in
    the health check and an alert from the 10-minute watch, whether remote
    access is on or off."""
    def fake(args, timeout):
        out = json.dumps(STATUS) if args == ["status", "--json"] else \
            json.dumps(DANGEROUS[name]) if args == ["serve", "status", "--json"] else ""
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(tailscale, "RUN", fake)
    monkeypatch.setattr(tailscale, "exe_path", lambda: EXE)
    monkeypatch.setattr(remote, "is_enabled", lambda: enabled)
    monkeypatch.setattr(remote, "logins", lambda: ["monsieur@example.com"])
    alerts = []
    monkeypatch.setattr(audit, "alert", lambda kind, text, caller=None, **kw: alerts.append(kind))

    assert tailscale.serve_status()["state"] == STATE_OF[name]
    assert health.check_remote in health.CHECKS
    [item] = health.check_remote()
    assert item["id"] == "remote" and item["level"] == "error" and item["ok"] is False
    assert "Publier sur Tailscale" in item["fix_fr"]
    assert [i["id"] for i in health.run_checks() if i["level"] == "error"].count("remote") == 1
    tailscale._watch_once()
    assert alerts == ["serve_misconfig"]
