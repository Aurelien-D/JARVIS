"""The second loopback listener, the only door Tailscale Serve uses.

It runs only while remote access is on: switching off closes its socket. It
serves the same app as the PC's listener, on its own uvicorn.Server and event
loop (events.py is thread-safe across loops), with lifespan off so the
scheduler never starts twice.

- The socket is bound here, before uvicorn sees it: a port clash fails at once
  with its errno (no sys.exit inside a thread, no race on server.started).
- SO_EXCLUSIVEADDRUSE on Windows: no other process may share the port.
- A new Server and Config per start (a uvicorn.Server cannot be reused), with
  log_config=None so the logging setup of the running app is left alone.
"""
import socket
import threading

import uvicorn

from . import config, events

_APP = None
_lock = threading.Lock()
_run = {"server": None, "thread": None, "port": 0, "error": ""}
STOP_WAIT_S = 5


def configure(app) -> None:
    """The app both listeners serve (server.py calls this once it is built)."""
    global _APP
    _APP = app


def _bind(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: no other process may share the port
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        elif not config.IS_WINDOWS:
            # POSIX only (never Windows, where it would let another process share
            # the port): a switch off then on again must not wait for TIME_WAIT.
            # A second listener on the port still fails to bind.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))  # literal: checked by the bind test
        s.listen(128)
        s.setblocking(False)
    except OSError:
        s.close()
        raise
    return s


def _running() -> bool:
    thread = _run["thread"]
    return thread is not None and thread.is_alive() and not _run["server"].should_exit


def state() -> dict:
    with _lock:
        port = _run["port"] if _running() else config.REMOTE_PORT
        return {"running": _running(), "port": port, "error": _run["error"]}


def start() -> dict:
    """Start serving on 127.0.0.1:REMOTE_PORT; the state, with a French error
    when the port is taken. Already running: the current state."""
    from . import audit  # late: audit is imported by modules this one serves
    with _lock:
        if _running():
            return {"running": True, "port": _run["port"], "error": ""}
        port = config.REMOTE_PORT
        if _APP is None:
            _run["error"] = "Serveur JARVIS pas encore prêt."
            return {"running": False, "port": port, "error": _run["error"]}
        try:
            sock = _bind(port)  # a clash fails here, synchronously, with its errno
        except OSError:
            _run["error"] = f"Port {port} déjà utilisé : choisissez un autre JARVIS_REMOTE_PORT."
            clash = True
        else:
            clash = False
            srv = uvicorn.Server(uvicorn.Config(_APP, host="127.0.0.1", port=port,
                                                proxy_headers=False, forwarded_allow_ips="", lifespan="off",
                                                access_log=False, log_config=None, log_level="warning",
                                                timeout_graceful_shutdown=2))
            thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, name="jarvis-remote-listener",
                                      daemon=True)
            _run.update(server=srv, thread=thread, port=port, error="")
            thread.start()
    if clash:
        audit.alert("listener_error", f"Accès à distance : le port {port} est déjà utilisé.")
        return {"running": False, "port": port, "error": _run["error"]}
    return {"running": True, "port": port, "error": ""}


def stop() -> None:
    """Close the remote streams, then the listener (its socket with it). Safe
    to call when nothing runs."""
    with _lock:
        srv, thread = _run["server"], _run["thread"]
        _run.update(server=None, thread=None, error="")
    if srv is None:
        return
    # A phone never hangs up its event stream: end those first, or uvicorn
    # would wait for them until its graceful timeout.
    events.close_streams(lambda c: c is not None and c.remote)
    srv.should_exit = True
    if thread is not None and thread is not threading.current_thread():
        thread.join(STOP_WAIT_S)


def start_if_enabled() -> None:
    """At startup: the listener comes back when remote access was left on."""
    from . import remote  # late: remote imports this module inside its functions too
    if remote.is_enabled():
        start()
