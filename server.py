#!/usr/bin/env python3
"""JARVIS Local: realtime voice assistant that drives Claude Code sessions.

One small FastAPI server, reachable from this PC only (see jarvis/security.py):
  GET  /                   the futuristic UI (orb, task panels, reminders, memory)
  POST /api/session        mints an ephemeral OpenAI Realtime token (your API key
                           never reaches the browser)
  POST /api/tool           runs a voice tool (Claude task, app, volume, reminder...)
  GET  /api/events         live push: task progress, reminders, briefings
  GET  /api/tasks ...      task list and cancel, reminders, memory

The browser talks to OpenAI Realtime over WebRTC for voice, and the model
calls tools; delegate_to_claude hands real work to Claude Code. Results are
read back aloud when the Claude session finishes.

  python server.py                    start, then open http://127.0.0.1:8788
  python server.py --app              start and open JARVIS in its own window
  python server.py --autostart on     launch JARVIS when Windows starts (off to undo)
"""
import argparse
import sys
import threading
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jarvis import config, desktop, events, memory, scheduler, security, tasks, tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks.load_history()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="JARVIS Local", lifespan=lifespan)
app.middleware("http")(security.guard)
app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")

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
    if not config.OPENAI_API_KEY:
        raise HTTPException(500, "OPENAI_API_KEY manquant: copie .env.example vers .env et mets ta clé.")
    payload = {
        "session": {
            "type": "realtime",
            "model": config.REALTIME_MODEL,
            "instructions": tools.build_instructions(body.recent if body else ""),
            "tools": tools.TOOLS,
            "audio": {
                "input": {"transcription": {"model": "whisper-1"}},
                "output": {"voice": config.VOICE},
            },
        }
    }
    try:
        r = httpx.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"OpenAI injoignable : {exc}") from None
    if r.status_code >= 400:
        # 502, not OpenAI's own status: a 401 here is about the API key, not our page token.
        raise HTTPException(502, f"OpenAI {r.status_code}: {r.text[:300]}")
    data = r.json()
    return {"client_secret": data["value"], "model": config.REALTIME_MODEL}

# ---------------------------------------------------------------- tools & live events

class ToolIn(BaseModel):
    name: str
    arguments: dict = {}


@app.post("/api/tool")
def run_tool(body: ToolIn):
    if body.name in tools.CLIENT_TOOLS:
        raise HTTPException(400, f"{body.name} s'exécute dans la page.")
    return tools.run_tool(body.name, body.arguments)


@app.get("/api/events")
async def stream_events():
    return StreamingResponse(events.stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

# ---------------------------------------------------------------- side panels

@app.get("/api/tasks")
def list_tasks():
    return tasks.list_tasks()


@app.get("/api/task/{task_id}")
def get_task(task_id: str):
    task = tasks.TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    return tasks.public(task)


@app.post("/api/task/{task_id}/cancel")
def cancel_task(task_id: str):
    return tasks.cancel(task_id)


@app.get("/api/schedules")
def list_schedules():
    return scheduler.items()


@app.delete("/api/schedules/{item_id}")
def delete_schedule(item_id: str):
    return {"ok": True, "removed": len(scheduler.cancel(item_id))}


@app.get("/api/memory")
def list_memory():
    return memory.facts()


@app.delete("/api/memory/{fact_id}")
def delete_memory(fact_id: str):
    return {"ok": True, "removed": len(memory.forget(fact_id))}

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
            desktop.open_app_window(url)
        else:
            print(f"JARVIS tourne déjà -> {url}")
        return
    if args.app:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    import uvicorn
    print(f"\n  JARVIS Local -> {url}\n")
    uvicorn.run(app, host="127.0.0.1", port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
