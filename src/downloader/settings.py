from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

from src.downloader.musicbrainz import DEFAULT_MUSICBRAINZ_USER_AGENT, MusicBrainzResolver
from src.downloader.providers import DICMusicProvider, PirateBayProvider, TorrentProvider
from src.downloader.scoring import merge_aliases
from src.downloader.selection import TorrentSelector
from src.downloader.service import TorrentDownloadService
from src.downloader.synology import DownloadStationClient


DEFAULT_QUERY_ALIASES: dict[str, list[str]] = {}


@dataclass(frozen=True)
class DownloaderSettings:
    synology_url: str
    synology_user: str
    synology_password: str
    destination: str = "music/lib"
    dicmusic_base_url: str = "https://dicmusic.com"
    dicmusic_user: str = ""
    dicmusic_password: str = ""
    dicmusic_cookie: str = ""
    piratebay_api_url: str = "https://apibay.org"
    piratebay_timeout_seconds: int = 8
    piratebay_retries: int = 2
    piratebay_query_delay_seconds: float = 1.0
    piratebay_rate_limit_cooldown_seconds: int = 180
    query_aliases: dict[str, list[str]] = field(default_factory=dict)
    musicbrainz_enabled: bool = True
    musicbrainz_base_url: str = "https://musicbrainz.org/ws/2"
    musicbrainz_user_agent: str = DEFAULT_MUSICBRAINZ_USER_AGENT
    musicbrainz_min_score: int = 85
    musicbrainz_rate_limit_seconds: float = 1.05
    auto_threshold: int = 88
    min_threshold: int = 70
    poll_interval: int = 15
    timeout_seconds: Optional[int] = None

    @classmethod
    def from_env(cls) -> "DownloaderSettings":
        load_dotenv()
        missing = [
            key
            for key in ("SYNOLOGY_URL", "SYNOLOGY_USER", "SYNOLOGY_PASSWORD")
            if not os.getenv(key)
        ]
        if missing:
            raise ValueError(f"Missing downloader env vars: {', '.join(missing)}")

        return cls(
            synology_url=os.environ["SYNOLOGY_URL"].rstrip("/"),
            synology_user=os.environ["SYNOLOGY_USER"],
            synology_password=os.environ["SYNOLOGY_PASSWORD"],
            destination=os.getenv("SYNOLOGY_DOWNLOAD_DESTINATION", "music/lib"),
            dicmusic_base_url=os.getenv("DICMUSIC_BASE_URL", "https://dicmusic.com").rstrip("/"),
            dicmusic_user=os.getenv("DICMUSIC_USER", ""),
            dicmusic_password=os.getenv("DICMUSIC_PASSWORD", ""),
            dicmusic_cookie=os.getenv("DICMUSIC_COOKIE", ""),
            piratebay_api_url=os.getenv("PIRATEBAY_API_URL", "https://apibay.org").rstrip("/"),
            piratebay_timeout_seconds=_env_int("PIRATEBAY_TIMEOUT_SECONDS", 8),
            piratebay_retries=_env_int("PIRATEBAY_RETRIES", 2),
            piratebay_query_delay_seconds=_env_float("PIRATEBAY_QUERY_DELAY_SECONDS", 1.0),
            piratebay_rate_limit_cooldown_seconds=_env_int("PIRATEBAY_RATE_LIMIT_COOLDOWN_SECONDS", 180),
            query_aliases=merge_aliases(
                DEFAULT_QUERY_ALIASES,
                _parse_query_aliases(os.getenv("DOWNLOADER_QUERY_ALIASES", "")),
            ),
            musicbrainz_enabled=_env_bool("MUSICBRAINZ_ENABLED", True),
            musicbrainz_base_url=os.getenv("MUSICBRAINZ_BASE_URL", "https://musicbrainz.org/ws/2").rstrip("/"),
            musicbrainz_user_agent=os.getenv("MUSICBRAINZ_USER_AGENT", DEFAULT_MUSICBRAINZ_USER_AGENT),
            musicbrainz_min_score=_env_int("MUSICBRAINZ_MIN_SCORE", 85),
            musicbrainz_rate_limit_seconds=_env_float("MUSICBRAINZ_RATE_LIMIT_SECONDS", 1.05),
            auto_threshold=_env_int("DOWNLOADER_AUTO_THRESHOLD", 88),
            min_threshold=_env_int("DOWNLOADER_MIN_THRESHOLD", 70),
            poll_interval=_env_int("DOWNLOADER_POLL_INTERVAL", 15),
            timeout_seconds=_env_optional_int("DOWNLOADER_TIMEOUT_SECONDS"),
        )

    def build_providers(self) -> list[TorrentProvider]:
        providers: list[TorrentProvider] = []
        if self.dicmusic_cookie or (self.dicmusic_user and self.dicmusic_password):
            providers.append(
                DICMusicProvider(
                    base_url=self.dicmusic_base_url,
                    username=self.dicmusic_user or None,
                    password=self.dicmusic_password or None,
                    cookie=self.dicmusic_cookie or None,
                )
            )
        providers.append(PirateBayProvider(
            api_url=self.piratebay_api_url,
            timeout=self.piratebay_timeout_seconds,
            retries=self.piratebay_retries,
            query_delay_seconds=self.piratebay_query_delay_seconds,
            rate_limit_cooldown_seconds=self.piratebay_rate_limit_cooldown_seconds,
        ))
        return providers

    def build_metadata_resolver(self) -> MusicBrainzResolver | None:
        if not self.musicbrainz_enabled:
            return None
        return MusicBrainzResolver(
            base_url=self.musicbrainz_base_url,
            user_agent=self.musicbrainz_user_agent,
            min_score=self.musicbrainz_min_score,
            rate_limit_seconds=self.musicbrainz_rate_limit_seconds,
        )

    def build_service(self) -> TorrentDownloadService:
        return TorrentDownloadService(
            providers=self.build_providers(),
            download_client=DownloadStationClient(
                self.synology_url,
                self.synology_user,
                self.synology_password,
            ),
            destination=self.destination,
            selector=TorrentSelector(
                auto_threshold=self.auto_threshold,
                min_threshold=self.min_threshold,
            ),
            query_aliases=self.query_aliases,
            metadata_resolver=self.build_metadata_resolver(),
        )


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{key} must be an integer, got: {raw!r}")


def _env_optional_int(key: str) -> Optional[int]:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{key} must be an integer, got: {raw!r}")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{key} must be a number, got: {raw!r}")


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_query_aliases(raw: str) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"DOWNLOADER_QUERY_ALIASES item must be source=alias, got: {item!r}")
        source, values = item.split("=", 1)
        source = source.strip()
        replacements = [v.strip() for v in re.split(r"[|,]", values) if v.strip()]
        if source and replacements:
            aliases[source] = replacements
    return aliases
