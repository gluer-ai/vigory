"""App configuration loaded from environment / .env."""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme123"

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    backend_port: int = 8000

    # Comma-separated allowed origins for CORS, e.g. "https://app.example.com".
    # Defaults to "*" for local dev; set explicitly in production.
    cors_allowed_origins: str = "*"

    # Trafikverket Open API (Sweden traffic incidents/cameras feed).
    trafikverket_api_key: str = ""
    feeds_enabled: bool = True

    # OpenSky Network (aircraft states). Anonymous mode works with no keys;
    # client id/secret enable OAuth2 for higher rate limits.
    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    opensky_bbox: str = ""  # "latmin,latmax,lonmin,lonmax"; empty -> SWEDEN_BBOX

    # aisstream.io (vessel AIS positions over WebSocket). Requires a free API key.
    aisstream_api_key: str = ""
    aisstream_bbox: str = ""  # "latmin,latmax,lonmin,lonmax"; empty -> SWEDEN_BBOX
    aisstream_listen_seconds: int = 8

    # GDELT DOC 2.0 API (news articles). Free, keyless, throttled to ~1 req/5s.
    gdelt_query: str = "military OR conflict OR security"
    gdelt_maxrecords: int = 50

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
