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
  The 'leader' event also says whether that page is in a conversation: the
  others then explain why they can't take over yet.
- Deliverable events (reminders, task results, briefings, warnings) go to the
  inbox before any page sees them, so nothing is lost when no page is open.
- Remote pages (a paired iPhone, see remote.py) get their own stream ids, never
  take part in the election and never hear the PC's controls ('leader',
  'hotkey', publish_pc); at most MAX_REMOTE_STREAMS stay open per device.
  A reconnecting phone replays nothing from before its pairing
  (remote.replay_floor) nor older than REMOTE_REPLAY_SECONDS.
- Every PING_SECONDS each stream gets a 'ping' data frame (no id): it keeps
  proxies from closing the connection, and a phone's page token alive
  (remote.keepalive).
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
PING = 'data: {"type":"ping"}\n\n'  # no id: never replayed, never Last-Event-ID
MAX_REMOTE_STREAMS = 4  # per device: a fifth closes the oldest
REMOTE_REPLAY_SECONDS = 600  # a phone back after a night never hears yesterday's events again
# publish() writes {"type": kind, ...} first: these PC controls never reach a remote stream.
_PC_ONLY = ('{"type": "leader"', '{"type": "hotkey"')

_lock = threading.Lock()
_elect_lock = threading.Lock()  # one election at a time, so 'leader' events go out in order
_subscribers: list = []  # _Sub per open stream
_replay: deque = deque(maxlen=REPLAY_SIZE)  # (id, message)
# When each _replay entry was published (time.monotonic()), appended with it:
# the newest ends of both deques always match.
_replay_at: deque = deque(maxlen=REPLAY_SIZE)
# Ids keep growing across restarts: a page still holding an id from the
# previous run then gets everything this run has buffered.
_seq = int(time.time() * 1000)
_closing = threading.Event()
_clients: dict = {}  # client id -> _Presence
HOOKS: list = []  # fn(kind, data) after each publish (journal.py notes tasks and reminders)
_leader = None
_leader_live = False
_CLIENT_RE = re.compile(r"^[\w-]{1,64}$")


class _Sub:
    __slots__ = ("loop", "queue", "client", "caller")

    def __init__(self, loop, client, caller=None):
        self.loop, self.client, self.caller = loop, client, caller  # caller None: a PC page
        self.queue = asyncio.Queue(maxsize=QUEUE_SIZE)


def _is_remote(caller) -> bool:
    return caller is not None and bool(caller.remote)


def _device(caller) -> str:
    return caller.device_id or caller.kind


def _delivers(sub, message: str) -> bool:
    """A remote stream never gets the PC's controls."""
    return not (_is_remote(sub.caller) and message.startswith(_PC_ONLY))


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
    return _push(kind, data, pc_only=False)


def publish_pc(kind: str, data: dict) -> int:
    """Push an event to the PC's pages only (pairing, devices, remote alerts):
    never to a remote stream, never replayed. The inbox and HOOKS still see it."""
    return _push(kind, data, pc_only=True)


def _push(kind: str, data: dict, pc_only: bool) -> int:
    global _seq
    data = _record(kind, data)
    message = json.dumps({"type": kind, **data}, ensure_ascii=False)
    with _lock:
        _seq += 1
        event_id = _seq
        if not pc_only:
            _replay.append((event_id, message))
            _replay_at.append(time.monotonic())
        # Scheduled under the lock: a stream that subscribes now either has
        # this event in its replay or in its queue, never both, never neither.
        for sub in _subscribers:
            if pc_only and _is_remote(sub.caller):
                continue
            if _delivers(sub, message):
                _send(sub, (event_id, message))
    for hook in list(HOOKS):
        try:
            hook(kind, data)
        except Exception:  # noqa: BLE001 - a listener must never break the push
            logging.exception("JARVIS: écouteur d'événements en échec (%s)", kind)
    return event_id


def _record(kind: str, data: dict) -> dict:
    """Keep what monsieur must hear in the inbox, and tell the page its inbox id."""
    from . import inbox  # late: inbox uses events too
    try:
        return inbox.on_publish(kind, data)
    except Exception:  # noqa: BLE001 - the live push must go out regardless
        logging.exception("JARVIS: boîte de réception indisponible pour %s", kind)
        return data


def close_streams(match=None) -> int:
    """End the streams whose caller matches (match(caller) -> bool; a PC page's
    caller may be None); returns how many. With no match JARVIS is stopping: end
    every page's stream, and any opened from now on. A page never hangs up on
    its own, and the server waits for open connections before it can exit."""
    if match is None:  # only a shutdown: a revocation must not end the PC's streams for good
        _closing.set()
    with _lock:
        subscribers = [sub for sub in _subscribers if match is None or match(sub.caller)]
    for sub in subscribers:
        _send(sub, None)
    return len(subscribers)


def has_subscribers(match=None) -> bool:
    """Is any JARVIS page of the PC listening? (otherwise a message needs another
    way out: an open phone stream never silences the PC's toasts). With match:
    any stream whose caller matches."""
    with _lock:
        if match is None:
            return any(not _is_remote(sub.caller) for sub in _subscribers)
        return any(match(sub.caller) for sub in _subscribers)


def current_id() -> int:
    """The last event id given out (a paired device replays nothing before it)."""
    with _lock:
        return _seq


def _parse_id(value, current: int):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    # From the future (a clock set back between runs): replaying nothing would
    # also drop every new event, so treat it as unknown.
    return n if 0 <= n <= current else None


def _replay_floor(caller):
    """The id a reconnecting phone replays from at the earliest (its pairing);
    None when it can't be known: nothing is replayed then (fail closed)."""
    from . import remote  # late: remote imports events
    try:
        return int(remote.replay_floor(caller))
    except Exception:  # noqa: BLE001 - a broken device file must not open the backlog
        logging.exception("JARVIS: reprise du flux distant impossible")
        return None


def _backlog(sub, after) -> list:
    """The buffered events after that id this stream may hear. A remote stream
    skips those older than REMOTE_REPLAY_SECONDS."""
    if after is None:
        return []
    if not _is_remote(sub.caller):
        return [item for item in _replay if item[0] > after and _delivers(sub, item[1])]
    oldest = time.monotonic() - REMOTE_REPLAY_SECONDS
    # Paired from the newest end; an entry without its time is never replayed.
    stamped = list(zip(reversed(_replay), reversed(_replay_at)))[::-1]
    return [item for item, at in stamped if at >= oldest and item[0] > after and _delivers(sub, item[1])]


def _keepalive(caller):
    from . import remote  # late: remote imports events
    try:
        remote.keepalive(caller)
    except Exception:  # noqa: BLE001 - the stream goes on; the next ping tries again
        logging.exception("JARVIS: flux distant : maintien en échec")


async def stream(client_id: str = "", last_event_id=None, caller=None):
    """One page's event stream. client_id is the page's own id (sse.js); with
    last_event_id, the buffered events after it are replayed first. caller: the
    remote.Caller of the request (None or PC: a page of this PC)."""
    remote_page = _is_remote(caller)
    if remote_page:
        # Its own namespace: a phone can never take a PC page's id, nor its presence.
        client = f"r-{_device(caller)}-{uuid.uuid4().hex[:8]}"
    else:
        client = client_id if client_id and _CLIENT_RE.match(client_id) else f"anon-{uuid.uuid4().hex[:12]}"
    sub = _Sub(asyncio.get_running_loop(), client, caller)
    oldest = []
    # A phone that reconnects never replays what came before its pairing.
    # Asked outside the lock: remote may read events.current_id().
    floor = _replay_floor(caller) if remote_page and last_event_id not in (None, "") else None
    with _lock:
        after = _parse_id(last_event_id, _seq)
        if after is not None and remote_page:
            # Unknown floor: nothing. From the future (a clock set back): nothing
            # either, without dropping the events still to come.
            after = _seq if floor is None else max(after, min(floor, _seq))
        backlog = _backlog(sub, after)
        if remote_page:  # this one makes MAX_REMOTE_STREAMS: the oldest of that device go
            same = [s for s in _subscribers if _is_remote(s.caller) and _device(s.caller) == _device(caller)]
            oldest = same[:max(0, len(same) - (MAX_REMOTE_STREAMS - 1))]
        _subscribers.append(sub)
        if not remote_page:  # a remote page never joins the election
            _join(client)
    for old in oldest:
        _send(old, None)
    if not remote_page:
        _elect()
    try:
        if _closing.is_set():
            return
        yield "retry: 3000\n\n"
        last = after or 0
        for event_id, message in backlog:
            last = event_id
            yield f"id: {event_id}\ndata: {message}\n\n"
        if not remote_page:
            # Who speaks, even if it didn't change (no id: not part of the replay).
            current = json.dumps({"type": "leader", **leader_info()})
            yield f"data: {current}\n\n"
        next_ping = time.monotonic() + PING_SECONDS
        while not _closing.is_set():
            wait = next_ping - time.monotonic()
            if wait <= 0:  # due even on a busy stream: a phone's token must not lapse
                next_ping = time.monotonic() + PING_SECONDS
                if remote_page:
                    _keepalive(caller)
                yield PING
                continue
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=wait)
            except asyncio.TimeoutError:
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
            if not remote_page:
                _leave(client)
        if not remote_page:
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


def leader_info() -> dict:
    """The leader and whether it holds a voice session (a claim can't take
    over a conversation: 'Utiliser celle-ci' waits until it ends)."""
    with _lock:
        return {"client": _leader, "live": _leader_live}


def _elect():
    global _leader, _leader_live
    with _elect_lock:
        with _lock:
            ranked = [(p.rank(), cid) for cid, p in _clients.items() if p.streams > 0]
            new = max(ranked)[1] if ranked else None
            live = bool(new is not None and _clients[new].live)
            changed = (new, live) != (_leader, _leader_live)
            _leader, _leader_live = new, live
        if changed and new is not None:
            publish("leader", {"client": new, "live": live})
