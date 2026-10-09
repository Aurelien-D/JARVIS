"""The side panel and the task viewer (WP10): readable task cards with their
actions, reminders by day with an undoable delete, memory, and the viewer
dialog. A full-access 'Réessayer' only ever asks for a confirmation."""
import time

import pytest

pytestmark = pytest.mark.e2e

NNBSP = "\u202f"


def emit_task(page, **task):
    now = time.time()
    tk = {"profile": "recherche", "model": "sonnet", "origin": "voix", "started": now - 30, "ended": None,
          "output": "", "progress": "", "steps": 0, "files": [], "permission_denials": [], **task}
    page.evaluate("tk => __jarvis.bus.emit('server:task', tk)", tk)
    return tk


def order(page):
    return page.evaluate("""[...document.querySelectorAll('#taskList > .task')].map(e =>
      [e.querySelector('.t').textContent, e.querySelector('.lbl').textContent])""")


def requests_to(page, pattern):
    seen = []
    page.on("request", lambda r: seen.append((r.method, r.url, r.post_data)) if pattern in r.url else None)
    return seen


@pytest.fixture
def no_pending():
    from jarvis import confirm
    confirm.PENDING.clear()
    yield
    confirm.PENDING.clear()


# ---------------------------------------------------------------- task cards

def test_tasks_are_ordered_running_failed_done_with_french_labels(jarvis):
    now = time.time()
    emit_task(jarvis, id="d1", title="Faite", status="done", output="ok", started=now - 300, ended=now - 200)
    emit_task(jarvis, id="e1", title="Ratée", status="error", output="Erreur", prompt="Fais-le",
              started=now - 100, ended=now - 90)
    emit_task(jarvis, id="r1", title="En route", status="running", progress="Recherche web : prix", started=now - 65)
    assert order(jarvis) == [["En route", "En cours"], ["Ratée", "Échec"], ["Faite", "Terminée"]]
    assert jarvis.text_content("#tasks summary h2") == "Sessions Claude Code · 1 en cours"
    run = "#task-r1"
    assert "Web uniquement" in jarvis.inner_text(f"{run} .meta")
    assert jarvis.get_attribute(f"{run} .meta", "title") == f"Modèle{NNBSP}: sonnet"  # the model in a tooltip
    assert jarvis.inner_text(f"{run} .prog") == "Recherche web : prix"
    assert jarvis.inner_text(f"{run} .chrono").startswith("1:0")
    # Actions by status: a running task can be read or cancelled; a failed one retried.
    acts = lambda sel: jarvis.evaluate(f"[...document.querySelectorAll('{sel} .actions button')].map(b => b.textContent)")
    assert acts(run) == ["Lire", "Annuler la tâche"]
    assert acts("#task-e1") == ["Lire", "Copier", "Réessayer"]
    assert acts("#task-d1") == ["Lire", "Copier", "Continuer"]
    # It ends: same card, now done, moved below the failed one.
    emit_task(jarvis, id="r1", title="En route", status="done", output="Fini", started=now - 65, ended=now)
    assert order(jarvis)[0] == ["Ratée", "Échec"]
    assert jarvis.text_content("#tasks summary h2") == "Sessions Claude Code (3)"


def test_output_markdown_renders_as_a_heading(jarvis):
    emit_task(jarvis, id="md1", title="Synthèse", status="done", ended=time.time(),
              output="## Titre\n\nUn **résultat** et une liste :\n\n- un\n- deux")
    out = jarvis.locator("#task-md1 .out")
    if not jarvis.evaluate("!!(window.marked && window.DOMPurify)"):
        pytest.skip("marked et DOMPurify (CDN) indisponibles")
    assert out.locator("h2").inner_text() == "Titre"  # not uppercased by the section titles
    assert "##" not in out.inner_text()
    assert out.locator("strong").inner_text() == "résultat"
    assert out.get_attribute("tabindex") == "0" and out.get_attribute("role") == "region"


def test_follow_up_shows_the_earlier_title_not_its_id(jarvis):
    now = time.time()
    emit_task(jarvis, id="a1", title="Comparatif", status="done", output="x", started=now - 90, ended=now - 80)
    emit_task(jarvis, id="a2", title="Précisions", status="running", resumed_from="a1", started=now - 5)
    assert f"suite de «{NNBSP}Comparatif{NNBSP}»" in jarvis.inner_text("#task-a2 .meta")
    assert "a1" not in jarvis.inner_text("#task-a2 .meta")


def test_old_finished_tasks_go_into_the_history(jarvis):
    now = time.time()
    emit_task(jarvis, id="h1", title="Ancienne", status="done", output="x", started=now - 9000, ended=now - 8000)
    emit_task(jarvis, id="h2", title="Récente", status="done", output="y", started=now - 100, ended=now - 50)
    hist = jarvis.locator("#taskList > details.task-history")
    assert hist.locator("summary").inner_text() == "Historique (1)"
    assert hist.locator(".task .t").text_content() == "Ancienne"
    assert not hist.evaluate("d => d.open")
    assert order(jarvis) == [["Récente", "Terminée"]]


def test_retry_posts_the_original_prompt_and_profile(jarvis):
    bodies = []

    def fake_create(route):
        bodies.append(route.request.post_data_json)
        route.fulfill(json={"id": "new1", "title": "Prix", "status": "running"})

    jarvis.route("**/api/tasks", fake_create)
    emit_task(jarvis, id="x1", title="Prix", status="error", prompt="Compare les prix des aspirateurs",
              profile="lecture", complexity="complexe", output="Claude Code s'est arrêté.", ended=time.time())
    jarvis.click("#task-x1 .actions button.retry")
    jarvis.wait_for_selector(".toast:has-text('Nouvel essai')")
    assert bodies == [{"prompt": "Compare les prix des aspirateurs", "title": "Prix", "profile": "lecture",
                       "complexity": "complexe"}]


def test_retry_of_a_complet_task_asks_first_and_never_posts_complet_holds(jarvis, no_pending):
    from jarvis import tasks
    created = requests_to(jarvis, "/api/tasks")
    old = {"id": "c0mplet1", "title": "Ménage", "prompt": "Vide la corbeille", "profile": "complet",
           "complexity": "normale", "model": "sonnet", "origin": "voix", "status": "error",
           "output": "Échec", "progress": "", "steps": 1, "started": time.time() - 60, "ended": time.time(),
           "session_id": None, "resumed_from": None, "files": [], "permission_denials": []}
    tasks.TASKS[old["id"]] = dict(old)
    try:
        jarvis.evaluate("tk => __jarvis.bus.emit('server:task', tk)", old)
        jarvis.click("#task-c0mplet1 .actions button.retry")
        card = jarvis.locator(".card.confirm[data-state='pending']")
        card.wait_for()
        assert "Ménage" in card.inner_text()
        # The panel shows the request too, first, with Lancer / Annuler.
        waiting = jarvis.locator("#taskList > .task.waiting")
        waiting.wait_for()
        assert waiting.locator(".lbl").inner_text() == "En attente de confirmation"
        assert order(jarvis)[0][0] == "Ménage"
        assert not [t for t in tasks.TASKS.values() if t["profile"] == "complet" and t["status"] in tasks.ACTIVE]
        assert not [r for r in created if r[0] == "POST"]
        # Annuler from the panel: nothing runs, both cards settle.
        waiting.locator("button.dismiss").click()
        jarvis.wait_for_selector("#taskList > .task.waiting", state="detached")
        jarvis.wait_for_selector(".card[data-state='cancelled']:has-text('Ménage')")
        assert not [t for t in tasks.TASKS.values() if t["profile"] == "complet" and t["status"] in tasks.ACTIVE]
        assert not [r for r in created if r[0] == "POST"]
    finally:
        tasks.TASKS.pop(old["id"], None)


def test_continue_puts_a_follow_up_in_the_composer(jarvis):
    jarvis.evaluate("window.__composed = []; __jarvis.bus.on('ui:compose', d => __composed.push(d)); 0")
    emit_task(jarvis, id="k1", title="Comparatif", status="done", output="Trois modèles.", ended=time.time())
    jarvis.click("#task-k1 .actions button.continue")
    composed = jarvis.evaluate("__composed")
    assert composed == [{"text": f"Suite de «{NNBSP}Comparatif{NNBSP}»{NNBSP}: "}]
    if jarvis.locator("#askInput").count():
        jarvis.wait_for_function("document.getElementById('askInput').value.startsWith('Suite de')")


def test_copy_puts_the_whole_output_on_the_clipboard(jarvis):
    jarvis.context.grant_permissions(["clipboard-read", "clipboard-write"])
    output = "Résultat complet\n" + "ligne\n" * 400
    emit_task(jarvis, id="cp1", title="Long", status="done", output=output, ended=time.time())
    jarvis.click("#task-cp1 .actions button.copy")
    jarvis.wait_for_selector(".toast:has-text('Copié')")
    assert jarvis.evaluate("navigator.clipboard.readText()") == output


def test_approval_button_decides_the_pending_request(jarvis):
    decided = []
    jarvis.route("**/api/pending/ap1/decide", lambda route: (decided.append(route.request.post_data_json),
                                                             route.fulfill(json={"ok": True, "state": "done"})))
    emit_task(jarvis, id="p1", title="Nettoyage", status="done", output="Refusé", ended=time.time(),
              approval="ap1", permission_denials=[{"tool": "Bash", "detail": "rm -rf"}, {"tool": "Write"}])
    box = jarvis.locator("#task-p1 .approval")
    assert box.is_visible()
    assert "Bash, Write" in box.inner_text()
    box.locator("button.approve").click()
    jarvis.wait_for_timeout(300)
    assert decided == [{"decision": "oui"}]
    emit_task(jarvis, id="p1", title="Nettoyage", status="done", output="Refusé", ended=time.time())
    assert box.is_hidden()


def test_cancel_a_running_task(jarvis):
    calls = requests_to(jarvis, "/cancel")
    jarvis.route("**/api/task/run9/cancel", lambda route: route.fulfill(json={"ok": True}))
    emit_task(jarvis, id="run9", title="Longue", status="running")
    btn = jarvis.locator("#task-run9 button.cancel")
    assert btn.get_attribute("aria-label") == f"Annuler la tâche «{NNBSP}Longue{NNBSP}»"
    btn.click()
    jarvis.wait_for_timeout(200)
    assert [c[0] for c in calls] == ["POST"]


# ---------------------------------------------------------------- the task viewer

def test_lire_opens_the_viewer_esc_closes_it_and_focus_returns(jarvis):
    emit_task(jarvis, id="v1", title="Analyse ventes", status="done", prompt="Analyse ventes.xlsx",
              profile="lecture", complexity="complexe", model="opus", steps=7, cost_usd=0.4321,
              output="## Synthèse\n\nEn hausse.", started=time.time() - 125, ended=time.time() - 5,
              files=["/tmp/jarvis-e2e/rapport.md"], permission_denials=[{"tool": "Bash", "detail": "Commande : rm x"}])
    read = jarvis.locator("#task-v1 button.read")
    assert read.get_attribute("aria-label") == f"Lire la tâche «{NNBSP}Analyse ventes{NNBSP}»"
    read.click()
    jarvis.wait_for_selector("#taskView[open]")
    dlg = jarvis.locator("#taskView")
    assert jarvis.evaluate("document.getElementById('taskView').matches(':modal')")
    assert jarvis.evaluate("document.getElementById('taskView').contains(document.activeElement)")
    assert dlg.locator("#tvTitle").inner_text() == "Analyse ventes"
    text = dlg.inner_text()
    for part in ("Lecture seule", "opus", "Complexe", "2 min 00 s", "7", "0,43 $ (estimation, équivalent API)",
                 "Analyse ventes.xlsx", "/tmp/jarvis-e2e/rapport.md", "Afficher dans l'explorateur",
                 "Claude n'a pas eu le droit de", "Bash"):
        assert part in text, part
    if jarvis.evaluate("!!(window.marked && window.DOMPurify)"):
        assert dlg.locator(".tv-md h2").inner_text() == "Synthèse"
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_function("!document.getElementById('taskView').open")
    assert jarvis.evaluate("document.activeElement === document.querySelector('#task-v1 button.read')")


def test_viewer_focus_returns_even_when_the_card_was_redrawn(jarvis):
    emit_task(jarvis, id="v2", title="Veille", status="running", progress="Lecture")
    jarvis.click("#task-v2 button.read")
    jarvis.wait_for_selector("#taskView[open]")
    # It ends while the viewer is open: the card's buttons change, the viewer follows.
    emit_task(jarvis, id="v2", title="Veille", status="done", output="Terminé, voici.", ended=time.time())
    jarvis.wait_for_function("document.querySelector('#taskView .tv-status').dataset.status === 'done'")
    jarvis.click("#taskView .tv-head .x")
    jarvis.wait_for_function("!document.getElementById('taskView').open")
    assert jarvis.evaluate("document.activeElement === document.querySelector('#task-v2 button.read')")


def test_viewer_shows_the_real_task_with_its_log(jarvis):
    task = jarvis.evaluate("b => __jarvis.api('/api/tasks', {method: 'POST', body: b})",
                           {"prompt": "quel temps à Lyon ?", "title": "Météo", "profile": "recherche"})
    jarvis.wait_for_selector(f"#task-{task['id']}.done", timeout=15_000)
    jarvis.click(f"#task-{task['id']} button.read")
    jarvis.wait_for_selector("#taskView[open]")
    jarvis.wait_for_function("document.querySelectorAll('#taskView .tv-steps li time').length >= 2")
    steps = jarvis.inner_text("#taskView .tv-steps")
    assert "Recherche web" in steps and "Fin" in steps
    assert "18 degres" in jarvis.inner_text("#taskView .tv-md")


def test_reveal_shows_the_file_or_hides_itself_when_unavailable(jarvis, tmp_path, monkeypatch):
    from jarvis import desktop, tasks
    report = tmp_path / "rapport.md"
    report.write_text("x", encoding="utf-8")
    snap = {"id": "f1le", "title": "Rapport", "status": "done", "profile": "lecture", "prompt": "p",
            "origin": "voix", "started": time.time() - 9, "ended": time.time(), "output": "fait",
            "files": [str(report)], "permission_denials": []}
    tasks.TASKS["f1le"] = dict(snap)
    shown = []
    monkeypatch.setattr(desktop, "reveal_in_explorer", lambda path: shown.append(path))
    try:
        jarvis.evaluate("tk => __jarvis.bus.emit('server:task', tk)", snap)
        jarvis.click("#task-f1le button.read")
        jarvis.click("#taskView button.reveal")
        jarvis.wait_for_timeout(300)
        assert shown == [str(report)]
        # The stub of a system that can't (501): the buttons go, a toast says why.
        def not_here(path):
            raise NotImplementedError("Afficher dans l'explorateur : pas encore disponible.")
        monkeypatch.setattr(desktop, "reveal_in_explorer", not_here)
        jarvis.click("#taskView button.reveal")
        jarvis.wait_for_selector(".toast:has-text('explorateur')")
        assert jarvis.locator("#taskView button.reveal").count() == 0
        assert jarvis.evaluate("document.getElementById('taskView').contains(document.activeElement)")
    finally:
        tasks.TASKS.pop("f1le", None)


# ---------------------------------------------------------------- reminders and memory

def schedules(page, items):
    page.evaluate("items => __jarvis.bus.emit('server:schedules', {items})", items)


def test_reminders_by_day_in_two_line_rows(jarvis):
    # Midnight today, by the page's clock: one item a minute after it (today,
    # overdue or not), one tomorrow evening, one in 40 days.
    midnight = jarvis.evaluate("""(() => { const d = new Date(); d.setHours(0, 0, 0, 0);
      return d.getTime() / 1000; })()""")
    items = [{"id": "s3", "kind": "reminder", "title": "Impôts", "due": midnight + 40 * 86400},
             {"id": "s2", "kind": "task", "title": "Point météo", "due": midnight + 86400 + 22 * 3600, "repeat": "daily"},
             {"id": "s1", "kind": "reminder", "title": "Thé", "due": midnight + 60}]
    schedules(jarvis, items)
    groups = jarvis.evaluate("[...document.querySelectorAll('#scheduleList .grp')].map(h => h.textContent)")
    assert groups[0] == "Aujourd'hui" and groups[-1] == "Plus tard"
    assert "Demain" in groups
    rows = jarvis.locator("#scheduleList .item")
    assert rows.nth(0).locator(".txt").inner_text() == "Thé"
    assert rows.nth(1).locator(".tag").inner_text() == "Routine"
    assert "chaque jour" in rows.nth(1).locator(".when").inner_text()
    assert jarvis.text_content("#schedules summary h2") == "Rappels & routines (3)"
    # Two lines: the time under the text, the ✕ beside both.
    box = lambda sel: rows.nth(0).locator(sel).bounding_box()
    assert box(".when")["y"] > box(".txt")["y"]
    assert box(".x")["x"] > box(".txt")["x"] + box(".txt")["width"] - 1


def test_deleting_a_reminder_can_be_undone_within_6_seconds(jarvis):
    deletes = []
    jarvis.route("**/api/schedules/u1", lambda route: (deletes.append(route.request.method),
                                                       route.fulfill(json={"ok": True, "removed": 1})))
    item = {"id": "u1", "kind": "reminder", "title": "Pain", "due": time.time() + 3600}
    schedules(jarvis, [item])
    jarvis.click("#scheduleList .item button.x")
    assert jarvis.locator("#scheduleList .item").count() == 0  # hidden at once
    toast = jarvis.locator(".toast:has-text('Rappel supprimé')")
    assert toast.locator("button").inner_text() == "Annuler"
    jarvis.wait_for_timeout(2000)
    toast.locator("button").click()
    assert jarvis.locator("#scheduleList .item").count() == 1  # back
    jarvis.mouse.move(5, 895)
    jarvis.wait_for_timeout(6500)
    assert deletes == []  # 'Annuler' kept the DELETE from leaving


def test_deleting_a_reminder_sends_the_delete_when_the_toast_goes(jarvis):
    deletes = []
    jarvis.route("**/api/schedules/u2", lambda route: (deletes.append(route.request.method),
                                                       route.fulfill(json={"ok": True, "removed": 1})))
    item = {"id": "u2", "kind": "reminder", "title": "Linge", "due": time.time() + 3600}
    schedules(jarvis, [item])
    jarvis.click("#scheduleList .item button.x")
    jarvis.mouse.move(5, 895)
    jarvis.wait_for_timeout(3000)
    assert deletes == []
    # A server push meanwhile (another reminder added) does not bring it back.
    schedules(jarvis, [item, {"id": "u3", "kind": "reminder", "title": "Autre", "due": time.time() + 7200}])
    assert jarvis.inner_text("#scheduleList").count("Linge") == 0
    jarvis.wait_for_function("!document.querySelector('.toast')", timeout=8000)
    jarvis.wait_for_timeout(300)
    assert deletes == ["DELETE"]


def test_memory_shows_text_with_its_date_on_hover_and_undo(jarvis):
    deletes = []
    jarvis.route("**/api/memory/m1", lambda route: (deletes.append(route.request.method),
                                                    route.fulfill(json={"ok": True, "removed": 1})))
    created = time.time() - 86400 * 3
    jarvis.evaluate("f => __jarvis.bus.emit('server:memory', {facts: f})",
                    [{"id": "m1", "text": "Préfère le thé", "created": created},
                     {"id": "m2", "text": "Habite à Laon", "created": created + 60}])
    rows = jarvis.locator("#memoryList .item")
    assert rows.nth(0).locator(".txt").inner_text() == "Habite à Laon"  # newest first
    assert rows.nth(1).locator(".txt").get_attribute("title").startswith("Retenu le ")
    assert jarvis.text_content("#memory summary h2") == "Mémoire (2)"
    rows.nth(1).locator("button.x").click()
    assert jarvis.locator("#memoryList .item").count() == 1
    jarvis.locator(".toast:has-text('Souvenir oublié') button").click()
    assert jarvis.locator("#memoryList .item").count() == 2
    assert deletes == []


def test_editing_appears_only_when_the_server_can_patch(jarvis, reload_jarvis):
    # This version has no PATCH routes yet (WP14, WP17): no ✎ at all.
    jarvis.evaluate("f => __jarvis.bus.emit('server:memory', {facts: f})", [{"id": "m9", "text": "Thé vert"}])
    jarvis.wait_for_timeout(300)
    assert jarvis.locator("#memoryList .edit").count() == 0
    # A server that has them (simulated): ✎ edits in place and PATCHes the text.
    patches = []

    def memory_route(route):
        if route.request.method == "PATCH":
            patches.append((route.request.url.rsplit("/", 1)[-1], route.request.post_data_json))
            route.fulfill(status=404 if "__sonde__" in route.request.url else 200,
                          json={"detail": "Souvenir inconnu."} if "__sonde__" in route.request.url else {"ok": True})
        else:
            route.continue_()

    jarvis.route("**/api/memory/*", memory_route)
    reload_jarvis()
    jarvis.wait_for_function("__jarvis.state.synced")
    jarvis.evaluate("f => __jarvis.bus.emit('server:memory', {facts: f})", [{"id": "m9", "text": "Thé vert"}])
    edit = jarvis.locator("#memoryList .item .edit")
    edit.wait_for()
    assert edit.get_attribute("aria-label") == f"Modifier «{NNBSP}Thé vert{NNBSP}»"
    edit.click()
    field = jarvis.locator("#memoryList .edit-form input")
    assert jarvis.evaluate("document.activeElement === document.querySelector('#memoryList .edit-form input')")
    field.fill("Thé vert sans sucre")
    field.press("Enter")
    jarvis.wait_for_selector("#memoryList .edit-form", state="detached")
    assert ("m9", {"text": "Thé vert sans sucre"}) in patches
    assert jarvis.inner_text("#memoryList .item .txt") == "Thé vert sans sucre"


def test_sections_are_details_with_counts_and_empty_states(jarvis):
    jarvis.evaluate("__jarvis.bus.emit('server:schedules', {items: []}); __jarvis.bus.emit('server:memory', {facts: []})")
    for sec in ("tasks", "schedules", "memory"):
        assert jarvis.evaluate(f"document.querySelector('#{sec} > details.side-sec').open")
    assert jarvis.inner_text("#scheduleList").startswith("Aucun rappel.")
    assert jarvis.inner_text("#memoryList").startswith("Je ne sais encore rien de vous.")
    assert jarvis.text_content("#schedules summary h2") == "Rappels & routines"
    # Folded once, folded after a reload too (remembered on this PC).
    jarvis.click("#memory summary")
    assert not jarvis.evaluate("document.querySelector('#memory > details').open")
    jarvis.wait_for_function("JSON.stringify(__jarvis.settings.get('panels.folded')) === '[\"memory\"]'")


def test_untrusted_task_fields_stay_text(jarvis):
    evil = "<img src=x onerror=\"window.__pwned=1\">"
    emit_task(jarvis, id="ev1", title=evil, status="done", output=evil + " **gras**", prompt=evil,
              progress=evil, ended=time.time(), files=[evil], permission_denials=[{"tool": evil, "detail": evil}],
              approval="zz", resumed_from=evil)
    jarvis.click("#task-ev1 button.read")
    jarvis.wait_for_selector("#taskView[open]")
    jarvis.wait_for_timeout(400)
    assert jarvis.evaluate("window.__pwned") is None
    assert jarvis.evaluate("""[...document.querySelectorAll('#side *, #taskView *')]
      .every(e => ![...e.attributes].some(a => a.name.startsWith('on')))""")
    assert evil in jarvis.inner_text("#task-ev1 .t")
    assert evil in jarvis.inner_text("#task-ev1 .prog") or jarvis.inner_text("#task-ev1 .prog") == ""
    assert evil in jarvis.inner_text("#taskView .tv-prompt .tv-text")
    assert evil in jarvis.inner_text("#taskView .tv-files")
    assert evil in jarvis.inner_text("#taskView .tv-denials")


def test_axe_finds_nothing_serious_in_the_panel_and_the_viewer(jarvis):
    from test_a11y import axe_violations
    now = time.time()
    emit_task(jarvis, id="ax1", title="Comparatif", status="done", prompt="Compare " * 300,
              output="## Synthèse\n\n" + "Une ligne de résultat.\n\n" * 30, ended=now, files=["/tmp/a.md"],
              permission_denials=[{"tool": "Bash", "detail": "rm"}], approval="zz", steps=4, cost_usd=0.1)
    emit_task(jarvis, id="ax2", title="En route", status="running", progress="Recherche web")
    emit_task(jarvis, id="ax3", title="Ratée", status="error", prompt="p", output="Erreur", ended=now)
    emit_task(jarvis, id="ax4", title="Ancienne", status="done", output="x", started=now - 9000, ended=now - 8000)
    jarvis.evaluate("""p => __jarvis.bus.emit('server:pending', {pending: p})""",
                    {"id": "axp", "name": "delegate_to_claude", "summary": "Confier : « Sauvegarde ».",
                     "detail": "Copie", "state": "pending", "expires_in": 80})
    schedules(jarvis, [{"id": "s1", "kind": "task", "title": "Point météo", "due": now + 600, "repeat": "daily"},
                       {"id": "s2", "kind": "reminder", "title": "Garage", "due": now + 86400 * 20}])
    jarvis.evaluate("f => __jarvis.bus.emit('server:memory', {facts: f})", [{"id": "m1", "text": "Thé", "created": now}])
    jarvis.wait_for_timeout(400)
    assert axe_violations(jarvis) == []
    jarvis.click("#task-ax1 button.read")
    jarvis.wait_for_selector("#taskView[open]")
    jarvis.wait_for_timeout(400)
    assert axe_violations(jarvis) == []
