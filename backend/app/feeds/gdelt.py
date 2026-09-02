"""GDELT DOC 2.0 API client: global news article search.

API reference: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
GET /api/v2/doc/doc?query=...&mode=artlist&format=json returns
{"articles": [{url, title, seendate, domain, language, sourcecountry,
socialimage, ...}]}. Free, keyless, but throttled to ~1 request/5s.

GDELT gives no stable article id, so entity_id hashes the URL (the
standard fallback when a feed has no natural identifier).
"""
import hashlib

import httpx
from neo4j import AsyncSession

from app.config import get_settings
from app.feeds.sink import upsert_entities
from app.models.entity import EntityCreate

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


async def _fetch_articles(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    settings = get_settings()
    params = {
        "query": settings.gdelt_query,
        "mode": "artlist",
        "maxrecords": str(settings.gdelt_maxrecords),
        "format": "json",
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        resp = await client.get(GDELT_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            await client.aclose()
    return data.get("articles") or []


def _article_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _articles_to_entities(articles: list[dict]) -> list[EntityCreate]:
    entities = []
    for article in articles:
        url = article.get("url")
        if not url:
            continue
        entities.append(
            EntityCreate(
                entity_id=f"GDELT-{_article_id(url)}",
                entity_class="INFORMATION_OBJECT",
                entity_subclass="INFORMATION_OBJECT.OPEN_SOURCE_ITEM.NEWS_ARTICLE",
                label=article.get("title") or url,
                status="active",
                confidence="B2",
                source_ref="news",
                attrs={
                    "url": url,
                    "domain": article.get("domain"),
                    "seendate": article.get("seendate"),
                    "language": article.get("language"),
                    "source_country": article.get("sourcecountry"),
                    "socialimage": article.get("socialimage"),
                },
            )
        )
    return entities


async def poll_news(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> dict:
    articles = await _fetch_articles(client=client)
    entities = _articles_to_entities(articles)
    written = await upsert_entities(session, entities)
    return {"fetched": len(articles), "written": written}
