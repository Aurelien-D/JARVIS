"""Remote access: a paired iPhone reaching JARVIS through Tailscale Serve.

Who is calling is decided here, never by the Host header:
- a request that arrived on REMOTE_PORT (the loopback port only Tailscale Serve
  uses), or that carries any proxy header, is remote;
- a remote request never receives nor authenticates with the PC's page token:
  it goes through remote_guard, which stamps request.state.caller.

This file is the shared contract. Caller, PC, caller_of, kind_of, PROXY_MARKERS,
is_remote_request and READY are final. The rest are fail-closed stubs until the
remote core lands: every remote request is refused and remote access cannot be
switched on.
"""
from dataclasses import dataclass

from fastapi.responses import HTMLResponse, JSONResponse

from . import config, page

# Flipped to True by the integrator in the C2 merge, once every P0 proof is green.
READY = False

PROXY_MARKERS = ("forwarded", "via", "x-real-ip")
REMOTE_SETTINGS: frozenset = frozenset()  # the Réglages keys the phone may change
REMOTE_SECTIONS: tuple = ()               # the Réglages sections the phone sees

_NOT_YET = "Accès à distance pas encore disponible dans cette version."


class RemoteError(Exception):
    """Remote access refused an operation; the message is French, shown as is."""


@dataclass(frozen=True)
class Caller:
    kind: str            # "pc" | "app" | "siri" | "unpaired"
    device_id: str = ""  # "d_<16 hex>" for app and siri (the paired device a Siri key belongs to)
    key_id: str = ""     # "k_<16 hex>" for siri
    ip: str = ""         # tailnet IP from X-Forwarded-For ("" for pc)
    login: str = ""      # Tailscale-User-Login, decoded and lowercased ("" for pc)
    name: str = ""       # device name, for audit and UI ("" for pc/unpaired)

    @property
    def remote(self) -> bool:
        return self.kind != "pc"

    @property
    def origin(self) -> str:
        """The value stored on sessions, tasks, pendings, schedules and inbox items."""
        if self.kind == "app":
            return f"app:{self.device_id}"
        if self.kind == "siri":
            return f"siri:{self.key_id}"
        return self.kind  # "pc" or "unpaired"


PC = Caller(kind="pc")
_UNPAIRED = Caller(kind="unpaired")


def caller_of(request) -> Caller:
    """request.state.caller; a request the guard did not stamp counts as unpaired (fail closed)."""
    caller = getattr(request.state, "caller", None)
    return caller if isinstance(caller, Caller) else _UNPAIRED


def kind_of(origin: str) -> str:
    """'pc' for '' or 'pc'; else the part before ':' ('app', 'siri'); anything else
    'unknown' (treated as remote)."""
    origin = str(origin or "")
    if origin in ("", "pc"):
        return "pc"
    head, sep, _ = origin.partition(":")
    return head if sep and head in ("app", "siri") else "unknown"


def is_remote_request(request) -> bool:
    """Arrived on the Serve port, or carries any proxy header: remote. A proxy
    header on the PC port still counts (fail closed); the gate then refuses it."""
    server = request.scope.get("server") or ()
    if len(server) > 1 and server[1] == config.REMOTE_PORT:
        return True
    return any(h in PROXY_MARKERS or h.startswith(("x-forwarded-", "tailscale-"))
               for h in (k.lower() for k in request.headers))


async def remote_guard(request, call_next):
    """Stub: every remote request is refused until the remote core lands."""
    return JSONResponse({"detail": "Accès à distance indisponible dans cette version."}, status_code=403)

# ---------------------------------------------------------------- state (stubs)

def is_enabled() -> bool:
    return False


def paused_until() -> float:
    return 0.0


def set_enabled(on: bool, *, host: str | None = None, login: str | None = None, by: Caller = PC) -> dict:
    raise RemoteError(_NOT_YET)


def pause(hours: int, *, by: Caller) -> float:
    raise RemoteError(_NOT_YET)


def host() -> str:
    return ""


def logins() -> list[str]:
    return []


def complet_allowed(now: float | None = None) -> bool:
    return False


def complet_until() -> float:
    return 0.0


def set_complet(duration: str) -> float:
    raise RemoteError(_NOT_YET)


def check_voice(caller) -> tuple[int, str] | None:
    """(status, French reason) when a remote voice session must be refused. Never counts."""
    return None


def note_mint(caller) -> None:
    """One successful remote mint (counted after OpenAI answered)."""


def mints_today(device_id: str) -> int:
    return 0


def render_remote_page(request, caller):
    return HTMLResponse(page.pairing_html("refused"), status_code=403, headers={"Cache-Control": "no-store"})


def replay_floor(caller) -> int:
    return 0


def keepalive(caller) -> None:
    """The device's event stream is still connected."""


def state_for(caller) -> dict:
    return {}


def open_pairing(minutes: int = 10) -> float:
    raise RemoteError(_NOT_YET)


def close_pairing() -> None:
    """Nothing to close: pairing never opens before the remote core lands."""


def pairing_until() -> float:
    return 0.0


def note_published(on: bool) -> None:
    """Remembers whether JARVIS's Serve configuration is published."""


def origin_active(origin: str) -> bool:
    """Is the device or key behind this origin still allowed? Only the PC, for now."""
    return kind_of(origin) == "pc"


def reset_memory() -> None:
    """Tests: forget every in-memory window (nothing kept yet)."""
