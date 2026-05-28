import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    netease_api_url: str
    navidrome_url: str
    navidrome_user: str
    navidrome_password: str
    fuzzy_match_threshold: int
    top_playlist_limit: int
    history_file: str

    def __init__(self):
        load_dotenv()

        missing = []
        for key in ("NETEASE_API_URL", "NAVIDROME_URL", "NAVIDROME_USER", "NAVIDROME_PASSWORD"):
            if not os.getenv(key):
                missing.append(key)
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

        self.netease_api_url = os.environ["NETEASE_API_URL"].rstrip("/")
        self.navidrome_url = os.environ["NAVIDROME_URL"].rstrip("/")
        self.navidrome_user = os.environ["NAVIDROME_USER"]
        self.navidrome_password = os.environ["NAVIDROME_PASSWORD"]

        try:
            self.fuzzy_match_threshold = int(os.getenv("FUZZY_MATCH_THRESHOLD", "80"))
        except ValueError:
            raise ValueError(
                f"FUZZY_MATCH_THRESHOLD must be an integer, got: {os.getenv('FUZZY_MATCH_THRESHOLD')!r}"
            )

        try:
            self.top_playlist_limit = int(os.getenv("TOP_PLAYLIST_LIMIT", "20"))
        except ValueError:
            raise ValueError(
                f"TOP_PLAYLIST_LIMIT must be an integer, got: {os.getenv('TOP_PLAYLIST_LIMIT')!r}"
            )

        self.history_file = os.getenv("HISTORY_FILE", "./data/history.json")
