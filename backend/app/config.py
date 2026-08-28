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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
