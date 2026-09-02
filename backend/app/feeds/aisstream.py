"""aisstream.io WebSocket client: live AIS vessel position reports.

API reference: https://aisstream.io/documentation
Connect to wss://stream.aisstream.io/v0/stream, send one JSON subscription
(APIKey, BoundingBoxes, FilterMessageTypes) within 3s, then read
PositionReport messages off the socket. There is no REST alternative; this
listens for a bounded window (AISSTREAM_LISTEN_SECONDS) and closes — same
"one poll = one bounded call" shape as the other feeds.
"""
import asyncio
import json

import websockets
from neo4j import AsyncSession

from app.config import get_settings
from app.feeds.bbox import SWEDEN_BBOX
from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    if not raw:
        return SWEDEN_BBOX
    parts = [float(p) for p in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"AISSTREAM_BBOX must be 'latmin,latmax,lonmin,lonmax', got: {raw!r}")
    return tuple(parts)  # type: ignore[return-value]


def _message_to_entity(message: dict) -> EntityCreate | None:
    if message.get("MessageType") != "PositionReport":
        return None
    meta = message.get("MetaData") or {}
    report = (message.get("Message") or {}).get("PositionReport") or {}
    mmsi = meta.get("MMSI")
    lat, lon = meta.get("Latitude"), meta.get("Longitude")
    if mmsi is None or lat is None or lon is None:
        return None
    ship_name = (meta.get("ShipName") or "").strip()
    return EntityCreate(
        entity_id=f"AISSTREAM-{mmsi}",
        entity_class="VEHICLE",
        entity_subclass="VEHICLE.SEA_VEHICLE.MERCHANT_VESSEL",
        label=ship_name or str(mmsi),
        status="active",
        confidence="B2",
        source_ref="vessels",
        attrs={
            "lat": lat,
            "lon": lon,
            "sog": report.get("Sog"),
            "cog": report.get("Cog"),
            "nav_status": report.get("NavigationalStatus"),
            "mmsi": mmsi,
        },
    )


async def _collect_messages(*, ws_connect=None) -> list[dict]:
    settings = get_settings()
    if not settings.aisstream_api_key:
        raise RuntimeError("AISSTREAM_API_KEY is not configured")

    lat_min, lat_max, lon_min, lon_max = _parse_bbox(settings.aisstream_bbox)
    subscription = {
        "APIKey": settings.aisstream_api_key,
        "BoundingBoxes": [[[lat_min, lon_min], [lat_max, lon_max]]],
        "FilterMessageTypes": ["PositionReport"],
    }

    connect = ws_connect or websockets.connect
    messages: list[dict] = []
    async with connect(AISSTREAM_URL) as ws:
        await ws.send(json.dumps(subscription))
        try:
            async with asyncio.timeout(settings.aisstream_listen_seconds):
                async for raw in ws:
                    messages.append(json.loads(raw))
        except (TimeoutError, asyncio.TimeoutError):
            pass
    return messages


async def poll_vessels(session: AsyncSession, *, ws_connect=None) -> dict:
    messages = await _collect_messages(ws_connect=ws_connect)
    entities = [e for e in (_message_to_entity(m) for m in messages) if e is not None]
    written = await upsert_entities(session, entities)
    return {"fetched": len(messages), "written": written}
