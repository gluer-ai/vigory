"""Trafikverket Open API client: Sweden traffic incidents (Situation) and
traffic cameras (RoadConditionCamera), mapped onto the existing ontology and
written straight to committed graph state via app.feeds.sink.

API reference: https://api.trafikinfo.trafikverket.se/API/Model — a single
POST endpoint takes an XML <REQUEST> query and returns JSON shaped as
RESPONSE.RESULT[0][<objecttype>]. All geometry is available as WGS84, given
as a "POINT (lon lat)" string.
"""
import re

import httpx
from neo4j import AsyncSession

from app.config import get_settings
from app.feeds.bbox import in_bbox
from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

TRAFIKVERKET_URL = "https://api.trafikinfo.trafikverket.se/v2/data.json"

_SITUATION_INCLUDES = [
    "Id",
    "Deviation.Id",
    "Deviation.Header",
    "Deviation.Message",
    "Deviation.MessageType",
    "Deviation.SeverityText",
    "Deviation.StartTime",
    "Deviation.EndTime",
    "Deviation.RoadNumber",
    "Deviation.CountyNo",
    "Deviation.Geometry.WGS84",
]
_CAMERA_INCLUDES = [
    "Id",
    "Name",
    "CountyNo",
    "PhotoUrl",
    "PhotoTime",
    "Status",
    "Geometry.WGS84",
]

_POINT_RE = re.compile(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)")


def _build_query_xml(api_key: str, objecttype: str, schemaversion: str, includes: list[str]) -> str:
    include_tags = "".join(f"<INCLUDE>{name}</INCLUDE>" for name in includes)
    return (
        "<REQUEST>"
        f'<LOGIN authenticationkey="{api_key}" />'
        f'<QUERY objecttype="{objecttype}" schemaversion="{schemaversion}">{include_tags}</QUERY>'
        "</REQUEST>"
    )


async def _fetch_objects(
    objecttype: str,
    schemaversion: str,
    includes: list[str],
    *,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """POST the Trafikverket query and return the raw list of result rows.
    Accepts an injectable httpx client so tests can pass a MockTransport
    instead of hitting the network."""
    settings = get_settings()
    xml = _build_query_xml(settings.trafikverket_api_key, objecttype, schemaversion, includes)
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        resp = await client.post(TRAFIKVERKET_URL, content=xml, headers={"Content-Type": "text/xml"})
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    try:
        return data["RESPONSE"]["RESULT"][0][objecttype]
    except (KeyError, IndexError, TypeError):
        return []


def _parse_wgs84(geometry: dict | None) -> tuple[float, float] | None:
    """Extract (lat, lon) from a Geometry.WGS84 "POINT (lon lat)" string."""
    if not geometry:
        return None
    match = _POINT_RE.search(geometry.get("WGS84") or "")
    if not match:
        return None
    lon, lat = float(match.group(1)), float(match.group(2))
    return lat, lon


def _situations_to_entities(situations: list[dict]) -> list[EntityCreate]:
    entities = []
    for situation in situations:
        for deviation in situation.get("Deviation") or []:
            point = _parse_wgs84(deviation.get("Geometry"))
            if point is None or not in_bbox(*point):
                continue
            lat, lon = point
            deviation_id = deviation.get("Id") or situation.get("Id")
            if not deviation_id:
                continue
            entities.append(
                EntityCreate(
                    entity_id=f"TRAFIKVERKET-SITUATION-{deviation_id}",
                    entity_class="EVENT",
                    entity_subclass="EVENT.INCIDENT.TRAFFIC_INCIDENT",
                    label=deviation.get("Header") or deviation.get("Message") or deviation_id,
                    status="active",
                    confidence="B2",
                    source_ref="trafikverket",
                    attrs={
                        "lat": lat,
                        "lon": lon,
                        "message": deviation.get("Message"),
                        "message_type": deviation.get("MessageType"),
                        "severity": deviation.get("SeverityText"),
                        "road_number": deviation.get("RoadNumber"),
                        "county_no": deviation.get("CountyNo"),
                        "start_time": deviation.get("StartTime"),
                        "end_time": deviation.get("EndTime"),
                    },
                )
            )
    return entities


def _cameras_to_entities(cameras: list[dict]) -> list[EntityCreate]:
    entities = []
    for camera in cameras:
        point = _parse_wgs84(camera.get("Geometry"))
        if point is None or not in_bbox(*point):
            continue
        lat, lon = point
        camera_id = camera.get("Id")
        if not camera_id:
            continue
        # Known Trafikverket URL convention when PhotoUrl isn't included.
        photo_url = camera.get("PhotoUrl") or (
            f"https://api.trafikinfo.trafikverket.se/v1/Images/RoadConditionCamera_{camera_id}.Jpeg?type=fullsize"
        )
        entities.append(
            EntityCreate(
                entity_id=f"TRAFIKVERKET-CAMERA-{camera_id}",
                entity_class="EQUIPMENT",
                entity_subclass="EQUIPMENT.SENSOR_AND_SURVEILLANCE.TRAFFIC_CAMERA",
                label=camera.get("Name") or camera_id,
                status="active" if (camera.get("Status") or "Active") == "Active" else "inactive",
                confidence="B2",
                source_ref="trafikverket",
                attrs={
                    "lat": lat,
                    "lon": lon,
                    "county_no": camera.get("CountyNo"),
                    "photo_url": photo_url,
                    "photo_time": camera.get("PhotoTime"),
                },
            )
        )
    return entities


async def poll_situations(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    situations = await _fetch_objects("Situation", "1.5", _SITUATION_INCLUDES, client=client)
    entities = _situations_to_entities(situations)
    written = await upsert_entities(session, entities)
    return {"fetched": len(situations), "written": written}


async def poll_cameras(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    cameras = await _fetch_objects("RoadConditionCamera", "1.0", _CAMERA_INCLUDES, client=client)
    entities = _cameras_to_entities(cameras)
    written = await upsert_entities(session, entities)
    return {"fetched": len(cameras), "written": written}
