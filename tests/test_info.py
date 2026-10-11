"""Instant information: Open-Meteo weather and RSS headlines through a fake
HTTP transport (never the network), in French, cached, never raising."""
import json

import httpx
import pytest

from jarvis import config, confirm, info, instructions, store, tools

GEO = {"results": [{"name": "Nantes", "latitude": 47.2184, "longitude": -1.5536, "country": "France",
                    "admin1": "Hauts-de-France"}]}
FORECAST = {
    "current": {"time": "2026-10-09T22:30", "temperature_2m": 13.5, "precipitation": 0.1, "weather_code": 51},
    "daily": {"time": ["2026-10-09", "2026-10-10", "2026-10-11"], "weather_code": [53, 61, 3],
              "temperature_2m_max": [13.7, 14.9, 14.8], "temperature_2m_min": [5.9, 9.3, -0.2],
              "precipitation_probability_max": [78, 98, 10]},
}


def rss(n: int, title="Titre") -> bytes:
    items = "".join(f"<item><title><![CDATA[{title} {i} <b>gras</b> &amp; co]]></title>"
                    f"<link>https://www.lemonde.fr/article-{i}.html</link></item>" for i in range(n))
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Le Monde</title>'
            f"{items}</channel></rss>").encode("utf-8")


ATOM = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>'
        b'<entry><title>Premier billet</title><link href="https://blog.example/1"/></entry>'
        b'<entry><title type="html">Deuxi&#232;me billet</title><link href="javascript:alert(1)"/></entry></feed>')


@pytest.fixture
def web(monkeypatch):
    """A fake internet: routes[host] = callable(request) -> httpx.Response."""
    seen, routes = [], {}

    def handler(request):
        seen.append(request)
        route = routes.get(request.url.host)
        if route is None:
            raise httpx.ConnectError("hôte inconnu", request=request)
        return route(request)

    info._forecasts.clear()
    info._feeds.clear()
    monkeypatch.setattr(info, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(config, "CITY", "")
    routes["geocoding-api.open-meteo.com"] = lambda r: httpx.Response(200, json=GEO)
    routes["api.open-meteo.com"] = lambda r: httpx.Response(200, json=FORECAST)
    yield seen, routes
    info._forecasts.clear()
    info._feeds.clear()


def hosts(seen) -> list:
    return [r.url.host for r in seen]


# ---------------------------------------------------------------- weather

def test_tomorrow_in_nantes_in_french_with_one_geocode(web):
    seen, _ = web
    out = info.weather("Nantes", "demain")
    assert out["ok"] is True
    assert out["text"] == "Demain à Nantes : pluie faible, de 9 à 15 °C, risque de pluie 98 %."
    assert out["source"] == "Météo : Open-Meteo.com (CC-BY 4.0)"
    assert (out["min"], out["max"], out["rain_probability"]) == (9.3, 14.9, 98)
    again = info.weather("nantes", "aujourdhui")  # another case: the same city
    assert again["text"] == "Aujourd'hui à Nantes : bruine modérée, de 6 à 14 °C, risque de pluie 78 %."
    assert hosts(seen).count("geocoding-api.open-meteo.com") == 1
    assert hosts(seen).count("api.open-meteo.com") == 1  # the forecast is kept 15 minutes
    geo = seen[0].url.params
    assert (geo["name"], geo["count"], geo["language"]) == ("Nantes", "1", "fr")
    fc = seen[1].url.params
    assert fc["current"] == "temperature_2m,precipitation,weather_code"
    assert fc["daily"] == "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
    assert fc["timezone"] == "auto"
    # The coordinates are kept for good (state.json), even after a restart's empty caches.
    info._forecasts.clear()
    assert store.load(info.STATE_FILE, {})["geocode"]["nantes"]["lat"] == 47.2184
    info.weather("Nantes", "demain")
    assert hosts(seen).count("geocoding-api.open-meteo.com") == 1


def test_right_now_and_the_settings_city(web, monkeypatch):
    monkeypatch.setattr(config, "CITY", "Nantes")
    out = info.weather(None, "maintenant")
    assert out["text"] == ("À Nantes, en ce moment : 14 °C, bruine légère, 0,1 mm de précipitations. "
                           "Aujourd'hui : bruine modérée, de 6 à 14 °C, risque de pluie 78 %.")
    assert info.weather_text(None, "apres-demain") == "Après-demain à Nantes : couvert, de 0 à 15 °C, risque de pluie 10 %."
    monkeypatch.setattr(config, "CITY", "")
    assert "Quelle ville" in info.weather()["error"]


def test_weather_codes_in_french():
    assert info.describe(61) == "pluie faible"
    assert info.describe(0) == "ciel dégagé" and info.describe(95) == "orage"
    assert info.describe(1234) == "conditions inconnues"
    assert info.describe(None) == "conditions inconnues" and info.describe("x") == "conditions inconnues"


def test_a_network_timeout_is_said_never_raised(web):
    _, routes = web

    def slow(request):
        raise httpx.ReadTimeout("trop lent", request=request)

    routes["geocoding-api.open-meteo.com"] = slow
    out = info.weather("Nantes", "demain")
    assert out["ok"] is False and out["error"].startswith("Météo indisponible")
    routes["geocoding-api.open-meteo.com"] = lambda r: httpx.Response(200, json=GEO)
    routes["api.open-meteo.com"] = lambda r: httpx.Response(429, json={"reason": "quota"})
    assert info.weather("Nantes")["error"] == info.T.busy
    routes["api.open-meteo.com"] = lambda r: httpx.Response(200, json={"daily": {}})
    assert info.weather("Nantes", "demain")["error"] == info.T.bad
    routes["geocoding-api.open-meteo.com"] = lambda r: httpx.Response(200, json={})
    assert info.weather("Atlantide")["error"] == "Ville introuvable pour la météo : « Atlantide »."


# ---------------------------------------------------------------- news

def test_an_rss_feed_gives_five_clean_titles(web, monkeypatch):
    _, routes = web
    monkeypatch.setattr(config, "NEWS_FEEDS", "https://www.lemonde.fr/rss/une.xml")
    routes["www.lemonde.fr"] = lambda r: httpx.Response(200, content=rss(8), headers={"Content-Type": "application/xml"})
    out = info.news(5)
    assert out["ok"] and len(out["headlines"]) == 5
    assert out["headlines"][0] == {"title": "Titre 0 gras & co", "link": "https://www.lemonde.fr/article-0.html",
                                   "source": "www.lemonde.fr"}
    assert "DONNÉES" in out["note"]


def test_several_feeds_take_turns_and_atom_works(web, monkeypatch):
    _, routes = web
    monkeypatch.setattr(config, "NEWS_FEEDS", "https://a.example/rss, https://blog.example/atom.xml")
    routes["a.example"] = lambda r: httpx.Response(200, content=rss(5, "Info A"))
    routes["blog.example"] = lambda r: httpx.Response(200, content=ATOM)
    titles = [h["title"] for h in info.news(4)["headlines"]]
    assert titles == ["Info A 0 gras & co", "Premier billet", "Info A 1 gras & co", "Deuxième billet"]
    links = {h["title"]: h["link"] for h in info.news(5)["headlines"]}
    assert links["Deuxième billet"] == ""  # only web links are kept


def test_a_malformed_feed_gives_a_french_error_and_no_exception(web, monkeypatch):
    _, routes = web
    monkeypatch.setattr(config, "NEWS_FEEDS", "https://www.lemonde.fr/rss/une.xml")
    routes["www.lemonde.fr"] = lambda r: httpx.Response(200, content="<rss><channel><item><title>coupé".encode())
    out = info.news()
    assert out == {"ok": False, "error": "Actualités indisponibles : le flux www.lemonde.fr est illisible"}
    routes["www.lemonde.fr"] = lambda r: httpx.Response(200, content=b"<html><body>Pas un flux</body></html>")
    info._feeds.clear()
    assert info.news()["ok"] is False
    bomb = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b"<rss><channel><item><title>&b;</title></item></channel></rss>")
    routes["www.lemonde.fr"] = lambda r: httpx.Response(200, content=bomb)
    info._feeds.clear()
    assert "illisible" in info.news()["error"]
    routes["www.lemonde.fr"] = lambda r: httpx.Response(200, content=b"<rss>" + b" " * (info.MAX_FEED_BYTES + 10))
    info._feeds.clear()
    assert "trop volumineux" in info.news()["error"]
    routes["www.lemonde.fr"] = lambda r: httpx.Response(503)
    info._feeds.clear()
    assert "ne répond pas" in info.news()["error"]
    monkeypatch.setattr(config, "NEWS_FEEDS", "")
    assert info.news()["error"] == info.T.no_feed


# ---------------------------------------------------------------- the tool

def test_the_info_tool_taints_the_session_for_news_only(web, monkeypatch):
    _, routes = web
    monkeypatch.setattr(config, "NEWS_FEEDS", "https://www.lemonde.fr/rss/une.xml")
    routes["www.lemonde.fr"] = lambda r: httpx.Response(200, content=rss(6))
    confirm.SESSIONS.clear()
    sid = confirm.new_session()
    ctx = tools.ToolCtx(session_id=sid)
    weather = tools.run_tool("info", {"type": "meteo", "ville": "Nantes", "quand": "demain"}, ctx)
    assert weather["ok"] and "Nantes" in weather["text"] and not confirm.is_tainted(sid)
    headlines = tools.run_tool("info", {"type": "actus"}, ctx)
    assert len(headlines["headlines"]) == 5 and confirm.is_tainted(sid)
    assert confirm.SESSIONS[sid]["last_turn"] == 0.0
    assert tools.run_tool("info", {"type": "horoscope"})["ok"] is False
    confirm.SESSIONS.clear()


def test_the_instructions_send_weather_and_news_to_the_tool(monkeypatch):
    monkeypatch.setattr(config, "CITY", "Nantes")
    text = instructions.build_instructions()
    assert "outil info (instantané), jamais delegate_to_claude" in text.replace("Outil info", "outil info")
    assert "Ville de monsieur par défaut : Nantes." in text
    schema = next(t for t in tools.session_tools() if t["name"] == "info")
    assert schema["parameters"]["properties"]["type"]["enum"] == ["meteo", "actus"]


def test_no_network_by_default_in_tests():
    """The test harness itself never lets a weather call out."""
    out = info.weather("Nantes")
    assert out == {"ok": False, "error": info.T.down}
    assert json.dumps(out, ensure_ascii=False).startswith('{"ok": false')
