"""Access control delegated to the voice platform's user accounts.

Users sign in with their platform email/password (we relay the login), and
every protected request carries the platform's bearer token. We validate a
token by asking the platform (`GET /api/v1/auth/me`) - no shared secret to
leak or rotate - and cache positive results briefly so a page that fires
many requests doesn't hammer the platform. Anything but a clear 200/401
from the platform fails closed (503).
"""
import hashlib
import time

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)

CACHE_TTL_SECONDS = 60
CACHE_MAX = 1000
_valid_until: dict[str, float] = {}  # sha256(token) -> monotonic expiry

# Brute-force throttle: max failed logins per client within the window. The
# platform rate-limits by IP too, but all our traffic shares one IP, so
# without this one attacker could lock every user out.
MAX_FAILURES = 5
WINDOW_SECONDS = 60
_failures: dict[str, list[float]] = {}


def _unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=503, detail=detail)


def _platform(settings: Settings, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    if not settings.voice_api_url:
        raise _unavailable("Authentication is enabled but VOICE_API_URL is not set")
    return httpx.AsyncClient(
        base_url=settings.voice_api_url.rstrip("/"), timeout=10.0, transport=transport
    )


async def platform_login(
    settings: Settings,
    email: str,
    password: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Exchange platform credentials for its bearer token."""
    async with _platform(settings, transport) as client:
        try:
            res = await client.post(
                "/api/v1/auth/login", data={"username": email, "password": password}
            )
        except httpx.RequestError:
            raise _unavailable("Cannot reach the sign-in service")
    if res.status_code == 401:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if res.status_code == 429:
        raise HTTPException(status_code=429, detail="Too many attempts; try again in a minute")
    if not res.is_success:
        raise _unavailable("Sign-in service error")
    return res.json()["access_token"]


async def verify_platform_token(
    settings: Settings,
    token: str,
    transport: httpx.AsyncBaseTransport | None = None,
    now: float | None = None,
) -> bool:
    t = time.monotonic() if now is None else now
    key = hashlib.sha256(token.encode()).hexdigest()
    if _valid_until.get(key, 0) > t:
        return True
    async with _platform(settings, transport) as client:
        try:
            res = await client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.RequestError:
            raise _unavailable("Cannot reach the sign-in service")
    if res.status_code == 200:
        if len(_valid_until) >= CACHE_MAX:
            for k in [k for k, v in _valid_until.items() if v <= t]:
                del _valid_until[k]
            if len(_valid_until) >= CACHE_MAX:
                _valid_until.clear()
        _valid_until[key] = t + CACHE_TTL_SECONDS
        return True
    if res.status_code in (401, 403):
        _valid_until.pop(key, None)
        return False
    raise _unavailable("Sign-in service error")


def check_throttle(client: str, now: float | None = None) -> None:
    """Raise 429 when this client has too many recent failed logins."""
    t = time.time() if now is None else now
    recent = [f for f in _failures.get(client, []) if t - f < WINDOW_SECONDS]
    _failures[client] = recent
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many attempts; try again in a minute")


def record_failure(client: str, now: float | None = None) -> None:
    _failures.setdefault(client, []).append(time.time() if now is None else now)


def clear_failures(client: str) -> None:
    _failures.pop(client, None)


async def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings = get_settings()
    if not settings.auth_enabled:
        return
    if creds is None or not await verify_platform_token(settings, creds.credentials):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """The caller's platform token, for routes that act as that user."""
    if creds is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in with a voice platform account to use the voice agent",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return creds.credentials
