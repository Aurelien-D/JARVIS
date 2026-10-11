#!/usr/bin/env python3
"""JARVIS Local: realtime voice assistant that drives Claude Code sessions.

One small FastAPI server (guarded by jarvis/security.py),
reachable from this PC, and from paired devices through Tailscale Serve (jarvis/remote.py):
  GET  /                   the futuristic UI (orb, task panels, reminders, memory)
  POST /api/session        mints an ephemeral OpenAI Realtime token (your API key
                           never reaches the browser)
  POST /api/tool           runs a voice tool (Claude task, app, volume, reminder...)
  GET  /api/events         live push: task progress, reminders, briefings
  GET  /api/tasks ...      task list and cancel, reminders, memory (jarvis/api_*.py)
  POST /api/shutdown       stops JARVIS (the tray's Quit)

The browser talks to OpenAI Realtime over WebRTC for voice, and the model
calls tools; delegate_to_claude hands real work to Claude Code. Results are
read back aloud when the Claude session finishes.

  python server.py                    start, then open http://127.0.0.1:8788
  python server.py --app              start and open JARVIS in its own window
  python server.py --autostart on     launch JARVIS when Windows starts (off to undo)
"""
import argparse
import asyncio
import mimetypes
import sys
import threading
import time
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jarvis import (api_memory, api_remote, api_schedules, api_tasks, ares, audit, config, confirm,
                    desktop, events, health, inbox, journal, listener, notify, page, raccourci, realtime,
                    remote, scheduler, security, settings, shell, tailscale, tasks, tools, usage)

settings.apply_overrides()  # settings saved from the UI win over .env

SERVER = None  # the running uvicorn.Server: /api/shutdown and the tray's Quit stop it


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks.load_history()
    scheduler.start()
    # The Serve listener comes back with JARVIS when remote access was left on;
    # in a thread, as binding a socket must never hold up the main loop.
    await asyncio.to_thread(listener.start_if_enabled)
    tailscale.start_watch()
    yield
    scheduler.stop()
    tailscale.stop_watch()


# No /docs, /redoc or /openapi.json: they sit outside /api/ (no token) and the
# docs pages run unpinned CDN scripts in this origin, where they could read the token.
app = FastAPI(title="JARVIS Local", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(security.guard)
# The Windows registry can map .js to text/plain, and browsers refuse to run a
# module script served that way: pin the types StaticFiles will guess.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/manifest+json", ".webmanifest")  # the iPhone's Home Screen web app


class FreshStaticFiles(StaticFiles):
    """Always revalidate: after an update, a browser keeping some modules from its
    cache and fetching others would mix two versions and break the page."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", FreshStaticFiles(directory=config.ROOT / "static"), name="static")
for router in (api_tasks.router, api_schedules.router, api_memory.router, inbox.router,
               settings.router, health.router, usage.router, journal.router, ares.router,
               confirm.router, api_remote.router, tailscale.router, notify.router, raccourci.router):
    app.include_router(router)
listener.configure(app)  # the Serve listener serves this same app (never started here)

# ---------------------------------------------------------------- page

@app.get("/")
def index(request: Request):
    caller = remote.caller_of(request)
    if caller.remote:  # never the PC's token: the remote gate renders its own page
        return remote.render_remote_page(request, caller)
    return HTMLResponse(page.index_html(security.TOKEN), headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz():
    return {"app": "jarvis"}


def _can_patch(router, prefix: str) -> bool:
    """The router has a PATCH route under prefix: the side panel shows its ✎ only then."""
    return any(getattr(r, "path", "").startswith(prefix) and "PATCH" in (getattr(r, "methods", None) or ())
               for r in router.routes)


@app.get("/api/config")
def get_config(request: Request):
    caller = remote.caller_of(request)
    # + quiet hours, the daily cap reached, versions (settings.py). A remote page
    # never listens for the wake word: a phone in a pocket must not wake JARVIS.
    return {"wake_word": False if caller.remote else config.WAKE_WORD,
            "speech_lang": config.SPEECH_LANG, "idle_minutes": config.IDLE_MINUTES, **settings.public_config(),
            "edit": {"memory": _can_patch(api_memory.router, "/api/memory/"),
                     "schedules": _can_patch(api_schedules.router, "/api/schedules/")},
            "remote": caller.remote, "origin": caller.origin}

# ---------------------------------------------------------------- realtime session

class SessionIn(BaseModel):
    recent: str = ""  # last exchanges, so a reconnection picks up the thread
    # The voice sessions JARVIS's lines in recent were said in ("" if unknown).
    sources: list[str] | None = None


@app.post("/api/session")
def create_session(request: Request, body: SessionIn | None = None):
    caller = remote.caller_of(request)
    # A remote voice session needs a daily cap and a free slot; checking holds
    # that slot (concurrent requests can't all pass) but never counts a mint.
    refusal = remote.check_voice(caller)
    if refusal is not None:
        raise HTTPException(*refusal)
    recent = body.recent if body else ""
    try:
        # The PC's call keeps its shape (tests fake mint with recent only).
        data = realtime.mint(recent, scope=caller.kind) if caller.remote else realtime.mint(recent)
    except realtime.MintError as exc:
        remote.release_voice(caller)  # a failed mint never burns the quota
        raise HTTPException(exc.status, exc.detail) from None
    except Exception:
        remote.release_voice(caller)
        raise
    if caller.remote:
        remote.note_mint(caller)  # counted once OpenAI said yes
    # A session that picks up the last exchanges also keeps their taint (confirm.py).
    return {"client_secret": data["value"], "model": config.REALTIME_MODEL,
            "session_id": confirm.new_session(continues=bool(body and body.recent.strip()),
                                              sources=body.sources if body else None,
                                              origin=caller.origin)}

# ---------------------------------------------------------------- tools & live events

class ToolIn(BaseModel):
    name: str
    arguments: dict = {}
    session_id: str | None = None  # the voice session that called it (see /api/session)


@app.post("/api/tool")
def run_tool(request: Request, body: ToolIn):
    caller = remote.caller_of(request)
    if body.name in tools.client_tools():
        raise HTTPException(400, f"{body.name} s'exécute dans la page.")
    # A voice session is bound to the origin that opened it.
    error = confirm.check_session(body.session_id, caller.origin)
    if error:
        raise HTTPException(403, error)
    if caller.remote:
        audit.event(caller, "tool", tool=body.name)
    return tools.run_tool(body.name, body.arguments,
                          tools.ToolCtx(session_id=body.session_id, origin=caller.origin))


@app.get("/api/events")
async def stream_events(request: Request, client: str = "", last_event_id: str = ""):
    # client: the page's own id (leader election). Last-Event-ID: the browser
    # sends it back when it reconnects, and the stream replays what it missed.
    last = request.headers.get("last-event-id") or last_event_id
    stream = events.stream(client, last, remote.caller_of(request))
    return StreamingResponse(stream, media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

# ---------------------------------------------------------------- lifecycle

class JarvisServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        # uvicorn waits for open connections before it stops, and a page never
        # closes its event stream: end the streams first, or every Quit would
        # wait timeout_graceful_shutdown and log a cancelled request. The Serve
        # listener first (in a thread: it joins its own server).
        await asyncio.to_thread(listener.stop)
        events.close_streams()
        await super().shutdown(sockets=sockets)


def _request_shutdown():
    # The pages first, before their streams end: each one closes its voice
    # session (it goes straight to OpenAI and would run on, billed, until the
    # idle timeout), stops the wake word and closes its window.
    events.publish("shutdown", {})
    tasks.shutdown()
    if SERVER is not None:
        SERVER.should_exit = True


@app.post("/api/shutdown")
def shutdown():
    _request_shutdown()
    return {"ok": True}

# ---------------------------------------------------------------- launcher

def _already_running(url: str) -> bool:
    try:
        return httpx.get(f"{url}/healthz", timeout=1).json().get("app") == "jarvis"
    except Exception:  # noqa: BLE001
        return False


def _open_when_ready(url: str):
    for _ in range(60):
        if _already_running(url):
            break
        time.sleep(0.25)
    desktop.show_app_window(url)  # a window left from the previous run is reused


def _log_to_file_if_windowless():
    """pythonw (no console) has no stdout: write to data/jarvis.log instead."""
    if sys.stdout is None or sys.stderr is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = open(config.DATA_DIR / "jarvis.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def main():
    parser = argparse.ArgumentParser(description="JARVIS Local")
    parser.add_argument("--app", action="store_true",
                        help="ouvrir JARVIS dans sa propre fenêtre")
    parser.add_argument("--autostart", choices=["on", "off"],
                        help="lancer JARVIS au démarrage de Windows")
    args = parser.parse_args()
    _log_to_file_if_windowless()

    if args.autostart:
        try:
            print(desktop.set_autostart(args.autostart == "on"))
        except RuntimeError as exc:
            sys.exit(str(exc))
        return

    url = f"http://127.0.0.1:{config.PORT}"
    if _already_running(url):
        # Second launch (double-click, autostart): just bring the window back.
        if args.app:
            desktop.show_app_window(url)
        else:
            print(f"JARVIS tourne déjà -> {url}")
        return
    if args.app:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    global SERVER
    # A Server object (not uvicorn.run) so Quit and /api/shutdown can stop it cleanly.
    # Loopback only, and uvicorn never trusts a forwarded header: request.client
    # stays the real peer, and remote.py alone reads what Tailscale Serve adds.
    SERVER = JarvisServer(uvicorn.Config(app, host="127.0.0.1", port=config.PORT,
                                         proxy_headers=False, forwarded_allow_ips="",
                                         log_level="warning", timeout_graceful_shutdown=3))
    print(f"\n  JARVIS Local -> {url}\n")
    shell.start(url, _request_shutdown)
    try:
        SERVER.run()
    except KeyboardInterrupt:  # Ctrl+C in the console: uvicorn re-raises it once stopped
        pass
    finally:
        shell.stop()


if __name__ == "__main__":
    main()
