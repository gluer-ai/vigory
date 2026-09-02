"""Sveriges Radio open API client: latest news broadcast episodes (Ekot +
regional news programs).

API reference: https://api.sr.se/api/documentation/v2/metoder/nyheter.html
GET /api/v2/news/episodes?format=json returns {"episodes": [{id, title,
description, url, program, publishdateutc, ...}]}. No auth required.
Unmaintained-but-live per SR's own docs; dates are .NET JSON ticks
("/Date(1234567890000)/") which we convert to Unix milliseconds.
"""
import re

import httpx
from neo4j import AsyncSession

from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

SR_NEWS_EPISODES_URL = "https://api.sr.se/api/v2/news/episodes"

_DOTNET_DATE_RE = re.compile(r"/Date\((\d+)\)/")


def _parse_dotnet_date_ms(value: str | None) -> int | None:
    if not value:
        return None
    match = _DOTNET_DATE_RE.match(value)
    return int(match.group(1)) if match else None


async def _fetch_episodes(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        resp = await client.get(SR_NEWS_EPISODES_URL, params={"format": "json"})
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    return data.get("episodes") or []


def _episodes_to_entities(episodes: list[dict]) -> list[EntityCreate]:
    entities = []
    for episode in episodes:
        episode_id = episode.get("id")
        if not episode_id:
            continue
        program = episode.get("program") or {}
        entities.append(
            EntityCreate(
                entity_id=f"SR-NEWS-{episode_id}",
                entity_class="INFORMATION_OBJECT",
                entity_subclass="INFORMATION_OBJECT.OPEN_SOURCE_ITEM.BROADCAST_SEGMENT",
                label=episode.get("title") or f"Episode {episode_id}",
                status="active",
                confidence="B2",
                source_ref="radio_news",
                attrs={
                    "url": episode.get("url"),
                    "description": episode.get("description"),
                    "program_name": program.get("name"),
                    "program_id": program.get("id"),
                    "published_at_ms": _parse_dotnet_date_ms(episode.get("publishdateutc")),
                    "imageurl": episode.get("imageurl"),
                },
            )
        )
    return entities


async def poll_radio_news(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    episodes = await _fetch_episodes(client=client)
    entities = _episodes_to_entities(episodes)
    written = await upsert_entities(session, entities)
    return {"fetched": len(episodes), "written": written}
