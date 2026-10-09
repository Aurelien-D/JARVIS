"""The report window (WP11): a real modal dialog, in French, with a native
sortable table instead of Grid.js and ApexCharts loaded only when needed."""
import json

import pytest

pytestmark = pytest.mark.e2e

NNBSP = "\u202f"
APEX_HASH = "sha384-fnhrfzODrKsQTTXTYoDIc5f/SIP1KuiO4hz6PmkcIBHYVaAj8QpSOtByqodkU2iO"

SALES = {
    "title": "Ventes du trimestre",
    "kpis": [{"label": "Chiffre d'affaires", "value": "12 480 €", "delta": "+12 %"},
             {"label": "Retours", "value": 37, "delta": "-3 %"}],
    "chart": {"type": "bar", "categories": ["janvier", "février", "mars"],
              "series": [{"name": "2026", "data": [1.5, 2.25, 1234.5]}]},
    "table": {"columns": ["Mois", "CA", "Année"],
              "rows": [["mars", 1234.5, 2026], ["janvier", 980, 2026], ["février", 15, 2025], ["avril", "", 2026]]},
    "markdown": "## Conclusion\n\nEn **hausse**.",
}


def show(page, report):
    page.evaluate("r => import('/static/js/report.js').then(m => m.showReport(r))", report)
    page.wait_for_selector("#report[open]")


def wait_chart(page):
    page.wait_for_selector("#rchart[data-state='ready'], #rchart[data-state='unavailable']", timeout=20_000)
    return page.get_attribute("#rchart", "data-state")


def column(page, n):
    return page.evaluate(f"[...document.querySelectorAll('#rtable tbody tr')].map(r => r.cells[{n}].textContent)")


def test_the_report_is_a_modal_dialog_esc_closes_it_and_focus_returns(jarvis):
    jarvis.evaluate("""() => { const b = document.createElement('button'); b.id = 'opener';
      b.textContent = 'Ouvrir'; document.getElementById('controlsExtra').append(b); }""")
    jarvis.evaluate("""r => document.getElementById('opener').addEventListener('click',
      () => import('/static/js/report.js').then(m => m.showReport(r)))""", SALES)
    jarvis.click("#opener")
    jarvis.wait_for_selector("#report[open]")
    state = jarvis.evaluate("""(() => { const d = document.getElementById('report');
      return {modal: d.matches(':modal'), inside: d.contains(document.activeElement),
              label: d.getAttribute('aria-labelledby'), title: document.getElementById('rtitle').textContent}; })()""")
    assert state == {"modal": True, "inside": True, "label": "rtitle", "title": "Ventes du trimestre"}
    # The page behind is out of reach: what is under the pointer at the orb is the report.
    assert jarvis.evaluate("""(() => { const b = document.getElementById('orbBtn').getBoundingClientRect();
      const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      return document.getElementById('report').contains(hit); })()""")
    assert jarvis.evaluate("""(() => { const b = document.getElementById('panelBtn') || document.getElementById('orbBtn');
      const r = b.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + 2, r.y + 2);
      return hit === document.getElementById('report') || document.getElementById('report').contains(hit); })()""")
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_function("!document.getElementById('report').open")
    assert jarvis.evaluate("document.activeElement.id") == "opener"
    # Its ✕ is a labelled button, and also gives the focus back.
    jarvis.click("#opener")
    jarvis.wait_for_selector("#report[open]")
    assert jarvis.get_attribute("#report .rhead .x", "aria-label") == "Fermer le rapport"
    jarvis.click("#report .rhead .x")
    jarvis.wait_for_function("!document.getElementById('report').open")
    assert jarvis.evaluate("document.activeElement.id") == "opener"


def test_no_grid_js_anywhere(jarvis, reload_jarvis):
    requested = []
    jarvis.on("request", lambda r: requested.append(r.url))
    reload_jarvis()
    show(jarvis, {"title": "Tableau", "table": {"columns": ["A", "B"], "rows": [[f"l{i}", i] for i in range(30)]}})
    show(jarvis, SALES)
    wait_chart(jarvis)
    assert not [u for u in requested if "gridjs" in u.lower()]
    assert jarvis.evaluate(
        "document.querySelectorAll('.gridjs, [class*=gridjs], script[src*=gridjs], link[href*=gridjs]').length") == 0
    assert jarvis.evaluate("typeof window.gridjs") == "undefined"
    assert jarvis.locator("#rtable table").count() == 1


def test_headers_sort_the_rows_and_set_aria_sort(jarvis):
    show(jarvis, SALES)
    assert column(jarvis, 0) == ["mars", "janvier", "février", "avril"]  # as given
    assert jarvis.locator("#rtable th[aria-sort]").count() == 0
    jarvis.click("#rtable th >> nth=1 >> button")  # CA: numbers by value, the empty cell last
    assert column(jarvis, 0) == ["février", "janvier", "mars", "avril"]
    assert jarvis.get_attribute("#rtable th >> nth=1", "aria-sort") == "ascending"
    jarvis.click("#rtable th >> nth=1 >> button")
    assert column(jarvis, 0) == ["mars", "janvier", "février", "avril"]
    assert jarvis.get_attribute("#rtable th >> nth=1", "aria-sort") == "descending"
    jarvis.click("#rtable th >> nth=0 >> button")  # Mois: French alphabetical order
    assert column(jarvis, 0) == ["avril", "février", "janvier", "mars"]
    assert jarvis.get_attribute("#rtable th >> nth=0", "aria-sort") == "ascending"
    assert jarvis.get_attribute("#rtable th >> nth=1", "aria-sort") is None
    # The sort button keeps the focus (keyboard users sort again with Enter).
    jarvis.keyboard.press("Enter")
    assert jarvis.get_attribute("#rtable th >> nth=0", "aria-sort") == "descending"


def test_numbers_are_french_and_years_stay_years(jarvis):
    show(jarvis, SALES)
    assert column(jarvis, 1)[:3] == [f"1{NNBSP}234,5", "980", "15"]
    assert column(jarvis, 2)[:2] == ["2026", "2026"]  # a year column is never '2 026'
    assert jarvis.evaluate("document.querySelector('#rtable tbody td.num') !== null")
    kpis = jarvis.inner_text("#kpis")
    assert "12 480 €" in kpis and "37" in kpis
    assert jarvis.get_attribute("#kpis .kpi >> nth=1 >> .d", "class") == "d neg"
    assert jarvis.inner_html("#rmd").startswith("<h2")  # markdown notes (sanitised)


def test_a_long_table_pages_in_french(jarvis):
    rows = [[f"Ligne {i:02d}", i * 10] for i in range(60)]
    show(jarvis, {"title": "Long", "table": {"columns": ["Nom", "Valeur"], "rows": rows}})
    assert jarvis.locator("#rtable tbody tr").count() == 25
    assert jarvis.inner_text("#rtable .rtable-info") == "Lignes 1 à 25 sur 60"
    prev, nxt = jarvis.locator("#rtable .rtable-pager button").all()
    assert prev.inner_text() == "Précédent" and nxt.inner_text() == "Suivant" and prev.is_disabled()
    nxt.click()
    assert jarvis.inner_text("#rtable .rtable-info") == "Lignes 26 à 50 sur 60"
    nxt.click()
    assert jarvis.inner_text("#rtable .rtable-info") == "Lignes 51 à 60 sur 60"
    assert nxt.is_disabled() and jarvis.evaluate("document.activeElement.textContent") == "Précédent"
    jarvis.click("#rtable th >> nth=1 >> button")  # sorting goes back to the first page
    jarvis.click("#rtable th >> nth=1 >> button")
    assert jarvis.inner_text("#rtable .rtable-info") == "Lignes 1 à 25 sur 60"
    assert column(jarvis, 0)[0] == "Ligne 59"


def test_copy_csv_is_french_and_defuses_formulas(jarvis):
    jarvis.context.grant_permissions(["clipboard-read", "clipboard-write"])
    show(jarvis, {"title": "CSV", "table": {"columns": ["Nom", "Montant"],
                                            "rows": [["=HYPERLINK(\"x\")", 1234.5], ["Dupont; fils", -3]]}})
    jarvis.click("#rtable .rtable-bar button")
    jarvis.wait_for_selector(".toast:has-text('Copié')")
    csv = jarvis.evaluate("navigator.clipboard.readText()")
    # ';' and a decimal comma; the formula starts with a quote (text, never run);
    # cells with ';' or '"' are quoted the CSV way.
    assert csv.split("\r\n") == ["Nom;Montant", "\"'=HYPERLINK(\"\"x\"\")\";1234,5", "\"Dupont; fils\";-3"]


def test_chart_is_loaded_on_demand_with_its_hash_and_speaks_french(jarvis):
    assert jarvis.evaluate("typeof window.ApexCharts") == "undefined"
    assert jarvis.locator("script[src*='apexcharts']").count() == 0
    show(jarvis, {"title": "Sans graphique", "table": {"columns": ["a"], "rows": [["b"]]}})
    jarvis.wait_for_timeout(200)
    assert jarvis.locator("script[src*='apexcharts']").count() == 0  # no chart, no library
    show(jarvis, SALES)
    script = jarvis.evaluate("""(() => { const s = document.querySelector('script[data-lazy="apexcharts"]');
      return s && {integrity: s.integrity, cors: s.crossOrigin, head: s.parentElement === document.head,
                   src: s.src}; })()""")
    assert script == {"integrity": APEX_HASH, "cors": "anonymous", "head": True,
                      "src": "https://cdn.jsdelivr.net/npm/apexcharts@7.4.0/dist/apexcharts.min.js"}
    if wait_chart(jarvis) != "ready":
        pytest.skip("ApexCharts (CDN) indisponible")
    # Axis ticks grouped the French way ('1 500', never '1,500'), values with a decimal comma.
    labels = jarvis.evaluate("[...document.querySelectorAll('#rchart .apexcharts-yaxis-label tspan')].map(t => t.textContent)")
    assert any(NNBSP in t for t in labels) and not any("," in t or "." in t for t in labels), labels
    fmt = jarvis.evaluate("""import('/static/js/report.js').then(m => { const o = m.shownChartOptions();
      const y = [].concat(o.yaxis)[0];  // ApexCharts turns it into a list
      return [y.labels.formatter(1234.5), o.tooltip.y.formatter(2.25), o.chart.defaultLocale,
              o.chart.locales[0].options.shortMonths[1]]; })""")
    assert fmt == [f"1{NNBSP}234,5", "2,25", "fr", "févr."]
    assert jarvis.get_attribute("#rchart", "role") == "img"
    assert jarvis.get_attribute("#rchart", "aria-label") == f"Graphique{NNBSP}: 2026"


def test_chart_offline_says_so_and_the_table_still_shows(jarvis):
    jarvis.route("**/apexcharts@*/**", lambda route: route.abort())
    show(jarvis, SALES)
    assert wait_chart(jarvis) == "unavailable"
    assert "Graphique indisponible" in jarvis.inner_text("#rchart")
    assert jarvis.locator("#rtable tbody tr").count() == 4


def test_reduced_motion_turns_chart_animations_off(jarvis):
    jarvis.emulate_media(reduced_motion="reduce")
    show(jarvis, SALES)
    if wait_chart(jarvis) != "ready":
        pytest.skip("ApexCharts (CDN) indisponible")
    animated = jarvis.evaluate("import('/static/js/report.js').then(m => m.shownChartOptions().chart.animations.enabled)")
    assert animated is False


def test_last_report_button_reopens_it(jarvis):
    assert jarvis.locator("#lastReportBtn").is_hidden()
    show(jarvis, SALES)
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_function("!document.getElementById('report').open")
    btn = jarvis.locator("#lastReportBtn")
    assert btn.is_visible() and btn.inner_text() == "Dernier rapport"
    btn.click()
    jarvis.wait_for_selector("#report[open]")
    assert jarvis.text_content("#rtitle") == "Ventes du trimestre"
    assert jarvis.evaluate("window.lastReport.title") == "Ventes du trimestre"
    jarvis.keyboard.press("Escape")
    jarvis.wait_for_function("!document.getElementById('report').open")
    assert jarvis.evaluate("document.activeElement.id") == "lastReportBtn"


def test_a_click_on_the_backdrop_closes_it(jarvis):
    show(jarvis, {"title": "Petit"})
    jarvis.mouse.click(8, 8)
    jarvis.wait_for_function("!document.getElementById('report').open")


def test_ten_reports_in_a_row_raise_no_page_error(jarvis):
    errors = []
    jarvis.on("pageerror", lambda e: errors.append(str(e)))
    variants = [SALES, {"title": "Vide"}, {"title": "Donut", "chart": {"type": "donut", "categories": ["a", "b"],
                                                                       "series": [{"name": "x", "data": [60, 40]}]}},
                {"title": "Aire", "chart": {"type": "area", "categories": ["1", "2"],
                                            "series": [{"name": "s", "data": [1, "2,5"]}]}},
                {"title": "Bizarre", "kpis": "x", "chart": {"type": "radar", "series": "x"}, "table": {"rows": [[{}, None]]}}]
    for i in range(10):
        jarvis.evaluate("r => import('/static/js/report.js').then(m => m.showReport(r))", variants[i % len(variants)])
        jarvis.wait_for_timeout(150)
    jarvis.wait_for_timeout(500)
    assert errors == []
    assert jarvis.evaluate("document.getElementById('report').open")
    assert jarvis.locator("#rchart .apexcharts-canvas").count() <= 1  # one chart, the old ones destroyed
    # Malformed input never throws: the tool always answers.
    jarvis.evaluate("import('/static/js/report.js').then(m => { m.showReport(null); m.showReport('x'); })")
    assert jarvis.text_content("#rtitle") == "Rapport"
    assert errors == []


def test_display_report_from_the_voice_model_opens_the_dialog(jarvis):
    jarvis.click("#orbBtn")
    jarvis.wait_for_function("__jarvis.state.mode === 'live'")
    jarvis.evaluate("""a => __emit({type: 'response.done', response: {status: 'completed', output: [
      {type: 'function_call', name: 'display_report', call_id: 'r1', status: 'completed', arguments: a}]}})""",
                    json.dumps(SALES))
    jarvis.wait_for_function("__sent.some(m => m.item && m.item.call_id === 'r1')")
    out = jarvis.evaluate("JSON.parse(__sent.find(m => m.item && m.item.call_id === 'r1').item.output)")
    assert out == {"status": "displayed"}
    assert jarvis.evaluate("document.getElementById('report').matches(':modal')")


def test_axe_finds_nothing_serious_in_the_open_report(jarvis):
    from test_a11y import axe_violations
    show(jarvis, SALES)
    wait_chart(jarvis)
    jarvis.wait_for_timeout(400)  # entrance animation over: axe reads final colours
    assert axe_violations(jarvis) == []
