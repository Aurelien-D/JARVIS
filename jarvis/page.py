"""The two HTML pages the server renders: the JARVIS page (index.html) and the
pairing page (pair.html) a remote device sees before it is paired.

Values are html-escaped before they go into an attribute, so nothing a caller
controls can close the attribute and inject markup.
"""
import html

from . import config

PAIR_STATES = ("pair", "closed", "off", "paused", "refused", "locked", "revoked")


def index_html(token: str, *, remote: bool = False, origin: str = "pc") -> str:
    """index.html with the page token, whether this page is remote, and its origin string."""
    text = (config.ROOT / "index.html").read_text(encoding="utf-8")
    return (text.replace("__JARVIS_TOKEN__", html.escape(str(token)))
                .replace("__JARVIS_REMOTE__", "1" if remote else "0")
                .replace("__JARVIS_ORIGIN__", html.escape(str(origin or "pc"))))


def pairing_html(state: str) -> str:
    """pair.html for one of PAIR_STATES (anything else is shown as refused)."""
    state = state if state in PAIR_STATES else "refused"
    text = (config.ROOT / "pair.html").read_text(encoding="utf-8")
    return text.replace("__PAIR_STATE__", html.escape(state))
