"""City of Austin Open Data: traffic camera catalog.

API reference: https://data.austintexas.gov/resource/b4k4-adkb.json (Socrata
SODA API) — free, keyless, ~1000 rows. Only TURNED_ON cameras are kept; the
dataset also carries DESIRED (planned, not built), REMOVED, and VOID rows
whose screenshot URLs never resolve.
"""
import httpx
from neo4j import AsyncSession

from app.feeds.bbox import in_bbox
from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

AUSTIN_URL = "https://data.austintexas.gov/resource/b4k4-adkb.json?$limit=5000"

# Austin metro sanity check — a guard against stray/erroneous coordinates in
# what's otherwise an inherently Austin-only dataset, not a scoping
# mechanism (see app/feeds/bbox.py's module docstring for the same rationale
# applied to Trafikverket/Sweden).
AUSTIN_BBOX = (30.02, 30.58, -98.12, -97.4)

_TRUSTED_PHOTO_HOST = "https://cctv.austinmobility.io/"


async def _fetch_rows(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        resp = await client.get(AUSTIN_URL)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    return data if isinstance(data, list) else []


def _cameras_to_entities(rows: list[dict]) -> list[EntityCreate]:
    entities = []
    for row in rows:
        if (row.get("camera_status") or "").upper() != "TURNED_ON":
            continue
        camera_id = row.get("camera_id")
        if not camera_id:
            continue

        photo_url = row.get("screenshot_address") or ""
        if not photo_url.startswith(_TRUSTED_PHOTO_HOST):
            continue

        coords = ((row.get("location") or {}).get("coordinates")) or []
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if not in_bbox(lat, lon, AUSTIN_BBOX):
            continue

        entities.append(
            EntityCreate(
                entity_id=f"AUSTIN-CAMERA-{camera_id}",
                entity_class="EQUIPMENT",
                entity_subclass="EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA",
                label=(row.get("location_name") or f"Austin Camera {camera_id}").strip(),
                status="active",
                confidence="B2",
                source_ref="austin",
                attrs={
                    "lat": lat,
                    "lon": lon,
                    "photo_url": photo_url,
                    "city": "Austin",
                    "provider": "Austin Transportation and Public Works",
                },
            )
        )
    return entities


async def poll_cameras(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    rows = await _fetch_rows(client=client)
    entities = _cameras_to_entities(rows)
    written = await upsert_entities(session, entities)
    return {"fetched": len(rows), "written": written}
