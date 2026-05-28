import os
import pytest
from unittest.mock import patch


def test_config_loads_required_fields():
    env = {
        "NETEASE_API_URL": "http://localhost:3000",
        "NAVIDROME_URL": "http://localhost:4533",
        "NAVIDROME_USER": "admin",
        "NAVIDROME_PASSWORD": "pass",
        "FUZZY_MATCH_THRESHOLD": "80",
        "TOP_PLAYLIST_LIMIT": "20",
        "HISTORY_FILE": "./data/history.json",
    }
    with patch.dict(os.environ, env, clear=True):
        from src.config import Config
        cfg = Config()
        assert cfg.netease_api_url == "http://localhost:3000"
        assert cfg.navidrome_url == "http://localhost:4533"
        assert cfg.navidrome_user == "admin"
        assert cfg.navidrome_password == "pass"
        assert cfg.fuzzy_match_threshold == 80
        assert cfg.top_playlist_limit == 20
        assert cfg.history_file == "./data/history.json"


def test_config_raises_on_missing_required():
    with patch.dict(os.environ, {}, clear=True), patch("src.config.load_dotenv"):
        from src.config import Config
        with pytest.raises(ValueError, match="NAVIDROME_URL"):
            Config()


def test_config_strips_trailing_slash_from_urls():
    env = {
        "NETEASE_API_URL": "http://localhost:3000/",
        "NAVIDROME_URL": "http://localhost:4533/",
        "NAVIDROME_USER": "admin",
        "NAVIDROME_PASSWORD": "pass",
    }
    with patch.dict(os.environ, env, clear=True):
        from src.config import Config
        cfg = Config()
        assert cfg.netease_api_url == "http://localhost:3000"
        assert cfg.navidrome_url == "http://localhost:4533"
