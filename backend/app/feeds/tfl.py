"""TfL (Transport for London) JamCam camera catalog.

API reference: https://api.tfl.gov.uk/Place/Type/JamCam — a single keyless
GET. An optional TFL_APP_KEY only raises the list-endpoint rate limit;
frames come from TfL's public S3 bucket, which isn't rate-limited.
Attribution: "Powered by TfL Open Data" (TfL's open data terms require it).
"""
import httpx
from neo4j import AsyncSession

from app.config import get_settings
from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

TFL_JAMCAM_URL = "https://api.tfl.gov.uk/Place/Type/JamCam"
_TRUSTED_PHOTO_HOST = "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/"


def _additional_property(place: dict, key: str) -> str:
    for prop in place.get("additionalProperties") or []:
        if prop.get("key") == key:
            return str(prop.get("value") or "")
    return ""


async def _fetch_places(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        app_key = get_settings().tfl_app_key
        url = f"{TFL_JAMCAM_URL}?app_key={app_key}" if app_key else TFL_JAMCAM_URL
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    return data if isinstance(data, list) else []


def _cameras_to_entities(places: list[dict]) -> list[EntityCreate]:
    entities = []
    for place in places:
        if _additional_property(place, "available").lower() != "true":
            continue

        image_url = _additional_property(place, "imageUrl")
        if not image_url.startswith(_TRUSTED_PHOTO_HOST):
            continue

        lat, lon = place.get("lat"), place.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue

        raw_id = str(place.get("id") or "").removeprefix("JamCams_")
        if not raw_id:
            continue

        entities.append(
            EntityCreate(
                entity_id=f"TFL-CAMERA-{raw_id}",
                entity_class="EQUIPMENT",
                entity_subclass="EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA",
                label=str(place.get("commonName") or f"JamCam {raw_id}"),
                status="active",
                confidence="B2",
                source_ref="tfl",
                attrs={
                    "lat": lat,
                    "lon": lon,
                    "photo_url": image_url,
                    "city": "London",
                    "provider": "Transport for London",
                },
            )
        )
    return entities


async def poll_cameras(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    places = await _fetch_places(client=client)
    entities = _cameras_to_entities(places)
    written = await upsert_entities(session, entities)
    return {"fetched": len(places), "written": written}
