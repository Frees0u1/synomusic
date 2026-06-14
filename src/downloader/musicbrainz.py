from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from rapidfuzz import fuzz

from src.downloader.models import TrackInfo
from src.matcher import normalize


DEFAULT_MUSICBRAINZ_USER_AGENT = "SynoMusic/0.1 (local downloader metadata resolver)"
OFFICIAL_ENGLISH_TITLE = re.compile(r"official english title\s*:\s*([^\r\n]+)", re.IGNORECASE)


@dataclass(frozen=True)
class MusicBrainzResolution:
    aliases: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class MusicBrainzResolver:
    def __init__(
        self,
        base_url: str = "https://musicbrainz.org/ws/2",
        user_agent: str = DEFAULT_MUSICBRAINZ_USER_AGENT,
        enabled: bool = True,
        min_score: int = 85,
        timeout: int = 15,
        rate_limit_seconds: float = 1.05,
        session: requests.Session | None = None,
        logger: logging.Logger | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._enabled = enabled
        self._min_score = min_score
        self._timeout = timeout
        self._rate_limit_seconds = max(0.0, rate_limit_seconds)
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._logger = logger or logging.getLogger(__name__)
        self._last_request_at = 0.0
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}

    def resolve(self, track: TrackInfo) -> MusicBrainzResolution:
        if not self._enabled:
            return MusicBrainzResolution()

        aliases: dict[str, list[str]] = {}
        notes: list[str] = []

        artist = self._search_artist(track.artist)
        if artist:
            _add_aliases(aliases, track.artist, self._artist_names(artist))
            notes.append(f"artist:{artist.get('name', '')}")

        release_group = self._search_release_group(track, artist_id=artist.get("id") if artist else "")
        if release_group:
            release_group = self._lookup_release_group(str(release_group.get("id"))) or release_group
            _add_aliases(aliases, track.album, self._release_group_names(release_group))
            notes.append(f"release-group:{release_group.get('title', '')}")

        recording = self._search_recording(track, artist_id=artist.get("id") if artist else "")
        if recording:
            _add_aliases(aliases, track.title, self._recording_names(recording))
            notes.append(f"recording:{recording.get('title', '')}")

        return MusicBrainzResolution(aliases=aliases, notes=notes)

    def _search_artist(self, artist: str) -> dict[str, Any] | None:
        if not artist:
            return None
        data = self._get("artist", {"query": f'artist:"{artist}"', "fmt": "json", "limit": "5"})
        candidates = data.get("artists", [])
        return _best_scored(candidates, artist, "name", self._min_score)

    def _search_release_group(self, track: TrackInfo, artist_id: str = "") -> dict[str, Any] | None:
        if not track.album:
            return None
        if artist_id:
            query = f'arid:{artist_id} AND releasegroup:"{track.album}"'
        elif track.artist:
            query = f'artist:"{track.artist}" AND releasegroup:"{track.album}"'
        else:
            query = f'releasegroup:"{track.album}"'
        data = self._get("release-group", {"query": query, "fmt": "json", "limit": "5"})
        candidates = [
            item for item in data.get("release-groups", [])
            if item.get("primary-type") in (None, "Album", "EP", "Single")
        ]
        return _best_scored(candidates, track.album, "title", self._min_score)

    def _lookup_release_group(self, release_group_id: str) -> dict[str, Any] | None:
        if not release_group_id:
            return None
        return self._get(
            f"release-group/{release_group_id}",
            {"inc": "aliases+annotation+releases", "fmt": "json"},
        )

    def _search_recording(self, track: TrackInfo, artist_id: str = "") -> dict[str, Any] | None:
        if not track.title:
            return None
        if artist_id:
            query = f'arid:{artist_id} AND recording:"{track.title}"'
        elif track.artist:
            query = f'artist:"{track.artist}" AND recording:"{track.title}"'
        else:
            query = f'recording:"{track.title}"'
        data = self._get("recording", {"query": query, "fmt": "json", "limit": "5"})
        return _best_scored(data.get("recordings", []), track.title, "title", self._min_score)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        key = (path, tuple(sorted(params.items())))
        if key in self._cache:
            return self._cache[key]

        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._rate_limit_seconds:
            time.sleep(self._rate_limit_seconds - elapsed)

        resp = self._session.get(f"{self._base_url}/{path.lstrip('/')}", params=params, timeout=self._timeout)
        self._last_request_at = time.monotonic()
        resp.raise_for_status()
        data = resp.json()
        self._cache[key] = data
        return data

    def _artist_names(self, artist: dict[str, Any]) -> list[str]:
        names = [artist.get("name", "")]
        for alias in artist.get("aliases", []):
            if alias.get("type") == "Search hint":
                continue
            names.append(alias.get("name", ""))
        return _unique_names(names)

    def _release_group_names(self, release_group: dict[str, Any]) -> list[str]:
        names = [release_group.get("title", "")]
        for alias in release_group.get("aliases", []):
            names.append(alias.get("name", ""))
        for release in release_group.get("releases", []):
            if release.get("status") in (None, "Official", "Pseudo-Release"):
                names.append(release.get("title", ""))
        annotation = release_group.get("annotation", "")
        match = OFFICIAL_ENGLISH_TITLE.search(annotation or "")
        if match:
            names.append(match.group(1).strip())
        return _unique_names(_split_combined_titles(names))

    def _recording_names(self, recording: dict[str, Any]) -> list[str]:
        names = [recording.get("title", "")]
        for alias in recording.get("aliases", []):
            names.append(alias.get("name", ""))
        for release in recording.get("releases", []):
            for medium in release.get("media", []):
                for track in medium.get("track", []):
                    names.append(track.get("title", ""))
        return _unique_names(names)


def _best_scored(items: list[dict[str, Any]], expected: str, field: str, min_score: int) -> dict[str, Any] | None:
    if not items:
        return None
    expected_norm = normalize(expected)

    def score(item: dict[str, Any]) -> int:
        api_score = _to_int(item.get("score"))
        text_score = fuzz.token_set_ratio(expected_norm, normalize(str(item.get(field, "")))) if expected_norm else 0
        return max(api_score, int(text_score))

    best = max(items, key=score)
    return best if score(best) >= min_score else None


def _add_aliases(target: dict[str, list[str]], source: str, aliases: list[str]) -> None:
    if not source:
        return
    additions: list[str] = []
    for alias in aliases:
        alias = alias.strip()
        if alias and normalize(alias) != normalize(source) and alias not in additions:
            additions.append(alias)
    if not additions:
        return
    bucket = target.setdefault(source, [])
    for alias in additions:
        if alias not in bucket:
            bucket.append(alias)


def _split_combined_titles(names: list[str]) -> list[str]:
    split_names: list[str] = []
    for name in names:
        if not name:
            continue
        split_names.append(name)
        for part in re.split(r"\s+[/-]\s+|\s{2,}", name):
            part = part.strip()
            if part and part != name:
                split_names.append(part)
    return split_names


def _unique_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name:
            continue
        key = normalize(name)
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
