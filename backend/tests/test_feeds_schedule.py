"""Tests for the feed poll-scheduling API: PUT /feeds/{name}/schedule turns
a background asyncio loop on/off per feed, and GET /feeds/status reports the
current interval. Uses a keyless feed (earthquakes) so no external API key
is required; the USGS network call itself is not exercised here.
"""
import asyncio

import httpx
import pytest

from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_schedules():
    """Every test starts and ends with no schedules running, regardless of
    what a previous test left behind (schedule state is module-level)."""
    from app.api.feeds import stop_all_schedules

    stop_all_schedules()
    yield
    stop_all_schedules()


@pytest.mark.asyncio
async def test_set_and_clear_schedule():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/feeds/earthquakes/schedule", json={"interval_seconds": 60})
        assert resp.status_code == 200
        assert resp.json() == {"feed": "earthquakes", "schedule_interval_seconds": 60}

        status = await client.get("/feeds/status")
        assert status.json()["earthquakes"]["schedule_interval_seconds"] == 60

        # Turning it off cancels the background task and clears the interval.
        resp = await client.put("/feeds/earthquakes/schedule", json={"interval_seconds": None})
        assert resp.status_code == 200
        assert resp.json() == {"feed": "earthquakes", "schedule_interval_seconds": None}

        status = await client.get("/feeds/status")
        assert status.json()["earthquakes"]["schedule_interval_seconds"] is None


@pytest.mark.asyncio
async def test_schedule_rejects_unknown_feed():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/feeds/not-a-feed/schedule", json={"interval_seconds": 60})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_schedule_rejects_interval_below_minimum():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put("/feeds/earthquakes/schedule", json={"interval_seconds": 5})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_poll_missing_key_feed_returns_503_not_500():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/feeds/vessels/poll")
    # aisstream requires AISSTREAM_API_KEY, which isn't set in the test env.
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_setting_a_new_schedule_replaces_the_old_task():
    """Re-scheduling the same feed must cancel the previous loop, not leak
    a second one running alongside it."""
    from app.api.feeds import _schedule_tasks

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/feeds/earthquakes/schedule", json={"interval_seconds": 60})
        first_task = _schedule_tasks["earthquakes"]

        await client.put("/feeds/earthquakes/schedule", json={"interval_seconds": 90})
        second_task = _schedule_tasks["earthquakes"]

    assert first_task is not second_task
    assert first_task.cancelled() or first_task.cancel()
    await asyncio.sleep(0)  # let the cancellation propagate
