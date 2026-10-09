"""Windows shell integration: tray icon with Quit, global hotkey (stub; WP13
fills it in). server.py starts it around the server's lifetime.
"""


def start(url: str, on_quit):
    """Show the tray icon and register the hotkey; on_quit stops JARVIS."""


def stop():
    """Remove the tray icon and release the hotkey."""
