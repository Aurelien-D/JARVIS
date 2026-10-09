"""Server -> page push channel (Server-Sent Events).

Task progress, reminders and briefings are produced on worker threads; each
open page gets its own asyncio queue and publish() hands events over
thread-safely.
"""
import asyncio
import json
import threading

_subscribers: list = []  # (loop, queue) per open page
_lock = threading.Lock()


def publish(kind: str, data: dict):
    message = json.dumps({"type": kind, **data}, ensure_ascii=False)
    with _lock:
        subscribers = list(_subscribers)
    for loop, queue in subscribers:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, message)
        except RuntimeError:  # that page's loop is gone
            pass


async def stream():
    sub = (asyncio.get_running_loop(), asyncio.Queue())
    with _lock:
        _subscribers.append(sub)
    try:
        yield "retry: 3000\n\n"
        while True:
            try:
                message = await asyncio.wait_for(sub[1].get(), timeout=15)
                yield f"data: {message}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # keeps the connection (and proxies) alive
    finally:
        with _lock:
            _subscribers.remove(sub)
