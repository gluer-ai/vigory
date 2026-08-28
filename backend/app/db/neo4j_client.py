"""Async Neo4j driver singleton."""
from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config import get_settings

_driver: AsyncDriver | None = None


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def verify_connectivity() -> bool:
    driver = get_driver()
    try:
        await driver.verify_connectivity()
        return True
    except Exception:
        return False
