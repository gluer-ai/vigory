"""FastAPI app factory."""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_auth
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

    from app.api import auth as auth_api
    from app.api import documents, entities, feeds, ingest, links, sandbox, scenarios, schema, voice

    # /health and /auth/* stay public; everything else requires a token
    # whenever AUTH_PASSWORD is set.
    app.include_router(auth_api.router)
    protected = [Depends(require_auth)]
    for module in (entities, links, schema, scenarios, ingest, documents, feeds, voice, sandbox):
        app.include_router(module.router, dependencies=protected)

    return app


app = create_app()
