"""Auth is delegated to the voice platform's users; the platform is faked
with httpx.MockTransport."""
import httpx
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app import auth
from app.api import auth as auth_api
from app.config import Settings
from app.main import create_app

PUBLIC = {"/health", "/auth/status", "/auth/login"}


def _settings(**over) -> Settings:
    return Settings(**{"auth_mode": "platform", "voice_api_url": "https://p.example.com", **over})


@pytest.fixture(autouse=True)
def _clean_state():
    auth._failures.clear()
    auth._valid_until.clear()


def test_auth_enabled_only_in_platform_mode():
    assert _settings().auth_enabled
    assert not Settings(auth_mode="").auth_enabled
    assert not Settings(auth_mode="none").auth_enabled


# ---- platform calls -----------------------------------------------------------


async def test_login_relays_credentials_and_returns_platform_token():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/auth/login"
        assert b"username=a%40b.com" in req.content and b"password=pw" in req.content
        return httpx.Response(200, json={"access_token": "T", "token_type": "bearer"})

    token = await auth.platform_login(_settings(), "a@b.com", "pw", httpx.MockTransport(handler))
    assert token == "T"


@pytest.mark.parametrize("status,expected", [(401, 401), (429, 429), (500, 503)])
async def test_login_maps_platform_failures(status, expected):
    t = httpx.MockTransport(lambda r: httpx.Response(status, json={}))
    with pytest.raises(HTTPException) as e:
        await auth.platform_login(_settings(), "a@b.com", "pw", t)
    assert e.value.status_code == expected


async def test_verify_accepts_valid_and_caches():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.headers["authorization"])
        return httpx.Response(200, json={"id": 1})

    t = httpx.MockTransport(handler)
    assert await auth.verify_platform_token(_settings(), "tok", t, now=100)
    assert await auth.verify_platform_token(_settings(), "tok", t, now=130)  # cached
    assert calls == ["Bearer tok"]
    assert await auth.verify_platform_token(_settings(), "tok", t, now=100 + auth.CACHE_TTL_SECONDS + 1)
    assert len(calls) == 2


async def test_verify_rejects_invalid_and_never_caches_rejection():
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    assert not await auth.verify_platform_token(_settings(), "bad", t, now=1)
    assert auth._valid_until == {}


async def test_verify_fails_closed_when_platform_is_down_or_unconfigured():
    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(HTTPException) as e:
        await auth.verify_platform_token(_settings(), "t", httpx.MockTransport(boom))
    assert e.value.status_code == 503
    with pytest.raises(HTTPException) as e:
        await auth.verify_platform_token(_settings(voice_api_url=""), "t")
    assert e.value.status_code == 503
    with pytest.raises(HTTPException) as e:
        await auth.verify_platform_token(
            _settings(), "t", httpx.MockTransport(lambda r: httpx.Response(500))
        )
    assert e.value.status_code == 503


def test_throttle_blocks_after_repeated_failures_then_recovers():
    for _ in range(auth.MAX_FAILURES):
        auth.check_throttle("1.2.3.4", now=100)
        auth.record_failure("1.2.3.4", now=100)
    with pytest.raises(HTTPException) as e:
        auth.check_throttle("1.2.3.4", now=101)
    assert e.value.status_code == 429
    auth.check_throttle("5.6.7.8", now=101)
    auth.check_throttle("1.2.3.4", now=100 + auth.WINDOW_SECONDS + 1)


# ---- HTTP layer ---------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    s = _settings()
    for mod in (auth, auth_api):
        monkeypatch.setattr(mod, "get_settings", lambda: s)

    async def fake_verify(settings, token, *a, **k):
        return token == "good"

    async def fake_login(settings, email, password, *a, **k):
        if (email, password) == ("a@b.com", "pw"):
            return "good"
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    monkeypatch.setattr(auth, "verify_platform_token", fake_verify)
    monkeypatch.setattr(auth_api, "platform_login", fake_login)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t")


async def test_every_non_public_route_requires_auth():
    unprotected = [
        r.path
        for r in create_app().routes
        if isinstance(r, APIRoute)
        and r.path not in PUBLIC
        and not any(d.call is auth.require_auth for d in r.dependant.dependencies)
    ]
    assert unprotected == []


async def test_protected_route_rejects_missing_and_bad_tokens(client):
    assert (await client.get("/voice/status")).status_code == 401
    r = await client.get("/voice/status", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


async def test_login_then_access(client):
    assert (await client.get("/auth/status")).json() == {"enabled": True}
    bad = await client.post("/auth/login", json={"email": "a@b.com", "password": "x"})
    assert bad.status_code == 401
    ok = await client.post("/auth/login", json={"email": "a@b.com", "password": "pw"})
    token = ok.json()["token"]
    res = await client.get("/voice/status", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


async def test_login_is_throttled_even_for_the_right_password(client):
    for _ in range(auth.MAX_FAILURES):
        r = await client.post("/auth/login", json={"email": "a@b.com", "password": "x"})
        assert r.status_code == 401
    r = await client.post("/auth/login", json={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 429


async def test_auth_disabled_when_mode_unset(monkeypatch):
    s = Settings(auth_mode="")
    for mod in (auth, auth_api):
        monkeypatch.setattr(mod, "get_settings", lambda: s)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t")
    assert (await c.get("/auth/status")).json() == {"enabled": False}
    assert (await c.get("/voice/status")).status_code == 200
    r = await c.post("/auth/login", json={"email": "a@b.com", "password": "x"})
    assert r.status_code == 400
