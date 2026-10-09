"""Tool family: this PC (apps, websites, instant system actions, screen).

The family interface is described in tools.py.
"""
import base64

from . import desktop

MONITOR = {"type": "string",
           "description": ("Target screen: 'left', 'right', 'top', 'bottom', 'primary', "
                           "or a number like '2'. Omit to leave window placement alone.")}

TOOLS = [{
    "type": "function",
    "name": "open_app",
    "description": ("Launch an application installed on this PC by name "
                    "(e.g. 'discord', 'spotify', 'chrome', 'notepad'). "
                    "Returns whether it was found and started."),
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


HANDLERS = {
    "open_app": lambda a, ctx: desktop.open_target(name=a.get("name", ""), monitor=a.get("monitor")),
    "open_url": lambda a, ctx: desktop.open_target(url=a.get("url", ""), monitor=a.get("monitor")),
    "system_control": lambda a, ctx: desktop.system_action(a.get("action", ""), a.get("value")),
    "look_at_screen": _look_at_screen,
}


def available() -> bool:
    return True


def instructions_block() -> str:
    return ""
