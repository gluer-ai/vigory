"""USGS GeoJSON summary feed: global M2.5+ earthquakes over the past day.

API reference: https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
No auth required. A single GET returns a GeoJSON FeatureCollection; each
Feature's geometry.coordinates is [lon, lat, depth_km].
"""
import httpx
from neo4j import AsyncSession

from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"


async def _fetch_features(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        resp = await client.get(USGS_URL)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    return data.get("features") or []


def _features_to_entities(features: list[dict]) -> list[EntityCreate]:
    entities = []
    for feature in features:
        feature_id = feature.get("id")
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        if not feature_id or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        depth_km = coords[2] if len(coords) > 2 else None
        entities.append(
            EntityCreate(
                entity_id=f"USGS-EARTHQUAKE-{feature_id}",
                entity_class="EVENT",
                entity_subclass="EVENT.INCIDENT.NATURAL_DISASTER",
                label=props.get("place") or feature_id,
                status="historical",
                confidence="B2",
                source_ref="earthquakes",
                attrs={
                    "lat": lat,
                    "lon": lon,
                    "depth_km": depth_km,
                    "magnitude": props.get("mag"),
                    "mag_type": props.get("magType"),
                    "time": props.get("time"),
                    "tsunami": props.get("tsunami"),
                    "alert": props.get("alert"),
                    "url": props.get("url"),
                },
            )
        )
    return entities


async def poll_earthquakes(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    features = await _fetch_features(client=client)
    entities = _features_to_entities(features)
    written = await upsert_entities(session, entities)
    return {"fetched": len(features), "written": written}
