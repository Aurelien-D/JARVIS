"""strings-fr.js (design spec §11): French typography, 'vous', the formatters
and the error table. The module is pure, so node runs it directly (skipped
when node is not installed; the browser tests use the same file)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = (Path(__file__).resolve().parent.parent / "static" / "js" / "strings-fr.js").as_uri()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node absent")

NNBSP, NBSP = "\u202f", "\u00a0"


def run(js: str):
    """Run `js` (a function body using the module as S) in node; returns its JSON result."""
    script = (f"import * as S from {json.dumps(MODULE)};\n"
              f"const out = await (async () => {{ {js} }})();\n"
              "process.stdout.write(JSON.stringify(out));")
    env = {"TZ": "Europe/Paris", "PATH": ""}
    if "SYSTEMROOT" in os.environ:  # Windows: without it node can't seed its RNG and aborts
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    res = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True,
                         text=True, encoding="utf-8", timeout=30, env=env)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# Every string of T, functions called with sample arguments.
ALL = """
  const out = [];
  const walk = (node, path) => {
    for (const [k, v] of Object.entries(node)) {
      const p = path + '.' + k;
      if (typeof v === 'string') out.push([p, v]);
      else if (typeof v === 'function') out.push([p, v('X', 'Y')]);
      else if (v && typeof v === 'object') walk(v, p);
    }
  };
  walk(S.T, 'T');
  return out;
"""


def test_french_typography_everywhere():
    strings = run(ALL)
    assert len(strings) > 150
    for path, s in strings:
        for i, ch in enumerate(s):
            if ch in "?!:;»" and i and s[i - 1] in (" ", NBSP):
                pytest.fail(f"{path}: espace ordinaire avant « {ch} » : {s!r}")
            if ch == "«":
                assert s[i + 1:i + 2] == NNBSP, f"{path}: « sans espace fine : {s!r}"
            if ch == "»":
                assert s[i - 1] == NNBSP, f"{path}: » sans espace fine : {s!r}"
    assert dict(strings)["T.controls.micOff"] == f"Micro{NNBSP}: coupé"
    assert dict(strings)["T.status.standby"] == f"En veille · dites «{NNBSP}Jarvis{NNBSP}» ou cliquez sur l'orbe"


def test_vous_everywhere_and_feminine_task():
    import re
    strings = run(ALL)
    for path, s in strings:
        if re.match(r"T\.help\.(examples|pcOnly|categories\.\d+\.examples)\.", path):
            continue  # what monsieur says to JARVIS, not what JARVIS writes
        own = re.sub(r"«[^»]*»", "", s)  # quoted examples of what he can say
        assert not re.search(r"\b(tu|toi|ton|ta|tes|te)\b", own, re.I), f"{path}: tutoiement : {s!r}"
    table = dict(strings)
    assert table["T.task.status.done"] == "Terminée"
    assert table["T.status.tasks"].startswith(" · X tâches en cours")


def test_fr_is_idempotent_and_spares_urls_and_times():
    out = run("""return [S.fr('Ouvrir ? Oui !'), S.fr(S.fr('A : b')), S.fr('https://x.fr/a?b=1'),
                        S.fr('14:30'), S.fr('« test »')]""")
    assert out == [f"Ouvrir{NNBSP}? Oui{NNBSP}!", f"A{NNBSP}: b", "https://x.fr/a?b=1", "14:30",
                   f"«{NNBSP}test{NNBSP}»"]


def test_formatters():
    out = run("""
      const d = (h, m) => new Date(2026, 9, 9, h, m);
      const now = d(15, 0).getTime();
      return {
        t1: S.fmtTime(d(14, 30)), t2: S.fmtTime(d(8, 0)), t3: S.fmtTime(d(9, 5)),
        r0: S.fmtRelative(now - 10e3, now), r3: S.fmtRelative(now - 3 * 60e3, now),
        rin: S.fmtRelative(now + 5 * 60e3, now), rh: S.fmtRelative(now - 2 * 3600e3, now),
        rtoday: S.fmtRelative(d(7, 15), now), ry: S.fmtRelative(new Date(2026, 9, 8, 9, 5), now),
        rtom: S.fmtRelative(new Date(2026, 9, 10, 8, 0), d(22, 0)),
        n: S.fmtNumber(1234.5), eur: S.fmtNumber(12480, {style: 'currency', currency: 'EUR'}),
        e1: S.fmtElapsed(42), e2: S.fmtElapsed(3725), e3: S.fmtElapsed(-3),
      };
    """)
    assert out["t1"] == f"14{NBSP}h{NBSP}30" and out["t2"] == f"8{NBSP}h" and out["t3"] == f"9{NBSP}h{NBSP}05"
    assert out["r0"] == "à l'instant"
    assert out["r3"] == "il y a 3 min" and out["rin"] == "dans 5 min" and out["rh"] == "il y a 2 h"
    assert out["rtoday"] == f"à 7{NBSP}h{NBSP}15"
    assert out["ry"] == f"hier à 9{NBSP}h{NBSP}05"
    assert out["rtom"] == f"demain à 8{NBSP}h"
    assert out["n"] == f"1{NNBSP}234,5" and out["eur"] == f"12{NNBSP}480,00{NBSP}€"
    assert (out["e1"], out["e2"], out["e3"]) == ("0:42", "1:02:05", "0:00")


def test_explain_error_table():
    out = run("""
      const E = S.T.error, x = (e) => S.explainError(e);
      const dom = (name) => Object.assign(new Error('Permission denied'), {name});
      return {
        allowed: x({kind: 'connect', message: 'Permission denied', detail: dom('NotAllowedError')}) === E.NotAllowedError,
        found: x(dom('NotFoundError')) === E.NotFoundError,
        readable: x(dom('NotReadableError')) === E.NotReadableError,
        key: x({message: "OPENAI_API_KEY manquant: copie .env.example vers .env et mets ta clé."}) === E.noKey,
        refused: x({message: 'OpenAI 401: {"error": {"code": "invalid_api_key"}}', status: 502}) === E.unauthorized,
        quota: x('OpenAI 429: {"error": {"type": "insufficient_quota"}}') === E.quota,
        rate: x('OpenAI 429: Rate limit reached') === E.rate,
        model: x('OpenAI 404: {"error": {"code": "model_not_found"}}') === E.model,
        network: x('OpenAI injoignable : ConnectError') === E.network,
        server: x(new TypeError('Failed to fetch')) === E.server,
        timeout: x(dom('AbortError')) === E.timeout,
        lost: x({kind: 'lost'}) === E.lost,
        filter: x({kind: 'content_filter'}) === E.contentFilter,
        empty: x(null) === E.unknown,
        other: x('Le disque est plein : libérez de la place.'),
        own: Object.values(E).filter(v => typeof v === 'string').every(v => x(v) === v),
        ownFn: [E.budget('2 €'), E.failed('raison'), E.hotkey('Ctrl+J')].every(v => x(v) === v),
      };
    """)
    other = out.pop("other")
    assert all(out.values()), out
    assert other == f"Le disque est plein{NNBSP}: libérez de la place."


def test_tool_labels():
    out = run("""return [S.toolLabel('open_app', {name: 'Spotify'}), S.toolLabel('open_app', null),
                         S.toolLabel('look_at_screen'), S.toolLabel('inconnu'),
                         S.toolLabel('info', {kind: 'actualites'}), S.toolLabel('info', {})]""")
    assert out == ["Ouverture de Spotify…", "Ouverture de l'application…", "Je regarde l'écran…", "",
                   "Je consulte l'actualité…", "Je consulte la météo…"]
