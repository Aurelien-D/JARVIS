"""Tool family: this PC (apps, websites, instant system actions, screen).

The family interface is described in tools.py. From a paired iPhone only
open_url and the volume, music and lock actions are offered (tools.APP_TOOLS),
and open_url sends the link back to the phone instead of opening it here.
"""
import base64
from urllib.parse import urlsplit

from . import confirm, desktop

LINK_REFUSED = "Lien refusé : seules les adresses web s'envoient."
LINK_SENT = "Lien envoyé sur l'iPhone : touchez la carte pour l'ouvrir."

MONITOR = {"type": "string",
           "description": ("Target screen: 'left', 'right', 'top', 'bottom', 'primary', "
                           "or a number like '2'. Omit to leave window placement alone.")}

TOOLS = [{
    "type": "function",
    "name": "open_app",
    "description": ("Launch an application installed on this PC by name "
                    "(e.g. 'discord', 'spotify', 'chrome', 'notepad', Microsoft Store apps, "
                    "'paramètres', 'calculatrice'). Returns whether it was found and started."),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Application name as the user said it"},
            "monitor": MONITOR,
        },
        "required": ["name"],
    },
}, {
    "type": "function",
    "name": "open_url",
    "description": ("Open a website in the browser on this PC. Use for online "
                    "services: 'mes emails' -> https://mail.google.com, "
                    "'YouTube' -> https://youtube.com, etc."),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to open (https://...)"},
            "monitor": MONITOR,
        },
        "required": ["url"],
    },
}, {
    "type": "function",
    "name": "system_control",
    "description": ("Instant actions on this PC, no Claude session needed: volume, "
                    "media keys, lock screen, clipboard, save a screenshot to a file."),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [
                "volume_up", "volume_down", "set_volume", "mute", "play_pause",
                "next_track", "previous_track", "lock_screen", "read_clipboard",
                "write_clipboard", "save_screenshot"]},
            "value": {"type": "string",
                      "description": ("set_volume: level 0-100; write_clipboard: the text; "
                                      "save_screenshot: optional screen")},
        },
        "required": ["action"],
    },
}, {
    "type": "function",
    "name": "look_at_screen",
    "description": "Take a screenshot of this PC's screen and look at it.",
    "parameters": {
        "type": "object",
        "properties": {"monitor": {"type": "string",
                                   "description": "Which screen ('left', '2', 'all'...); default primary"}},
    },
}]

CLIENT_TOOLS: set = set()


def _look_at_screen(a: dict, ctx) -> dict:
    jpeg = desktop.screenshot_jpeg(a.get("monitor"))
    return {"ok": True, "image": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}


def _open_url(a: dict, ctx) -> dict:
    """On the PC: open it here. From anywhere else: nothing opens on the PC
    (monsieur may be away); the phone gets a card with the link to tap."""
    from . import remote
    if remote.kind_of(getattr(ctx, "origin", "pc")) == "pc":
        return desktop.open_target(url=a.get("url", ""), monitor=a.get("monitor"))
    link = confirm.web_address(a.get("url", ""))
    try:
        scheme = urlsplit(link).scheme.lower()
    except ValueError:
        scheme = ""
    domain = confirm._domain(link)
    if scheme not in ("http", "https") or not domain:
        return {"ok": False, "error": LINK_REFUSED}
    link = scheme + link[len(scheme):]  # 'HTTPS://…' too passes the page's own http(s) check
    return {"ok": True, "opened": False, "link": link, "domain": domain,
            "tainted": confirm.is_tainted(getattr(ctx, "session_id", None)), "message": LINK_SENT}


HANDLERS = {
    "open_app": lambda a, ctx: desktop.open_target(name=a.get("name", ""), monitor=a.get("monitor")),
    "open_url": _open_url,
    "system_control": lambda a, ctx: desktop.system_action(a.get("action", ""), a.get("value")),
    "look_at_screen": _look_at_screen,
}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
