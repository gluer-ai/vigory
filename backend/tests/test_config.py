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
