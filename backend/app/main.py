"""FastAPI app factory."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.neo4j_client import close_driver, verify_connectivity


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    from app.api.feeds import stop_all_schedules

    stop_all_schedules()
    await close_driver()


def create_app() -> FastAPI:
    app = FastAPI(title="Vigory.ai API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        neo4j_ok = await verify_connectivity()
        return {"status": "ok" if neo4j_ok else "degraded", "neo4j": neo4j_ok}

    from app.api import entities, feeds, ingest, links, schema, scenarios

    app.include_router(entities.router)
    app.include_router(links.router)
    app.include_router(schema.router)
    app.include_router(scenarios.router)
    app.include_router(ingest.router)
    app.include_router(feeds.router)

    return app


app = create_app()
