from __future__ import annotations

import math
import re
from collections.abc import Mapping

from rapidfuzz import fuzz

from src.downloader.models import TrackInfo
from src.matcher import normalize


QUALITY_HINTS = re.compile(r"\b(flac|lossless|24bit|24-bit|hi-?res|wav|ape|dsd|dsf)\b", re.IGNORECASE)
LOSSLESS_HINTS = re.compile(r"\b(flac|lossless|wav|ape|dsd|dsf)\b", re.IGNORECASE)
LOSSY_HINTS = re.compile(r"\b(mp3|aac|ogg|opus|320\s?kbps|256\s?kbps|128\s?kbps)\b", re.IGNORECASE)
NOISE_HINTS = re.compile(r"\b(discography|全集|合集|collection|top\s*\d+|various artists)\b", re.IGNORECASE)
VERSION_MISMATCH_HINTS = re.compile(
    r"remix|live|cover|karaoke|acoustic|instrumental|伴奏|重混音|摇滚版|现场|演唱会|翻唱",
    re.IGNORECASE,
)
OFFICIAL_RELEASE_HINTS = re.compile(r"专辑|album|single|单曲|ep|录音室|studio|web|cd", re.IGNORECASE)
SPECIAL_RELEASE_HINTS = re.compile(r"重混音|remix|live|cover|现场|演唱会|翻唱|伴奏", re.IGNORECASE)
SINGLE_SIZE_LIMIT_BYTES = 200 * 1024 * 1024


QueryAliases = Mapping[str, list[str]]


def merge_aliases(*alias_maps: QueryAliases | None) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for alias_map in alias_maps:
        if not alias_map:
            continue
        for source, aliases in alias_map.items():
            bucket = merged.setdefault(source, [])
            for alias in aliases:
                if alias and alias not in bucket:
                    bucket.append(alias)
    return merged


def build_search_queries(track: TrackInfo, aliases: QueryAliases | None = None) -> list[str]:
    artist_variants = _field_variants(track.artist, aliases)
    title_variants = _field_variants(track.title, aliases)
    album_variants = _field_variants(track.album, aliases)
    has_distinct_album = bool(track.album and normalize(track.album) != normalize(track.title))

    parts = {
        "artist_title": [
            " ".join(p for p in [artist, title] if p).strip()
            for artist in artist_variants for title in title_variants
        ],
        "artist_album_title": [
            " ".join(p for p in [artist, album, title] if p).strip()
            for artist in artist_variants for album in album_variants for title in title_variants
            if has_distinct_album
        ],
        "title_artist": [
            " ".join(p for p in [title, artist] if p).strip()
            for title in title_variants for artist in artist_variants
        ],
        "artist_album": [
            " ".join(p for p in [artist, album] if p).strip()
            for artist in artist_variants for album in album_variants
        ],
        "album": [
            album.strip()
            for album in album_variants
            if has_distinct_album and album.strip()
        ],
        "title": [
            title.strip()
            for title in title_variants
            if title.strip()
        ],
    }
    queries: list[str] = []
    for key in ("artist_title", "artist_album", "artist_album_title", "album", "title", "title_artist"):
        for value in parts[key]:
            if value and value not in queries:
                queries.append(value)
    return queries[:24]


def score_torrent_title(
    track: TrackInfo,
    result_title: str,
    seeders: int = 0,
    size_bytes: int = 0,
    release_kind: str = "",
    aliases: QueryAliases | None = None,
) -> int:
    scores = [
        _score_torrent_title_once(track, result_title, seeders, size_bytes, release_kind)
        for track in _track_variants(track, aliases)
    ]
    return max(scores) if scores else 0


def _score_torrent_title_once(
    track: TrackInfo,
    result_title: str,
    seeders: int = 0,
    size_bytes: int = 0,
    release_kind: str = "",
) -> int:
    candidate = normalize(result_title)
    release = normalize(release_kind)
    title = normalize(track.title)
    artist = normalize(track.artist)
    album = normalize(track.album)

    query = normalize(" ".join(p for p in [track.artist, track.title] if p))
    title_artist_score = fuzz.token_set_ratio(query, candidate) if query else 0
    title_score = fuzz.partial_ratio(title, candidate) if title else 0
    artist_score = fuzz.partial_ratio(artist, candidate) if artist else 0
    album_score = fuzz.partial_ratio(album, candidate) if album else 0
    album_artist_query = normalize(" ".join(p for p in [track.artist, track.album] if p))
    album_artist_score = fuzz.token_set_ratio(album_artist_query, candidate) if album_artist_query else 0
    strong_artist_match = not artist or artist_score >= 75
    strong_title_match = not title or title_score >= 84
    strong_album_match = bool(album and album_score >= 88 and album_artist_score >= 75)

    weighted = title_artist_score * 0.45 + title_score * 0.3 + artist_score * 0.2
    if album:
        weighted += album_score * 0.05

    if strong_artist_match and strong_album_match:
        weighted = max(weighted, 78 + (album_artist_score - 80) * 0.25)

    text_for_hints = f"{result_title} {release_kind}"
    quality_bonus = 0
    if LOSSLESS_HINTS.search(text_for_hints):
        quality_bonus = 8
    elif QUALITY_HINTS.search(text_for_hints):
        quality_bonus = 4
    if LOSSY_HINTS.search(text_for_hints):
        quality_bonus -= 4

    is_special_mismatch = (
        bool(SPECIAL_RELEASE_HINTS.search(text_for_hints) or VERSION_MISMATCH_HINTS.search(text_for_hints))
        and not bool(VERSION_MISMATCH_HINTS.search(track.title))
    )
    official_bonus = 0
    if OFFICIAL_RELEASE_HINTS.search(text_for_hints) and not is_special_mismatch:
        official_bonus = 6

    size_bonus = _size_bonus(track, result_title, size_bytes, release_kind)
    noise_penalty = 8 if NOISE_HINTS.search(result_title) and normalize(track.album) not in candidate else 0
    version_penalty = 0
    if is_special_mismatch:
        version_penalty = 32 if album else 22

    weighted += quality_bonus + official_bonus + size_bonus
    weighted -= noise_penalty + version_penalty

    if not strong_artist_match:
        weighted = min(weighted, 58)
    elif not strong_title_match and not strong_album_match:
        weighted = min(weighted, 62)

    weighted += min(8, math.log1p(max(0, seeders)) * 2)
    return max(0, min(100, int(round(weighted))))


def _size_bonus(track: TrackInfo, result_title: str, size_bytes: int, release_kind: str = "") -> int:
    if size_bytes <= 0:
        return 0

    candidate = normalize(result_title)
    title = normalize(track.title)
    album = normalize(track.album)
    release = normalize(release_kind)

    album_candidate = bool(album and album in candidate) or "专辑" in release or "album" in release
    single_candidate = bool(title and title in candidate and not album_candidate)
    if "单曲" in release or "single" in release:
        single_candidate = True

    if not single_candidate:
        return 0
    if size_bytes <= SINGLE_SIZE_LIMIT_BYTES:
        return 6
    if size_bytes <= 300 * 1024 * 1024:
        return 2
    return -25


def _track_variants(track: TrackInfo, aliases: QueryAliases | None = None) -> list[TrackInfo]:
    if not aliases:
        return [track]

    variants: list[TrackInfo] = []
    seen: set[tuple[str, str, str]] = set()
    for artist in _field_variants(track.artist, aliases):
        for title in _field_variants(track.title, aliases):
            for album in _field_variants(track.album, aliases):
                key = (artist, title, album)
                if key in seen:
                    continue
                seen.add(key)
                variants.append(
                    TrackInfo(
                        id=track.id,
                        title=title,
                        artist=artist,
                        album=album,
                        duration_ms=track.duration_ms,
                    )
                )
    return variants[:40]


def _field_variants(value: str, aliases: QueryAliases | None = None) -> list[str]:
    if not value:
        return [""]

    variants: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(value)
    if aliases:
        value_norm = normalize(value)
        for source, replacements in aliases.items():
            if normalize(source) == value_norm:
                for replacement in replacements:
                    add(replacement)

    for candidate in list(variants):
        for replacement in _punctuation_variants(candidate):
            add(replacement)
    return variants


def _punctuation_variants(value: str) -> list[str]:
    variants: list[str] = []

    def add(candidate: str) -> None:
        candidate = " ".join(candidate.split())
        if candidate and candidate != value and candidate not in variants:
            variants.append(candidate)

    curly = value.replace("'", "’")
    straight = value.replace("’", "'").replace("‘", "'")
    stripped = re.sub(r"['’‘]", "", straight)
    terminal_stripped = re.sub(r"[.。!！?？]+$", "", straight).strip()
    slash_spaced = re.sub(r"[/／_]+", " ", straight).strip()
    slash_stripped = re.sub(r"[/／_]+", "", straight).strip()
    for candidate in (curly, straight, stripped):
        add(candidate)
    for candidate in (terminal_stripped, slash_spaced, slash_stripped):
        add(candidate)
    for part in re.split(r"[/／_]+", straight):
        if len(part.strip()) >= 4:
            add(part.strip())
    return variants
