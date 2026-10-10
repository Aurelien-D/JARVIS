"""The remote audit trail and its alerts (spec 3.6, 3.13, 4.9): one JSON line per
event with known fields only, rotation under one lock, the alerts kept apart,
dedupe per kind and device or address, fixed hook texts, refusals throttled
per address, and nothing that ever raises."""
import json
import re
import threading

import pytest

from jarvis import audit, config, desktop, events, remote

PHONE = remote.Caller(kind="app", device_id="d_0123456789abcdef", ip="100.101.102.103",
                      login="monsieur@example.com", name="iPhone de test")
EXPECTED = {  # kind: (toast, ntfy, dedupe_s, ntfy_text) as the spec's table says
    "pair_request": (True, False, 0, ""),
    "new_device": (True, True, 0, "nouvel appareil associé."),
    "funnel": (True, True, 600, "requête venue d'internet refusée (Funnel) : vérifiez Tailscale."),
    "auth_failures": (True, True, 600, "échecs répétés d'authentification : accès bloqué 15 minutes."),
    "remote_complet": (True, True, 0, "tâche avec accès complet lancée depuis l'iPhone."),
    "login_change": (True, True, 600, "compte Tailscale inattendu pour un appareil associé : refusé."),
    "ip_change": (True, True, 600, "appareil associé utilisé depuis une autre machine : refusé."),
    "secret_copied": (True, True, 0, "secret d'appareil utilisé depuis une autre machine : appareil retiré."),
    "siri_key": (True, False, 0, ""),
    "remote_paused": (True, False, 0, ""),
    "remote_on": (True, True, 0, "accès à distance activé sur le PC."),
    "remote_off": (True, True, 0, "accès à distance coupé sur le PC."),
    "complet_optin": (True, True, 0, "accès complet depuis l'iPhone autorisé sur le PC."),
    "listener_error": (True, False, 600, ""),
    "serve_misconfig": (True, True, 3600,
                        "Tailscale publie JARVIS d'une façon dangereuse : vérifiez Réglages › Accès à distance."),
    "pc_login_change": (True, True, 3600, "le compte Tailscale du PC a changé."),
}


@pytest.fixture
def seen(monkeypatch):
    """What reached the PC (toasts, PC-only events) and the hooks."""
    out = {"toasts": [], "pc": [], "hooks": []}
    monkeypatch.setattr(desktop, "toast", lambda title, body: out["toasts"].append((title, body)) or True)
    monkeypatch.setattr(events, "publish_pc", lambda kind, data: out["pc"].append((kind, data)) or 1)
    monkeypatch.setattr(audit, "ALERT_HOOKS", [lambda kind, text: out["hooks"].append((kind, text))])
    return out


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_760_000_000.0}
    monkeypatch.setattr(audit, "_now", lambda: now["t"])
    return now


def lines(name: str = "remote-audit.jsonl") -> list:
    path = config.DATA_DIR / name
    if not path.exists():
        return []
    return [json.loads(text) for text in path.read_text(encoding="utf-8").splitlines()]

# ---------------------------------------------------------------- the table


def test_alerts_table_is_the_specs():
    assert set(audit.ALERTS) == set(EXPECTED) and len(audit.ALERTS) == 16
    for kind, (toast, ntfy, dedupe, text) in EXPECTED.items():
        spec = audit.ALERTS[kind]
        assert spec == {"title": "JARVIS · sécurité", "toast": toast, "ntfy": ntfy, "dedupe_s": dedupe,
                        "ntfy_text": text}, kind
        if ntfy:  # a fixed sentence: nothing a device, a name or an address could fill in
            assert text and "«" not in text and "@" not in text and not re.search(r"\d+\.\d+", text), kind

# ---------------------------------------------------------------- lines


def test_a_line_holds_known_fields_only_and_never_a_secret(seen):
    audit.event(PHONE, "request", method="GET", route="/api/task/{task_id}", status=200,
                cookie="__Host-jarvis=d_x.SECRETVALUE", secret="SECRETVALUE", page_token="TOKENVALUE",
                url="/api/x?token=TOKENVALUE", prompt="PROMPTVALUE", title="t" * 300, text="é" * 400)
    (line,) = lines()
    assert set(line) == {"t", "kind", "caller", "device", "ip", "login", "method", "route", "status", "title",
                         "text"}
    assert line["caller"] == "app" and line["device"] == PHONE.device_id and line["route"] == "/api/task/{task_id}"
    assert len(line["title"]) == 80 and len(line["text"]) == 160
    raw = (config.DATA_DIR / "remote-audit.jsonl").read_text(encoding="utf-8")
    for secret in ("SECRETVALUE", "TOKENVALUE", "PROMPTVALUE", "__Host-jarvis", "?token"):
        assert secret not in raw
    assert seen["toasts"] == [] and seen["pc"] == []  # a plain event alerts nobody


def test_the_pc_caller_and_no_caller(seen):
    audit.event(remote.PC, "state", text="Accès à distance activé.")
    audit.event(None, "pair", decision="oui", count=2.0, status=float("nan"))
    first, second = lines()
    assert first["caller"] == "pc" and "device" not in first and "ip" not in first
    assert second == {"t": second["t"], "kind": "pair", "decision": "oui", "count": 2}


def test_nothing_ever_raises(tmp_path, monkeypatch, seen):
    blocker = tmp_path / "pas-un-dossier"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", blocker / "data")  # a file in the way: every write fails
    audit.event(PHONE, "request", status=200)
    audit.alert("funnel", "Requête refusée venant d'internet (Funnel) : vérifiez Tailscale.", PHONE)
    assert audit.tail() == []
    # The alert still reached the PC: a full disk must not hide it.
    assert seen["toasts"] and seen["pc"]


def test_tail_is_newest_first_and_reads_the_old_file_when_needed(monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_MAX_BYTES", 400)
    for n in range(12):
        audit.event(PHONE, "request", status=200, count=n)
    assert (config.DATA_DIR / "remote-audit.1.jsonl").exists()
    got = [line["count"] for line in audit.tail(6)]
    assert got == [11, 10, 9, 8, 7, 6]
    every = [line["count"] for line in audit.tail(200)]
    assert every == sorted(every, reverse=True) and every[0] == 11
    assert audit.tail(0) == []

# ---------------------------------------------------------------- rotation


def test_rotation_keeps_one_old_file(monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_MAX_BYTES", 1000)
    count = 0
    while not (config.DATA_DIR / "remote-audit.1.jsonl").exists():
        audit.event(PHONE, "request", status=200, count=count)
        count += 1
    old = lines("remote-audit.1.jsonl")
    current = lines()
    assert [x["count"] for x in old] == list(range(count - 1)) and [x["count"] for x in current] == [count - 1]
    assert (config.DATA_DIR / "remote-audit.1.jsonl").stat().st_size > 1000


def test_rotation_under_the_lock_never_tears_a_line(monkeypatch):
    """Four threads writing while the file rotates again and again: every line
    of both files is whole JSON, and no file grows past its limit by more than a line."""
    monkeypatch.setattr(audit, "AUDIT_MAX_BYTES", 4000)

    def write(worker):
        for n in range(150):
            audit.event(PHONE, "request", method="GET", route="/api/config", status=200, count=worker * 1000 + n)
    threads = [threading.Thread(target=write, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    current, old = lines(), lines("remote-audit.1.jsonl")
    assert current and old
    for path in ("remote-audit.jsonl", "remote-audit.1.jsonl"):
        assert (config.DATA_DIR / path).stat().st_size < 4000 + 400
    counts = [x["count"] for x in old + current]
    assert len(counts) == len(set(counts))
    assert counts[-1] % 1000 == 149  # the last line written is there, whole


def test_alerts_are_kept_in_their_own_file_with_500_lines(monkeypatch, seen):
    monkeypatch.setattr(audit, "ALERTS_MAX_LINES", 5)
    for n in range(7):
        audit.alert("remote_complet", f"Tâche avec accès complet lancée depuis l'iPhone : « T{n} ».", PHONE)
    alerts = lines("remote-alerts.jsonl")
    assert [a["text"][-5:-3] for a in alerts] == ["T5", "T6"]
    assert len(lines("remote-alerts.1.jsonl")) == 5
    assert all(a["kind"] == "alert" and a["alert"] == "remote_complet" for a in alerts)
    # The same lines are in the audit trail, whatever floods it.
    assert len([x for x in lines() if x["kind"] == "alert"]) == 7

# ---------------------------------------------------------------- alerts


def test_an_alert_reaches_the_pc_as_plain_text_and_the_hooks_with_the_fixed_text(seen):
    text = "Nouvel appareil associé : « iPhone de test » (100.101.102.103)."
    audit.alert("new_device", text, PHONE)
    assert seen["toasts"] == [("JARVIS · sécurité", text)]
    assert seen["pc"] == [("warning", {"kind": "remote", "text": text, "plain": True})]
    assert seen["hooks"] == [("new_device", "nouvel appareil associé.")]
    (line,) = lines("remote-alerts.jsonl")
    assert line["alert"] == "new_device" and line["text"] == text and line["device"] == PHONE.device_id


def test_toast_only_kinds_never_reach_the_hooks(seen):
    audit.alert("pair_request", "Demande d'association : code 4821 (iOS, 100.101.102.103).", PHONE)
    audit.alert("listener_error", "Accès à distance : le port 8789 est déjà utilisé.")
    assert len(seen["toasts"]) == 2 and seen["hooks"] == []


def test_dedupe_per_kind_and_device_or_address(seen, clock):
    funnel = "Requête refusée venant d'internet (Funnel) : vérifiez Tailscale."
    other = remote.Caller(kind="unpaired", ip="100.64.0.9")
    audit.alert("funnel", funnel, PHONE)
    audit.alert("funnel", funnel, PHONE)          # same device within 10 minutes: silent
    audit.alert("funnel", funnel, other)          # another address: told
    audit.alert("login_change", "Compte Tailscale inattendu pour « iPhone de test » : refusé.", PHONE)  # another kind
    assert [k for k, _ in seen["hooks"]] == ["funnel", "funnel", "login_change"]
    clock["t"] += 601
    audit.alert("funnel", funnel, PHONE)          # the window passed
    assert [k for k, _ in seen["hooks"]] == ["funnel", "funnel", "login_change", "funnel"]
    assert len(seen["toasts"]) == len(seen["pc"]) == 4
    # Every one of them is in the record, the silenced one included.
    assert len([a for a in lines("remote-alerts.jsonl") if a["alert"] == "funnel"]) == 4


def test_a_device_named_in_the_fields_is_the_dedupe_key(seen):
    unpaired = remote.Caller(kind="unpaired", ip="100.101.102.103")
    text = "Compte Tailscale inattendu pour « iPhone de test » : refusé."
    audit.alert("login_change", text, unpaired, device="d_0123456789abcdef")
    audit.alert("login_change", text, unpaired, device="d_fedcba9876543210")
    audit.alert("login_change", text, unpaired, device="d_0123456789abcdef")
    assert len(seen["hooks"]) == 2


def test_remote_complet_is_never_deduplicated(seen):
    for _ in range(3):
        audit.alert("remote_complet", "Tâche avec accès complet lancée depuis l'iPhone : « Ranger ».", PHONE)
    assert seen["hooks"] == [("remote_complet", "tâche avec accès complet lancée depuis l'iPhone.")] * 3
    assert len(seen["toasts"]) == 3


def test_a_failing_hook_never_stops_the_others(seen, monkeypatch):
    calls = []

    def broken(kind, text):
        raise RuntimeError("ntfy en panne")
    monkeypatch.setattr(audit, "ALERT_HOOKS", [broken, lambda kind, text: calls.append(kind)])
    monkeypatch.setattr(desktop, "toast", lambda title, body: (_ for _ in ()).throw(OSError("pas de toast")))
    audit.alert("remote_on", "Accès à distance activé.")
    assert calls == ["remote_on"]
    assert seen["pc"] == [("warning", {"kind": "remote", "text": "Accès à distance activé.", "plain": True})]

# ---------------------------------------------------------------- throttling


def test_refusal_lines_are_throttled_per_address(clock):
    noisy = remote.Caller(kind="unpaired", ip="100.64.0.9")
    calm = remote.Caller(kind="unpaired", ip="100.64.0.10")
    for _ in range(25):
        audit.event(noisy, "request", route="/api/config", status=401, reason="unpaired")
    audit.event(calm, "request", route="/api/config", status=401, reason="unpaired")
    audit.event(noisy, "request", route="/api/config", status=200)  # successes are never throttled
    written = lines()
    assert len([x for x in written if x.get("ip") == noisy.ip and x.get("status") == 401]) == 20
    assert len([x for x in written if x.get("ip") == calm.ip]) == 1
    assert written[-1]["status"] == 200
    clock["t"] += 601
    audit.event(noisy, "request", route="/api/config", status=403, reason="scope")
    count_line, refusal = lines()[-2:]
    assert count_line == {"t": count_line["t"], "kind": "request", "ip": noisy.ip, "reason": "throttled",
                          "count": 5}
    assert refusal["status"] == 403 and refusal["reason"] == "scope"


def test_alerts_are_never_throttled(seen):
    noisy = remote.Caller(kind="unpaired", ip="100.64.0.9")
    for _ in range(30):
        audit.event(noisy, "request", status=401, reason="unpaired")
    audit.alert("auth_failures", "Échecs répétés d'authentification depuis 100.64.0.9 : bloqué 15 minutes.", noisy)
    assert lines()[-1]["alert"] == "auth_failures"
    assert len(seen["hooks"]) == 1


def test_reset_memory_forgets_the_windows(seen):
    audit.alert("funnel", "x", PHONE)
    audit.reset_memory()
    audit.alert("funnel", "x", PHONE)
    assert len(seen["hooks"]) == 2
