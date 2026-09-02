"""Manual trigger, status, and interval scheduling for live feed polling.

Scheduling is a lightweight in-process asyncio loop per feed (no new
dependency, no cron) — good enough for a single-worker backend. See the
`simplification:` note on `_schedule_tasks` for the scale-out ceiling.
"""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db.neo4j_client import get_driver
from app.feeds import aisstream, gdelt, opensky, sverigesradio, trafikverket, usgs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds", tags=["feeds"])

_POLLERS = {
    "situations": trafikverket.poll_situations,
    "cameras": trafikverket.poll_cameras,
    "earthquakes": usgs.poll_earthquakes,
    "aircraft": opensky.poll_aircraft,
    "vessels": aisstream.poll_vessels,
    "news": gdelt.poll_news,
    "radio_news": sverigesradio.poll_radio_news,
}

# Feed name -> Settings attribute that must be non-empty for that feed to
# run. Feeds not listed here need no secret (earthquakes/aircraft/news/radio_news).
_REQUIRED_KEY = {
    "situations": "trafikverket_api_key",
    "cameras": "trafikverket_api_key",
    "vessels": "aisstream_api_key",
}

MIN_INTERVAL_SECONDS = 30

# In-memory last-poll status and schedule state; simplification: lost on
# process restart, and not shared across multiple backend instances/workers
# (each would run its own independent schedule and double-poll). Upgrade
# path: persist schedule + last-poll status to Neo4j (a FeedStatus node) and
# elect a single poller if this ever runs behind more than one worker.
_status: dict[str, dict] = {}
_schedule_intervals: dict[str, int] = {}
_schedule_tasks: dict[str, asyncio.Task] = {}


class ScheduleRequest(BaseModel):
    interval_seconds: int | None = Field(
        None, description="Poll every N seconds; null/omitted turns scheduling off."
    )


def _feed_error(name: str) -> str | None:
    """Returns why `name` can't be polled right now, or None if it's ready."""
    if name not in _POLLERS:
        return f"unknown feed '{name}', expected one of {list(_POLLERS)}"
    if not get_settings().feeds_enabled:
        return "feeds are disabled (FEEDS_ENABLED=false)"
    required_key = _REQUIRED_KEY.get(name)
    if required_key and not getattr(get_settings(), required_key):
        return f"{required_key.upper()} is not configured"
    return None


async def _run_poll(name: str) -> dict:
    """Runs one poll cycle for `name` and records its outcome in `_status`,
    whether it succeeds or raises. Callers decide whether to re-raise."""
    poller = _POLLERS[name]
    try:
        driver = get_driver()
        async with driver.session() as session:
            counts = await poller(session)
        _status[name] = {**counts, "polled_at": datetime.now(timezone.utc).isoformat(), "error": None}
    except Exception as e:  # noqa: BLE001 - a single feed's failure must not crash the loop/process
        _status[name] = {"error": str(e), "polled_at": datetime.now(timezone.utc).isoformat()}
        raise
    return _status[name]


async def _schedule_loop(name: str, interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await _run_poll(name)
        except Exception:  # noqa: BLE001 - logged; loop keeps running on the next tick
            logger.warning("scheduled poll failed for feed %s", name, exc_info=True)


def stop_all_schedules() -> None:
    """Cancels every running schedule task — called from app shutdown so no
    background pollers outlive the process."""
    for task in _schedule_tasks.values():
        task.cancel()
    _schedule_tasks.clear()
    _schedule_intervals.clear()


@router.get("/status")
async def feeds_status():
    return {
        name: {**(_status.get(name) or {}), "schedule_interval_seconds": _schedule_intervals.get(name)}
        for name in _POLLERS
    }


@router.post("/{name}/poll")
async def poll_feed(name: str):
    error = _feed_error(name)
    if error:
        status_code = 404 if name not in _POLLERS else 503
        raise HTTPException(status_code=status_code, detail=error)

    try:
        counts = await _run_poll(name)
    except Exception as e:  # noqa: BLE001 - surfaced to the caller as a 502
        raise HTTPException(status_code=502, detail=str(e))
    return {"feed": name, **counts}


@router.put("/{name}/schedule")
async def set_feed_schedule(name: str, body: ScheduleRequest):
    if name not in _POLLERS:
        raise HTTPException(status_code=404, detail=f"unknown feed '{name}', expected one of {list(_POLLERS)}")

    existing = _schedule_tasks.pop(name, None)
    if existing:
        existing.cancel()
    _schedule_intervals.pop(name, None)

    if body.interval_seconds is not None:
        if body.interval_seconds < MIN_INTERVAL_SECONDS:
            raise HTTPException(status_code=422, detail=f"interval_seconds must be >= {MIN_INTERVAL_SECONDS}")
        _schedule_intervals[name] = body.interval_seconds
        _schedule_tasks[name] = asyncio.create_task(_schedule_loop(name, body.interval_seconds))

    return {"feed": name, "schedule_interval_seconds": _schedule_intervals.get(name)}
