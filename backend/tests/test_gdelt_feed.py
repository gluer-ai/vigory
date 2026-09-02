"""Tests for the GDELT news feed: URL-hash id generation, entity mapping
against the live ontology, and MERGE idempotency. The GDELT HTTP call is
mocked via httpx.MockTransport — no network access needed. Requires Neo4j
running.
"""
import httpx
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from app.config import get_settings
from app.feeds.gdelt import _article_id, _articles_to_entities, poll_news
from app.ontology.validate import validate_entity

ARTICLE_URL = "https://example.com/news/some-article"


def _doc_response(url: str) -> dict:
    return {
        "articles": [
            {
                "url": url,
                "title": "Something happened somewhere",
                "seendate": "20260901T120000Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "Sweden",
                "socialimage": "https://example.com/img.jpg",
            }
        ]
    }


def _mock_client(response_json: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_json)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db_session():
    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    async with driver.session() as session:
        yield session
    await driver.close()


def test_article_id_is_stable_hash_of_url():
    assert _article_id(ARTICLE_URL) == _article_id(ARTICLE_URL)
    assert _article_id(ARTICLE_URL) != _article_id(ARTICLE_URL + "x")
    assert len(_article_id(ARTICLE_URL)) == 16


def test_articles_to_entities_maps_row():
    payload = _doc_response(ARTICLE_URL)
    entities = _articles_to_entities(payload["articles"])
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == f"GDELT-{_article_id(ARTICLE_URL)}"
    assert entity.entity_class == "INFORMATION_OBJECT"
    assert entity.entity_subclass == "INFORMATION_OBJECT.OPEN_SOURCE_ITEM.NEWS_ARTICLE"
    assert entity.label == "Something happened somewhere"
    assert entity.attrs["url"] == ARTICLE_URL
    assert entity.attrs["domain"] == "example.com"


def test_articles_to_entities_skips_row_without_url():
    payload = _doc_response(ARTICLE_URL)
    payload["articles"][0]["url"] = None
    assert _articles_to_entities(payload["articles"]) == []


@pytest.mark.asyncio
async def test_news_entities_validate_against_live_ontology(db_session):
    """Regression guard for the ontology gap check: INFORMATION_OBJECT.OPEN_SOURCE_ITEM.NEWS_ARTICLE
    must actually exist as a ClassDef key."""
    articles = _doc_response(ARTICLE_URL)["articles"]
    for entity in _articles_to_entities(articles):
        await validate_entity(db_session, entity)  # no raise


@pytest.mark.asyncio
async def test_poll_news_is_idempotent_on_repeated_poll(db_session):
    entity_id = f"GDELT-{_article_id(ARTICLE_URL)}"
    try:
        response = _doc_response(ARTICLE_URL)
        for _ in range(2):
            client = _mock_client(response)
            try:
                counts = await poll_news(db_session, client=client)
            finally:
                await client.aclose()
            assert counts == {"fetched": 1, "written": 1}

        result = await db_session.run(
            "MATCH (e:Entity {entity_id: $id}) RETURN count(e) AS c", id=entity_id
        )
        record = await result.single()
        assert record["c"] == 1
    finally:
        await db_session.run("MATCH (e:Entity {entity_id: $id}) DETACH DELETE e", id=entity_id)
