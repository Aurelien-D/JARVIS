"""The iPhone's Home Screen web app (spec 4.15): the manifest, the icons and the
head links of both pages, and the pairing page's own rules under the remote
CSP (no inline script, no on* attribute, no CDN, strings-fr.js only)."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import server
from jarvis import page, security

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

MANIFEST = {
    "id": "/", "name": "J.A.R.V.I.S.", "short_name": "JARVIS", "lang": "fr", "dir": "ltr",
    "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#05080d", "theme_color": "#05080d",
    "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
               "purpose": "maskable"}],
}
HEAD = [
    '<link rel="manifest" href="/static/manifest.webmanifest">',
    '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">',
    '<meta name="theme-color" content="#05080d">',
    '<meta name="apple-mobile-web-app-title" content="JARVIS">',
    '<meta name="mobile-web-app-capable" content="yes">',
]


def test_manifest_fields_are_exact():
    assert json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8")) == MANIFEST


def test_icons_exist_with_their_declared_sizes():
    for icon in MANIFEST["icons"]:
        path = ROOT / icon["src"].lstrip("/")
        with Image.open(path) as img:
            assert img.format == "PNG", path
            assert "x".join(map(str, img.size)) == icon["sizes"], path
    with Image.open(STATIC / "icons" / "apple-touch-icon.png") as img:
        assert img.format == "PNG" and img.size == (180, 180)


def _opaque(path: Path) -> bool:
    with Image.open(path) as img:
        rgba = img.convert("RGBA")
        return rgba.getchannel("A").getextrema() == (255, 255)


def test_apple_touch_icon_is_fully_opaque_on_the_dark_background():
    path = STATIC / "icons" / "apple-touch-icon.png"
    assert _opaque(path)  # iOS fills transparency with black or white: never leave it to chance
    with Image.open(path) as img:
        assert img.convert("RGB").getpixel((0, 0)) == (5, 8, 13)  # #05080d, the manifest's background
    # The maskable icon fills its whole square too (the mask cuts into it).
    assert _opaque(STATIC / "icons" / "icon-maskable-512.png")


def test_both_pages_link_the_web_app():
    for html in (page.index_html("jeton"), page.pairing_html("pair")):
        for tag in HEAD:
            assert tag in html, tag


def test_the_manifest_and_icons_are_served():
    api = TestClient(server.app, base_url="http://127.0.0.1:8788", headers={"X-Jarvis-Token": security.TOKEN})
    r = api.get("/static/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    for name in ("apple-touch-icon", "icon-192", "icon-512", "icon-maskable-512"):
        r = api.get(f"/static/icons/{name}.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_the_pairing_page_runs_under_the_remote_csp():
    """script-src 'self' (no inline script, no on* attribute), and nothing from a CDN."""
    html = page.pairing_html("pair")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)
    assert scripts == [""]  # one script, external
    assert '<script type="module" src="/static/js/pair.js"></script>' in html
    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE)
    assert "http://" not in html and "https://" not in html
    assert "style=" not in html  # no inline style either
    source = (STATIC / "js" / "pair.js").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*import\b.*?from\s+[\"']([^\"']+)[\"']", source, re.MULTILINE)
    assert imports == ["./strings-fr.js"]
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source and "eval(" not in source
    assert "https://" not in source and "http://" not in source
