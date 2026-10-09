"""Untrusted text on screen (design: the page never runs what it shows).

Markup is injected through every channel that reaches the screen: a task's
title, progress and output, its denied tools on an approval card, a reminder,
a memory fact, display_card, display_report (title, KPIs, chart labels and
series names, table cells, markdown), JARVIS's and monsieur's captions, the
inbox replayed on load, a confirmation card, a toast, an error in the status
line. Each payload tries an onerror image, a javascript: link (raw and in
markdown), an SVG onload, a details ontoggle and an iframe srcdoc; it must stay
text (or inert markup): window.__pwned stays undefined and no javascript: link
is left to click. Links rendered from markdown open in a new tab, without
opener or referrer, and only for http(s) and mailto."""
import json
import sys
import time

import pytest

pytestmark = pytest.mark.e2e


def compact(tag: str) -> str:
    """Short enough for a task title (80) or a progress line (160)."""
    return f"{tag}<img src=x onerror=\"__pwned='{tag}'\"><a href=\"javascript:__pwned='{tag}'\">x</a>"


def evil(tag: str) -> str:
    return (f"{tag} <img src=x onerror=\"window.__pwned='{tag}'\"> "
            f"<a href=\"javascript:window.__pwned='{tag}'\">x</a> "
            f"[x](javascript:window.__pwned='{tag}') "
            f"<svg onload=\"window.__pwned='{tag}'\"></svg> "
            f"<details open ontoggle=\"window.__pwned='{tag}'\"><summary>d</summary></details> "
            f"<iframe srcdoc=\"<script>parent.__pwned='{tag}'</script>\"></iframe> "
            f"**{tag}-gras**")


# Already escaped once: must not turn into markup on a second pass.
ENTITIES = "&lt;img src=x onerror=&quot;window.__pwned='entites'&quot;&gt; &#60;b&#62;entites"

# Every element on the page: no inline handler, no script-like URL, no frame.
# Then, if none is left, every link that is not a web or mail link is clicked.
SWEEP = r"""
async () => {
  const found = [];
  const bad = /^(javascript|vbscript|data:text\/html)/;
  await new Promise(r => setTimeout(r, 600));  // an image's onerror, a details' ontoggle
  for (const el of document.body.querySelectorAll("*")) {
    for (const a of el.attributes) {
      const name = a.name.toLowerCase();
      const value = a.value.replace(/[\u0000-\u0020]/g, "").toLowerCase();
      if (name.startsWith("on")) found.push(`${el.tagName}[${name}]`);
      if (bad.test(value)) found.push(`${el.tagName}[${name}=${a.value.slice(0, 40)}]`);
    }
    const own = el.tagName === "SCRIPT" && el.getAttribute("src") === "/static/js/main.js";  // the page itself
    if (!own && ["SCRIPT", "IFRAME", "OBJECT", "EMBED", "FRAME"].includes(el.tagName.toUpperCase())) found.push(el.tagName);
  }
  const before = window.__pwned === undefined ? null : String(window.__pwned);
  if (!found.length && before === null) {
    for (const a of document.body.querySelectorAll("a, area")) {
      const href = (a.getAttribute("href") || a.getAttribute("xlink:href") || "").trim();
      if (!/^(https?:|mailto:|#)/i.test(href)) a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    }
    await new Promise(r => setTimeout(r, 300));
  }
  return { found, pwned: window.__pwned === undefined ? null : String(window.__pwned) };
}
"""


def assert_inert(page):
    out = page.evaluate(SWEEP)
    assert out["pwned"] is None, f"du texte non fiable a été exécuté : {out['pwned']}"
    assert out["found"] == [], out["found"]


def shown(page, selector, tag):
    """The payload did reach the screen there (the test is not vacuous)."""
    page.wait_for_function("([s, t]) => [...document.querySelectorAll(s)].some(e => e.textContent.includes(t))",
                           arg=[selector, tag])


def tool(page, name, args, session_id=None):
    return page.evaluate("b => __jarvis.api('/api/tool', {method: 'POST', body: b})",
                         {"name": name, "arguments": args, "session_id": session_id})


def emit(page, event):
    page.evaluate("ev => __emit(ev)", event)


def go_live(page):
    page.click("#orbBtn")
    page.wait_for_function("__jarvis.state.mode === 'live'")


def call(page, name, args, call_id):
    emit(page, {"type": "response.done", "response": {"status": "completed", "output": [
        {"type": "function_call", "name": name, "call_id": call_id, "status": "completed",
         "arguments": json.dumps(args)}]}})
    page.wait_for_function(f"__sent.some(m => m.item && m.item.call_id === '{call_id}')")


def needs_cdn(page):
    if not page.evaluate("!!(window.marked && window.DOMPurify)"):
        pytest.skip("marked et DOMPurify (CDN) indisponibles")


# A `claude` whose progress, output and denied tools are hostile.
HOSTILE_CLAUDE = r'''
import json, sys, time
sys.stdin.read()
def emit(obj):
    print(json.dumps(obj), flush=True)
emit({"type": "system", "subtype": "init", "session_id": "s-hostile"})
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t1", "name": "WebSearch", "input": {"query": PROGRESS}}]}})
time.sleep(1.5)
emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "s-hostile",
      "permission_denials": [{"tool_name": DENIED, "tool_use_id": "b1", "tool_input": {}},
                             {"tool_name": "Bash", "tool_use_id": "b2", "tool_input": {"command": COMMAND}}],
      "result": OUTPUT})
'''


@pytest.fixture
def hostile_claude(tmp_path, monkeypatch):
    from jarvis import tasks
    script = tmp_path / "hostile_claude.py"
    consts = (f"PROGRESS = {compact('progres')!r}\nDENIED = {compact('refus')!r}\n"
              f"COMMAND = {compact('commande')!r}\nOUTPUT = {evil('sortie')!r}\n")
    script.write_text(consts + HOSTILE_CLAUDE, encoding="utf-8")
    monkeypatch.setattr(tasks, "claude_command", lambda: [sys.executable, str(script)])


@pytest.fixture
def no_confirmations():
    from jarvis import confirm
    yield
    confirm.PENDING.clear()


# ---------------------------------------------------------------- 2. untrusted text stays text

def test_task_title_progress_output_and_denials_stay_text_holds(jarvis, hostile_claude, no_confirmations):
    task = jarvis.evaluate("b => __jarvis.api('/api/tasks', {method: 'POST', body: b})",
                           {"prompt": "cherche", "title": compact("titre"), "profile": "recherche"})
    card = f"#task-{task['id']}"
    shown(jarvis, f"{card} .t", "titre")
    shown(jarvis, f"{card} .prog", "progres")  # while it runs
    assert_inert(jarvis)
    jarvis.wait_for_selector(f"{card}.done", timeout=15_000)
    shown(jarvis, f"{card} .out", "sortie-gras")
    assert jarvis.locator(f"{card} .out strong").count() >= 1  # markdown still renders
    # Claude's denied tools come back as an approval card.
    shown(jarvis, "#cards .card.confirm", "refus")
    shown(jarvis, "#cards .card.confirm .confirm-detail", "refus")
    shown(jarvis, "#cards .card.confirm .confirm-detail", "commande")
    assert_inert(jarvis)


@pytest.fixture
def forget_hostile():
    """The facts and reminders this test planted go with it."""
    yield
    from jarvis import inbox, memory, scheduler
    memory.forget("__pwned")
    scheduler.cancel("__pwned")
    for item in inbox.pending():
        inbox.ack(item["id"])


def test_reminders_memory_warnings_and_the_inbox_stay_text_holds(jarvis, reload_jarvis, forget_hostile):
    from jarvis import events, inbox
    tool(jarvis, "schedule", {"kind": "reminder", "title": compact("rappel-titre"),
                              "text": evil("rappel-texte"), "delay_minutes": 600})
    shown(jarvis, "#scheduleList", "rappel-titre")
    tool(jarvis, "remember", {"fact": evil("fait")})
    shown(jarvis, "#memoryList", "fait")
    events.publish("reminder", {"id": "xss-r1", "title": compact("echeance"), "text": evil("echeance")})
    shown(jarvis, "#cards .card.warning", "echeance")
    events.publish("warning", {"kind": "corrupt", "text": evil("avertissement")})
    shown(jarvis, "#cards .card.warning", "avertissement")
    assert_inert(jarvis)
    for item in inbox.pending():
        inbox.ack(item["id"])

    # Told on the next page load: « Pendant votre absence ».
    inbox.add("reminder", {"id": "xss-r2", "title": compact("absent-rappel"), "text": evil("absent-rappel")})
    inbox.add("warning", {"text": evil("absent-avert")})
    inbox.add("task", {"id": "xss-t1", "title": compact("absent-tache"), "status": "done",
                       "output": evil("absent-sortie")})
    reload_jarvis()
    shown(jarvis, "#cards .card.warning", "absent-rappel")
    shown(jarvis, "#cards .card.warning", "absent-avert")
    shown(jarvis, "#memoryList", "fait")
    shown(jarvis, "#scheduleList", "rappel-titre")
    assert_inert(jarvis)


def test_display_card_and_report_stay_text_holds(jarvis):
    go_live(jarvis)
    call(jarvis, "display_card", {"title": evil("carte-titre"), "content": evil("carte-contenu"),
                                  "kind": 'info" onmouseover="window.__pwned=1'}, "c1")
    shown(jarvis, "#cards h3", "carte-titre")
    shown(jarvis, "#cards .body", "carte-contenu-gras")
    rows = [[evil(f"cellule{i}"), i] for i in range(12)]  # >8: search box, >12 would page
    report = {
        "title": evil("rapport-titre"),
        "kpis": [{"label": evil("kpi-label"), "value": evil("kpi-valeur"), "delta": evil("kpi-delta")}],
        "chart": {"type": "bar", "categories": [evil("categorie-a"), ENTITIES],
                  "series": [{"name": evil("serie-a"), "data": [3, 5]},
                             {"name": evil("serie-b"), "data": [4, compact("valeur")]}]},
        "table": {"columns": [evil("colonne-a"), evil("colonne-b")], "rows": rows},
        "markdown": evil("notes"),
    }
    call(jarvis, "display_report", report, "r1")
    jarvis.wait_for_selector("#report[open]")
    shown(jarvis, "#rtitle", "rapport-titre")
    shown(jarvis, "#kpis", "kpi-label")
    shown(jarvis, "#kpis", "kpi-delta")
    shown(jarvis, "#rmd", "notes-gras")
    if jarvis.evaluate("!!window.gridjs"):
        shown(jarvis, "#rtable", "cellule3")
        shown(jarvis, "#rtable th", "colonne-a")
        jarvis.click("#rtable th >> nth=0")  # sorted: re-rendered
        jarvis.fill("#rtable input", "cellule")  # searched: re-rendered
    if jarvis.evaluate("!!window.ApexCharts"):
        jarvis.wait_for_selector("#rchart svg.apexcharts-svg")
        shown(jarvis, "#rchart", "serie-a")  # the legend
        box = jarvis.locator("#rchart svg.apexcharts-svg").bounding_box()
        for fx in (0.3, 0.5, 0.7):  # the tooltip writes the category and the series names
            jarvis.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * 0.6)
            jarvis.wait_for_timeout(150)
    assert_inert(jarvis)
    # A donut writes its slice labels in the legend and the tooltip.
    donut = {"title": "Parts", "chart": {"type": "donut", "categories": [evil("part-a"), evil("part-b")],
                                         "series": [{"name": evil("donut"), "data": [60, 40]}]}}
    call(jarvis, "display_report", donut, "r2")
    if jarvis.evaluate("!!window.ApexCharts"):
        jarvis.wait_for_selector("#rchart svg.apexcharts-svg")
        shown(jarvis, "#rchart", "part-a")
        box = jarvis.locator("#rchart svg.apexcharts-svg").bounding_box()
        jarvis.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.3)
        jarvis.wait_for_timeout(200)
    assert_inert(jarvis)


def test_captions_stay_text_holds(jarvis):
    # Monsieur through the wake word recogniser (standby), then both captions live.
    jarvis.evaluate("t => __say(t, false)", "Jarvis " + compact("eveil"))
    jarvis.wait_for_timeout(200)
    assert_inert(jarvis)
    if jarvis.evaluate("__jarvis.state.mode") != "live":
        go_live(jarvis)
    emit(jarvis, {"type": "input_audio_buffer.speech_started"})
    emit(jarvis, {"type": "input_audio_buffer.speech_stopped", "item_id": "u1"})
    emit(jarvis, {"type": "conversation.item.input_audio_transcription.delta", "item_id": "u1",
                  "delta": evil("monsieur")})
    shown(jarvis, "#you", "monsieur")
    emit(jarvis, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1",
                  "transcript": evil("monsieur")})
    emit(jarvis, {"type": "response.created"})
    emit(jarvis, {"type": "response.output_audio_transcript.delta", "item_id": "j1", "delta": evil("jarvis")})
    shown(jarvis, "#transcript", "jarvis")
    emit(jarvis, {"type": "response.output_audio_transcript.done", "item_id": "j1", "transcript": evil("jarvis")})
    emit(jarvis, {"type": "response.done", "response": {"status": "completed", "output": []}})
    assert_inert(jarvis)
    # OpenAI's own error and failure messages reach a card too.
    emit(jarvis, {"type": "error", "error": {"message": evil("erreur-openai"), "code": "x"}})
    shown(jarvis, "#cards .card.warning", "erreur-openai")
    emit(jarvis, {"type": "response.done", "response": {"status": "failed", "output": [],
                  "status_details": {"error": {"message": evil("echec-reponse")}}}})
    shown(jarvis, "#cards", "echec-reponse")
    assert_inert(jarvis)


def test_confirmation_cards_toasts_and_errors_stay_text_holds(jarvis, no_confirmations):
    from jarvis import confirm
    out = tool(jarvis, "delegate_to_claude", {"title": compact("confirmation"), "prompt": evil("consigne"),
                                              "profile": "complet"})
    assert out["status"] == "needs_confirmation"
    card = f"#card-confirm-{out['pending_id']}"
    shown(jarvis, f"{card} .body", "confirmation")
    shown(jarvis, f"{card} .confirm-detail", "consigne")
    assert_inert(jarvis)
    jarvis.click(f"{card} .actions button >> nth=1")  # Annuler: the outcome card repeats the summary
    jarvis.wait_for_function(f"document.querySelector('{card}')?.dataset.state === 'cancelled'")
    shown(jarvis, f"{card} .body", "confirmation")
    assert confirm.PENDING[out["pending_id"]]["state"] == "cancelled"
    assert_inert(jarvis)

    # The composer's « /tâche » toasts what was typed (and starts a task named
    # after it), then the server's refusal.
    jarvis.fill("#askInput", "/tâche " + compact("tache-tapee"))
    jarvis.press("#askInput", "Enter")
    shown(jarvis, "#toasts", "tache-tapee")
    shown(jarvis, "#taskList .t", "tache-tapee")
    jarvis.route("**/api/tasks", lambda route: route.fulfill(status=400, json={"detail": evil("refus-serveur")}))
    jarvis.fill("#askInput", "/tâche encore")
    jarvis.press("#askInput", "Enter")
    shown(jarvis, "#toasts", "refus-serveur")
    jarvis.evaluate("""async ([t, a]) => (await import('/static/js/hud.js')).toast(t, {actionLabel: a})""",
                    [evil("toast"), compact("action")])
    shown(jarvis, "#toasts", "toast")
    assert_inert(jarvis)

    # An app opened by name: the status line (tool label) and its card.
    jarvis.route("**/api/tool", lambda route: route.fulfill(json={"ok": False, "error": evil("outil")}))
    go_live(jarvis)
    emit(jarvis, {"type": "response.done", "response": {"status": "completed", "output": [
        {"type": "function_call", "name": "open_app", "call_id": "a1", "status": "completed",
         "arguments": json.dumps({"name": compact("appli")})}]}})
    shown(jarvis, "#cards", "appli")
    jarvis.wait_for_function("__sent.some(m => m.item && m.item.call_id === 'a1')")
    assert_inert(jarvis)


def test_a_session_error_from_the_server_stays_text_holds(jarvis):
    jarvis.route("**/api/session", lambda route: route.fulfill(
        status=502, json={"detail": f"OpenAI a refusé d'ouvrir la session : {evil('motif')} (OpenAI 400)"}))
    jarvis.click("#orbBtn")
    shown(jarvis, "#statusPill", "motif")
    assert_inert(jarvis)

# ---------------------------------------------------------------- 3. links from markdown

LINKS = "\n\n".join([
    "[web](https://exemple.fr/a)", "[clair](http://exemple.fr/b)", "[courriel](mailto:monsieur@exemple.fr)",
    "<https://auto.exemple.fr/c>", "Nu : https://nu.exemple.fr/d",
    "[js](javascript:window.__pwned='js')", "[JS](JaVaScRiPt:window.__pwned='JS')",
    "[tab](java\tscript:window.__pwned='tab')", "[ent](jav&#x09;ascript:window.__pwned='ent')",
    "[vb](vbscript:msgbox)", "[data](data:text/html,<script>parent.__pwned='data'</script>)",
    "[tel](tel:+33123456789)", "[ftp](ftp://exemple.fr/f)", "[fichier](file:///C:/Windows/win.ini)",
    "[relatif](/api/shutdown)", "[proto](//evil.example/x)", "[ancre](#askInput)", "[rien]()",
    "<a href=\"javascript:window.__pwned='raw'\">raw</a>",
    "<a href=\"https://exemple.fr/e\" target=\"_self\" rel=\"opener\">brut</a>",
    ("<svg><a href=\"https://exemple.fr/svg\"><text y=\"20\">svg</text></a>"
     "<a xlink:href=\"javascript:window.__pwned='svg'\"><text y=\"40\">svg2</text></a></svg>"),
    "<map name=\"m\"><area shape=\"rect\" coords=\"0,0,10,10\" href=\"javascript:window.__pwned='area'\"></map>",
])
WEB = {"https://exemple.fr/a", "http://exemple.fr/b", "mailto:monsieur@exemple.fr", "https://auto.exemple.fr/c",
       "https://nu.exemple.fr/d", "https://exemple.fr/e"}

LINK_AUDIT = r"""
(root) => [...document.querySelector(root).querySelectorAll("a, area, [href], [xlink\\:href]")].map(a => ({
  tag: a.tagName.toLowerCase(), text: a.textContent,
  href: a.getAttribute("href") ?? a.getAttribute("xlink:href"),
  target: a.getAttribute("target"), rel: a.getAttribute("rel") || "",
}))
"""


def audit_links(page, root):
    links = page.evaluate(LINK_AUDIT, root)
    with_href = [link for link in links if link["href"]]
    for link in with_href:
        assert link["href"].lower().startswith(("https:", "http:", "mailto:")), link
        assert link["target"] == "_blank", link
        assert {"noopener", "noreferrer"} <= set(link["rel"].split()), link
    return {link["href"].rstrip("/") for link in with_href}


def test_markdown_links_open_in_a_new_tab_only_for_web_and_mail_holds(jarvis):
    needs_cdn(jarvis)
    jarvis.evaluate("""async ([text, task]) => {
      const hud = await import('/static/js/hud.js');
      hud.addCard('Liens', text, 'result', {id: 'liens'});
      (await import('/static/js/report.js')).showReport({title: 'Liens', markdown: text});
      (await import('/static/js/panels.js')).renderTask(task);
    }""", [LINKS, {"id": "liens", "title": "Liens", "status": "done", "output": LINKS, "started": time.time() - 5,
                   "ended": time.time(), "profile": "recherche", "model": "sonnet", "origin": "voix"}])
    for root in ("#card-liens .body", "#rmd", "#task-liens .out"):
        assert audit_links(jarvis, root) >= {h.rstrip("/") for h in WEB}, root
        assert audit_links(jarvis, root) <= {h.rstrip("/") for h in WEB} | {"https://exemple.fr/svg"}, root
    jarvis.evaluate("document.querySelector('#report').close()")
    assert_inert(jarvis)

    # A web link opens beside JARVIS, which it can neither reach nor tell where it came from.
    seen = []
    jarvis.context.route("https://exemple.fr/**", lambda route: (seen.append(route.request.headers), route.fulfill(
        status=200, body="<title>ok</title><p>ok</p>", headers={"Content-Type": "text/html"})))
    with jarvis.context.expect_page() as opened:
        jarvis.click("#card-liens .body a[href='https://exemple.fr/a']")
    page = opened.value
    page.wait_for_load_state()
    assert page.evaluate("window.opener === null")
    assert not any("referer" in h for h in seen)
    assert jarvis.url.startswith("http://127.0.0.1")
    page.close()


def test_markdown_without_the_cdn_renders_no_link_at_all_holds(jarvis):
    jarvis.evaluate("""async (text) => {
      const hud = await import('/static/js/hud.js');
      const marked = window.marked;
      window.marked = undefined;
      try { hud.addCard('Hors ligne', text, 'result', {id: 'liens-off'}); } finally { window.marked = marked; }
    }""", LINKS)
    assert jarvis.locator("#card-liens-off .body a, #card-liens-off .body [href]").count() == 0
    assert "javascript:" in jarvis.inner_text("#card-liens-off .body")  # shown as text
    assert_inert(jarvis)


def test_a_link_from_the_voice_model_is_held_to_the_same_rules_holds(jarvis):
    needs_cdn(jarvis)
    go_live(jarvis)
    call(jarvis, "display_card", {"title": "Liens", "content": LINKS}, "l1")
    shown(jarvis, "#cards .body", "courriel")
    root = "#cards .card:first-of-type .body"
    assert audit_links(jarvis, root) >= {h.rstrip("/") for h in WEB}
    assert_inert(jarvis)


def test_markdown_draws_no_form_input_or_inline_style_holds(jarvis):
    """No script runs, but a fake password box inside a card is phishing all the same."""
    needs_cdn(jarvis)
    jarvis.evaluate("""() => import('/static/js/hud.js').then(m => m.addCard('Formulaire',
        'avant <form action="https://evil.example"><input type="password" placeholder="Mot de passe">'
        + '<button>Valider</button><textarea>t</textarea><select><option>o</option></select></form>'
        + '<span style="position:fixed;inset:0;background:red">voile</span><style>body{display:none}</style> apres',
        'info'))""")
    shown(jarvis, ".card .body", "apres")
    body = jarvis.locator(".card .body").first
    assert body.locator("form, input, button, textarea, select, option, style, [style]").count() == 0
    assert "voile" in body.inner_text()  # the text stays, only the markup goes
    assert_inert(jarvis)


def test_a_camera_photo_counts_as_outside_content_holds(jarvis):
    from jarvis import confirm
    go_live(jarvis)
    sid = jarvis.evaluate("__jarvis.voice.sessionId()")
    assert not confirm.is_tainted(sid)
    with jarvis.expect_response("**/api/voice/taint"):
        call(jarvis, "look_at_camera", {}, "cam1")
    assert confirm.is_tainted(sid)
