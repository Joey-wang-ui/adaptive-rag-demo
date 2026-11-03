from pathlib import Path

from app.config.settings import Settings


def test_safe_default_is_authenticated_isolated_jiahui(monkeypatch):
    monkeypatch.delenv("BUSINESS_PROFILE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.business_profile == "jiahui"
    assert "jiahui" in settings.database_url
    assert Path(settings.knowledge_dir).parts[-2:] == ("jiahui", "knowledge")
    assert settings.mcp_enabled is False
    assert not settings.redis_url and not settings.qdrant_url
