"""Caltrans (California Department of Transportation) CCTV camera catalog.

API reference: https://cwwp2.dot.ca.gov/data/d{N}/cctv/cctvStatusD{NN}.json —
one JSON feed per district (1-12), identical schema statewide, free and
keyless. Districts are fetched in parallel and fail independently: one
district's outage never blocks the others (CALTRANS_DISTRICTS setting picks
which districts to poll; default covers the four busiest metros rather than
all 12 every poll).
"""
import asyncio
import re

import httpx
from neo4j import AsyncSession

from app.config import get_settings
from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

# Leading token of locationName is the stable camera code
# ("TV102 -- I-580 : West of SR-24").
_CODE_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*--\s*")

_TRUSTED_PHOTO_HOST = "https://cwwp2.dot.ca.gov/"


def _district_url(district: int) -> str:
    return f"https://cwwp2.dot.ca.gov/data/d{district}/cctv/cctvStatusD{district:02d}.json"


def _parse_districts(raw: str) -> list[int]:
    districts = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            n = int(token)
        except ValueError:
            continue
        if 1 <= n <= 12:
            districts.append(n)
    return districts


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _fetch_district_rows(district: int, *, client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(_district_url(district))
    resp.raise_for_status()
    data = resp.json()
    return data.get("data") or []


async def _fetch_rows(
    districts: list[int], *, client: httpx.AsyncClient | None = None
) -> list[tuple[int, dict]]:
    """Returns a flat list of (district, cctv_row) pairs across every
    district that responded successfully; a failed district contributes
    nothing rather than aborting the whole poll."""
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        results = await asyncio.gather(
            *(_fetch_district_rows(d, client=client) for d in districts),
            return_exceptions=True,
        )
    finally:
        if owns_client:
            await client.aclose()

    rows: list[tuple[int, dict]] = []
    for district, result in zip(districts, results):
        if isinstance(result, BaseException):
            continue
        for row in result:
            cctv = row.get("cctv") if isinstance(row, dict) else None
            if cctv:
                rows.append((district, cctv))
    return rows


def _cameras_to_entities(rows: list[tuple[int, dict]]) -> list[EntityCreate]:
    entities = []
    for district, cctv in rows:
        if str(cctv.get("inService", "")).lower() != "true":
            continue

        location = cctv.get("location") or {}
        lat = _to_float(location.get("latitude"))
        lon = _to_float(location.get("longitude"))
        if lat is None or lon is None:
            continue

        image_url = str(((cctv.get("imageData") or {}).get("static") or {}).get("currentImageURL") or "")
        if not image_url.startswith(_TRUSTED_PHOTO_HOST):
            continue

        location_name = str(location.get("locationName") or "").strip()
        match = _CODE_RE.match(location_name)
        code = (match.group(1) if match else str(cctv.get("index") or "unknown")).lower()
        camera_id = f"D{district}-{code}"

        label = _CODE_RE.sub("", location_name).strip() or f"Caltrans D{district} {code}"
        nearby_place = str(location.get("nearbyPlace") or "").strip()
        if nearby_place:
            label = f"{label} ({nearby_place})"

        entities.append(
            EntityCreate(
                entity_id=f"CALTRANS-CAMERA-{camera_id}",
                entity_class="EQUIPMENT",
                entity_subclass="EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA",
                label=label,
                status="active",
                confidence="B2",
                source_ref="caltrans",
                attrs={
                    "lat": lat,
                    "lon": lon,
                    "photo_url": image_url,
                    "city": nearby_place or f"Caltrans D{district}",
                    "provider": "Caltrans",
                },
            )
        )
    return entities


async def poll_cameras(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    districts = _parse_districts(get_settings().caltrans_districts)
    rows = await _fetch_rows(districts, client=client)
    entities = _cameras_to_entities(rows)
    written = await upsert_entities(session, entities)
    return {"fetched": len(rows), "written": written}
