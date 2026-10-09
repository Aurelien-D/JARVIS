#!/usr/bin/env python3
"""JARVIS Local: realtime voice assistant that drives Claude Code sessions.

One small FastAPI server, reachable from this PC only (see jarvis/security.py):
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

from jarvis import (api_memory, api_schedules, api_tasks, ares, config, confirm, desktop, events,
                    health, inbox, journal, realtime, scheduler, security, settings, shell, tasks,
                    tools, usage)

settings.apply_overrides()  # settings saved from the UI win over .env

SERVER = None  # the running uvicorn.Server: /api/shutdown and the tray's Quit stop it


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks.load_history()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="JARVIS Local", lifespan=lifespan)
app.middleware("http")(security.guard)
# The Windows registry can map .js to text/plain, and browsers refuse to run a
# module script served that way: pin the types StaticFiles will guess.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


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
               confirm.router):
    app.include_router(router)

# ---------------------------------------------------------------- page

@app.get("/")
def index():
    html = (config.ROOT / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__JARVIS_TOKEN__", security.TOKEN),
                        headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz():
    return {"app": "jarvis"}


@app.get("/api/config")
def get_config():
    return {"wake_word": config.WAKE_WORD, "speech_lang": config.SPEECH_LANG,
            "idle_minutes": config.IDLE_MINUTES}

# ---------------------------------------------------------------- realtime session

class SessionIn(BaseModel):
    recent: str = ""  # last exchanges, so a reconnection picks up the thread


@app.post("/api/session")
def create_session(body: SessionIn | None = None):
    try:
        data = realtime.mint(body.recent if body else "")
    except realtime.MintError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    return {"client_secret": data["value"], "model": config.REALTIME_MODEL,
            "session_id": confirm.new_session()}

# ---------------------------------------------------------------- tools & live events

class ToolIn(BaseModel):
    name: str
    arguments: dict = {}
    session_id: str | None = None  # the voice session that called it (see /api/session)


@app.post("/api/tool")
def run_tool(body: ToolIn):
    if body.name in tools.client_tools():
        raise HTTPException(400, f"{body.name} s'exécute dans la page.")
    return tools.run_tool(body.name, body.arguments, tools.ToolCtx(session_id=body.session_id))


@app.get("/api/events")
async def stream_events(request: Request, client: str = "", last_event_id: str = ""):
    # client: the page's own id (leader election). Last-Event-ID: the browser
    # sends it back when it reconnects, and the stream replays what it missed.
    last = request.headers.get("last-event-id") or last_event_id
    return StreamingResponse(events.stream(client, last), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

# ---------------------------------------------------------------- lifecycle

class JarvisServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        # uvicorn waits for open connections before it stops, and a page never
        # closes its event stream: end the streams first, or every Quit would
        # wait timeout_graceful_shutdown and log a cancelled request.
        events.close_streams()
        await super().shutdown(sockets=sockets)


def _request_shutdown():
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
    desktop.open_app_window(url)


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
    SERVER = JarvisServer(uvicorn.Config(app, host="127.0.0.1", port=config.PORT,
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
