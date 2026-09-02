"""OpenSky Network REST API client: live aircraft state vectors.

API reference: https://github.com/openskynetwork/opensky-api/blob/master/docs/free/rest.rst
GET /states/all returns {"time": ..., "states": [[icao24, callsign, ...], ...]}
(a fixed-order array per state vector; see _STATE_FIELDS). Anonymous requests
work (rate-limited); optional OAuth2 client-credentials raise the limit.
"""
import time

import httpx
from neo4j import AsyncSession

from app.config import get_settings
from app.feeds.bbox import SWEDEN_BBOX, in_bbox
from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

STATES_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

# Index of each field within a /states/all state vector row.
_STATE_FIELDS = [
    "icao24", "callsign", "origin_country", "time_position", "last_contact",
    "longitude", "latitude", "baro_altitude", "on_ground", "velocity",
    "true_track", "vertical_rate", "sensors", "geo_altitude", "squawk",
    "spi", "position_source", "category",
]

# simplification: process-lifetime in-memory token cache (module-level, not
# per-request) — fine for a single-worker backend; a multi-worker deployment
# would want a shared cache. Upgrade path: move to Redis/Neo4j if needed.
_token_cache: dict[str, float | str] = {}


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    if not raw:
        return SWEDEN_BBOX
    parts = [float(p) for p in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"OPENSKY_BBOX must be 'latmin,latmax,lonmin,lonmax', got: {raw!r}")
    return tuple(parts)  # type: ignore[return-value]


async def _get_token(client: httpx.AsyncClient) -> str | None:
    """Exchange OpenSky client credentials for a Bearer token, cached until
    near expiry. Returns None if no credentials are configured (anonymous
    mode)."""
    settings = get_settings()
    if not settings.opensky_client_id or not settings.opensky_client_secret:
        return None

    cached = _token_cache.get("token")
    expires_at = _token_cache.get("expires_at", 0)
    if cached and isinstance(expires_at, (int, float)) and time.monotonic() < expires_at:
        return cached  # type: ignore[return-value]

    resp = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.opensky_client_id,
            "client_secret": settings.opensky_client_secret,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    expires_in = data.get("expires_in", 1800)
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.monotonic() + expires_in - 30
    return token


async def _fetch_states(*, client: httpx.AsyncClient | None = None) -> list[list]:
    settings = get_settings()
    lat_min, lat_max, lon_min, lon_max = _parse_bbox(settings.opensky_bbox)
    params = {"lamin": lat_min, "lamax": lat_max, "lomin": lon_min, "lomax": lon_max}

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = await client.get(STATES_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    return data.get("states") or []


def _states_to_entities(states: list[list], bbox: tuple[float, float, float, float] = SWEDEN_BBOX) -> list[EntityCreate]:
    entities = []
    for row in states:
        state = dict(zip(_STATE_FIELDS, row))
        icao24 = state.get("icao24")
        lat, lon = state.get("latitude"), state.get("longitude")
        if not icao24 or lat is None or lon is None or not in_bbox(lat, lon, bbox):
            continue
        callsign = (state.get("callsign") or "").strip()
        entities.append(
            EntityCreate(
                entity_id=f"OPENSKY-{icao24}",
                entity_class="VEHICLE",
                entity_subclass="VEHICLE.AIR_VEHICLE.FIXED_WING_AIRCRAFT",
                label=callsign or icao24,
                status="active",
                confidence="B2",
                source_ref="aircraft",
                attrs={
                    "lat": lat,
                    "lon": lon,
                    "altitude_m": state.get("baro_altitude"),
                    "velocity_mps": state.get("velocity"),
                    "heading_deg": state.get("true_track"),
                    "vertical_rate": state.get("vertical_rate"),
                    "on_ground": state.get("on_ground"),
                    "origin_country": state.get("origin_country"),
                    "last_contact": state.get("last_contact"),
                },
            )
        )
    return entities


async def poll_aircraft(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    settings = get_settings()
    bbox = _parse_bbox(settings.opensky_bbox)
    states = await _fetch_states(client=client)
    entities = _states_to_entities(states, bbox)
    written = await upsert_entities(session, entities)
    return {"fetched": len(states), "written": written}
