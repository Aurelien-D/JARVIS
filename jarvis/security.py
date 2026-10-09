"""Local-only guard.

JARVIS runs Claude Code with no permission prompts, so anything that can call
its API can run commands on this PC. Three layers keep that to this page:

- Host check: only 127.0.0.1 / localhost are served. This blocks DNS
  rebinding, where a web page re-points its own domain at 127.0.0.1 to reach
  the API as if it were same-origin.
- Origin check: a browser request sent by another site is refused.
- Session token: minted at startup and embedded in the page; every /api call
  must carry it, so a page that cannot read ours cannot call us.
"""
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

TOKEN = secrets.token_urlsafe(32)
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _hostname(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):  # IPv6 literal: [::1]:8788
        return host[: host.find("]") + 1]
    return host.split(":", 1)[0]


def _token_ok(request: Request) -> bool:
    given = request.headers.get("x-jarvis-token") or request.query_params.get("token") or ""
    return secrets.compare_digest(given.encode(), TOKEN.encode())


async def guard(request: Request, call_next):
    host = request.headers.get("host", "")
    if _hostname(host) not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Hôte refusé : JARVIS ne répond qu'en local."}, status_code=403)
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc.lower() != host.lower():
        return JSONResponse({"detail": "Origine refusée."}, status_code=403)
    if request.url.path.startswith("/api/") and not _token_ok(request):
        return JSONResponse({"detail": "Jeton de session invalide : recharge la page."}, status_code=401)
    return await call_next(request)
