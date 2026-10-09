"""Server -> page push channel (Server-Sent Events), and which page speaks.

Task progress, reminders and briefings are produced on worker threads; each
open page gets its own asyncio queue and publish() hands events over
thread-safely.

- Replay: every event carries an increasing id and the last ones stay in a
  short buffer. A page whose stream dropped (sleep, Wi-Fi, a server hiccup)
  reconnects with Last-Event-ID, which the browser sends back on its own, and
  gets what it missed, in order.
- Bounded queues: a page that stops reading (a frozen tab) loses its oldest
  events instead of growing the server's memory.
- Leader: several pages can be open (the app window, a forgotten tab, a page
  in another browser profile). Only one of them listens for the wake word and
  announces things. Browser-side locks don't span profiles, so the server
  picks it: the live page, else the most recently focused one, else the newest.
- Deliverable events (reminders, task results, briefings, warnings) go to the
  inbox before any page sees them, so nothing is lost when no page is open.
"""
import asyncio
import json
import logging
import re
import threading
import time
import uuid
from collections import deque

REPLAY_SIZE = 200
QUEUE_SIZE = 500
PING_SECONDS = 15

_lock = threading.Lock()
_elect_lock = threading.Lock()  # one election at a time, so 'leader' events go out in order
_subscribers: list = []  # _Sub per open stream
_replay: deque = deque(maxlen=REPLAY_SIZE)  # (id, message)
# Ids keep growing across restarts: a page still holding an id from the
# previous run then gets everything this run has buffered.
_seq = int(time.time() * 1000)
_closing = threading.Event()
_clients: dict = {}  # client id -> _Presence
_leader = None
_CLIENT_RE = re.compile(r"^[\w-]{1,64}$")


class _Sub:
    __slots__ = ("loop", "queue", "client")

    def __init__(self, loop, client):
        self.loop, self.client = loop, client
        self.queue = asyncio.Queue(maxsize=QUEUE_SIZE)


class _Presence:
    """What a page last told us about itself (POST /api/presence)."""
    __slots__ = ("streams", "connected", "focused", "attention", "live", "seen")

    def __init__(self, now):
        self.streams = 0          # open event streams (a reload briefly has two)
        self.connected = now
        self.focused = False
        self.attention = 0.0      # last focus gained or lost, claim, or start of a session
        self.live = False
        self.seen = now

    def rank(self):
        return (self.live, self.focused, self.attention, self.connected)


def _put(queue, item):
    """On the page's own loop: a full queue makes room by dropping its oldest event."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(item)


def _send(sub, item):
    try:
        sub.loop.call_soon_threadsafe(_put, sub.queue, item)
    except RuntimeError:  # that page's loop is gone
        pass


def publish(kind: str, data: dict) -> int:
    """Push an event to every open page; returns its id."""
    global _seq
    data = _record(kind, data)
    message = json.dumps({"type": kind, **data}, ensure_ascii=False)
    with _lock:
        _seq += 1
        event_id = _seq
        _replay.append((event_id, message))
        # Scheduled under the lock: a stream that subscribes now either has
        # this event in its replay or in its queue, never both, never neither.
        for sub in _subscribers:
            _send(sub, (event_id, message))
    return event_id


def _record(kind: str, data: dict) -> dict:
    """Keep what monsieur must hear in the inbox, and tell the page its inbox id."""
    from . import inbox  # late: inbox uses events too
    try:
        return inbox.on_publish(kind, data)
    except Exception:  # noqa: BLE001 - the live push must go out regardless
        logging.exception("JARVIS: boîte de réception indisponible pour %s", kind)
        return data


def close_streams():
    """JARVIS is stopping: end every page's stream. A page never hangs up on its
    own, and the server waits for open connections before it can exit."""
    _closing.set()
    with _lock:
        subscribers = list(_subscribers)
    for sub in subscribers:
        _send(sub, None)


def has_subscribers() -> bool:
    """Is any JARVIS page listening? (otherwise a message needs another way out)"""
    with _lock:
        return bool(_subscribers)


def _parse_id(value, current: int):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    # From the future (a clock set back between runs): replaying nothing would
    # also drop every new event, so treat it as unknown.
    return n if 0 <= n <= current else None


async def stream(client_id: str = "", last_event_id=None):
    """One page's event stream. client_id is the page's own id (sse.js); with
    last_event_id, the buffered events after it are replayed first."""
    client = client_id if client_id and _CLIENT_RE.match(client_id) else f"anon-{uuid.uuid4().hex[:12]}"
    sub = _Sub(asyncio.get_running_loop(), client)
    with _lock:
        after = _parse_id(last_event_id, _seq)
        backlog = [item for item in _replay if after is not None and item[0] > after]
        _subscribers.append(sub)
        _join(client)
    _elect()
    try:
        if _closing.is_set():
            return
        yield "retry: 3000\n\n"
        last = after or 0
        for event_id, message in backlog:
            last = event_id
            yield f"id: {event_id}\ndata: {message}\n\n"
        # Who speaks, even if it didn't change (no id: not part of the replay).
        current = json.dumps({"type": "leader", "client": leader()})
        yield f"data: {current}\n\n"
        while not _closing.is_set():
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=PING_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # keeps the connection (and proxies) alive
                continue
            if item is None:  # close_streams()
                return
            event_id, message = item
            if event_id <= last:  # already sent from the replay
                continue
            last = event_id
            yield f"id: {event_id}\ndata: {message}\n\n"
    finally:
        with _lock:
            _subscribers.remove(sub)
            _leave(client)
        _elect()

# ---------------------------------------------------------------- the leader page

def _join(client: str):
    p = _clients.get(client)
    if p is None:
        p = _clients[client] = _Presence(time.time())
    p.streams += 1


def _leave(client: str):
    p = _clients.get(client)
    if p is not None:
        p.streams -= 1
        if p.streams <= 0:
            del _clients[client]


def presence(client: str, focused=None, live=None, claim: bool = False):
    """A page reports its focus and whether it holds a voice session; claim is
    monsieur choosing that page ('Utiliser celle-ci'). Returns the leader."""
    if not client or not _CLIENT_RE.match(client):
        raise ValueError("identifiant de page invalide")
    now = time.time()
    with _lock:
        _prune_presence(now)
        p = _clients.get(client)
        if p is None:  # before its stream registered: kept until it does
            p = _clients[client] = _Presence(now)
        p.seen = now
        if claim:
            focused = True
            p.attention = now
        if focused is not None:
            if bool(focused) != p.focused:
                p.attention = now  # it gained or lost the focus just now
            p.focused = bool(focused)
        if live is not None:
            if live and not p.live:
                p.attention = now
            p.live = bool(live)
    _elect()
    return leader()


def _prune_presence(now: float):
    for cid in [cid for cid, p in _clients.items() if p.streams <= 0 and now - p.seen > 60]:
        del _clients[cid]


def leader():
    """The page that speaks and listens for the wake word (None: no page open)."""
    with _lock:
        return _leader


def _elect():
    global _leader
    with _elect_lock:
        with _lock:
            ranked = [(p.rank(), cid) for cid, p in _clients.items() if p.streams > 0]
            new = max(ranked)[1] if ranked else None
            changed = new != _leader
            _leader = new
        if changed and new is not None:
            publish("leader", {"client": new})
