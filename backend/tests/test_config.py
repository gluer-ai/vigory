"""Unit tests for Settings.cors_origin_list."""
from app.config import Settings


def test_cors_origin_list_wildcard_by_default():
    settings = Settings(cors_allowed_origins="*")
    assert settings.cors_origin_list == ["*"]


def test_cors_origin_list_splits_comma_separated_origins():
    settings = Settings(cors_allowed_origins="https://a.example.com, https://b.example.com")
    assert settings.cors_origin_list == ["https://a.example.com", "https://b.example.com"]


def test_cors_origin_list_single_origin():
    settings = Settings(cors_allowed_origins="https://vigory-frontend.up.railway.app")
    assert settings.cors_origin_list == ["https://vigory-frontend.up.railway.app"]


def test_upload_settings_have_sane_defaults():
    settings = Settings(upload_dir="uploads", max_upload_mb=20, ingest_chunk_chars=8000)
    assert settings.upload_dir == "uploads"
    assert settings.max_upload_mb == 20
    assert settings.ingest_chunk_chars == 8000
