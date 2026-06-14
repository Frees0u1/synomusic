from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import Iterable, Protocol
from urllib.parse import quote, urljoin

import requests

from src.downloader.models import TorrentSearchResult, TrackInfo
from src.downloader.scoring import QueryAliases, build_search_queries, merge_aliases, score_torrent_title


class TorrentProvider(Protocol):
    name: str

    def search(
        self,
        track: TrackInfo,
        limit: int = 20,
        aliases: QueryAliases | None = None,
    ) -> list[TorrentSearchResult]:
        ...

    def resolve(self, result: TorrentSearchResult) -> TorrentSearchResult:
        ...


DEFAULT_TRACKERS = (
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
)
OFFICIAL_OR_SPECIAL_RELEASE_KIND = re.compile(
    r"专辑|album|single|单曲|ep|录音室|studio|web|cd|重混音|remix|live|cover|现场|演唱会|翻唱|伴奏",
    re.IGNORECASE,
)


class PirateBayRateLimitError(RuntimeError):
    pass


class PirateBayProvider:
    name = "piratebay"
    audio_music_categories = {"101", "104"}

    def __init__(
        self,
        api_url: str = "https://apibay.org",
        session: requests.Session | None = None,
        timeout: int = 8,
        retries: int = 2,
        query_delay_seconds: float = 0.0,
        rate_limit_cooldown_seconds: int = 180,
        query_aliases: dict[str, list[str]] | None = None,
    ):
        self._api_url = api_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout = timeout
        self._retries = max(1, retries)
        self._query_delay_seconds = max(0.0, query_delay_seconds)
        self._rate_limit_cooldown_seconds = max(0, rate_limit_cooldown_seconds)
        self._cooldown_until = 0.0
        self._last_query_at = 0.0
        self._query_aliases = query_aliases or {}

    def search(
        self,
        track: TrackInfo,
        limit: int = 20,
        aliases: QueryAliases | None = None,
    ) -> list[TorrentSearchResult]:
        if self._cooldown_remaining_seconds() > 0:
            return []

        results: dict[str, TorrentSearchResult] = {}
        query_aliases = merge_aliases(self._query_aliases, aliases or {})
        queries = build_search_queries(track, aliases=query_aliases)
        failures: list[str] = []
        rate_limited: PirateBayRateLimitError | None = None
        for query in queries:
            try:
                items = self._fetch_query(query)
            except PirateBayRateLimitError as exc:
                failures.append(f"{query}: {exc}")
                rate_limited = exc
                break
            except requests.RequestException as exc:
                failures.append(f"{query}: {exc}")
                continue
            for item in items:
                if str(item.get("id")) == "0":
                    continue
                category = str(item.get("category", ""))
                if category and category not in self.audio_music_categories:
                    continue
                info_hash = str(item.get("info_hash", "")).strip().upper()
                title = html.unescape(str(item.get("name", ""))).strip()
                if not info_hash or not title:
                    continue
                seeders = _to_int(item.get("seeders"))
                result = TorrentSearchResult(
                    source=self.name,
                    title=title,
                    url=magnet_uri(info_hash, title),
                    seeders=seeders,
                    leechers=_to_int(item.get("leechers")),
                    size_bytes=_to_int(item.get("size")),
                    category=category,
                    info_hash=info_hash,
                    raw=dict(item),
                ).with_score(
                    score_torrent_title(
                        track,
                        title,
                        seeders,
                        size_bytes=_to_int(item.get("size")),
                        aliases=query_aliases,
                    )
                )
                existing = results.get(info_hash)
                if existing is None or result.score > existing.score:
                    results[info_hash] = result
        if not results and rate_limited is not None:
            raise RuntimeError(f"Pirate Bay rate limited: {rate_limited}")
        if not results and failures and len(failures) == len(queries):
            raise RuntimeError(f"all Pirate Bay queries failed; last error: {failures[-1]}")
        return _sort_results(results.values())[:limit]

    def resolve(self, result: TorrentSearchResult) -> TorrentSearchResult:
        return result

    def _fetch_query(self, query: str) -> list[dict]:
        last_exc: requests.RequestException | None = None
        for _ in range(self._retries):
            try:
                self._throttle()
                resp = self._session.get(
                    f"{self._api_url}/q.php",
                    params={"q": query},
                    timeout=self._timeout,
                )
                if getattr(resp, "status_code", 0) == 429:
                    retry_after = _parse_retry_after(getattr(resp, "headers", {}).get("Retry-After"))
                    self._start_cooldown(retry_after)
                    remaining = self._cooldown_remaining_seconds()
                    raise PirateBayRateLimitError(f"HTTP 429 Too Many Requests; cooling down {remaining}s")
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        return []

    def _throttle(self) -> None:
        if self._query_delay_seconds <= 0:
            return
        now = time.monotonic()
        wait_seconds = self._query_delay_seconds - (now - self._last_query_at)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        self._last_query_at = time.monotonic()

    def _start_cooldown(self, retry_after_seconds: int | None = None) -> None:
        cooldown = retry_after_seconds or self._rate_limit_cooldown_seconds
        if cooldown <= 0:
            return
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + cooldown)

    def _cooldown_remaining_seconds(self) -> int:
        return max(0, int(round(self._cooldown_until - time.monotonic())))


class DICMusicProvider:
    name = "dicmusic"

    def __init__(
        self,
        base_url: str = "https://dicmusic.com",
        username: str | None = None,
        password: str | None = None,
        cookie: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 15,
    ):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._cookie = cookie
        self._session = session or requests.Session()
        self._timeout = timeout
        self._authenticated = False
        if cookie:
            self._session.headers.update({"Cookie": cookie})

    def login(self) -> None:
        if self._authenticated:
            return
        if self._cookie:
            self._authenticated = True
            return
        if not self._username or not self._password:
            raise RuntimeError("DIC Music credentials or DICMUSIC_COOKIE are required")

        self._session.get(f"{self._base_url}/login.php", timeout=self._timeout).raise_for_status()
        resp = self._session.post(
            f"{self._base_url}/login.php",
            data={
                "username": self._username,
                "password": self._password,
                "keeplogged": "1",
                "login": "登录",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        if _looks_like_login_page(resp.text):
            detail = _extract_login_failure(resp.text)
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"DIC Music login failed{suffix}")
        self._authenticated = True

    def search(
        self,
        track: TrackInfo,
        limit: int = 20,
        aliases: QueryAliases | None = None,
    ) -> list[TorrentSearchResult]:
        self.login()
        results: dict[str, TorrentSearchResult] = {}
        for query in build_search_queries(track, aliases=aliases):
            resp = self._session.get(
                f"{self._base_url}/torrents.php",
                params={"searchstr": query},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            if _looks_like_login_page(resp.text):
                self._authenticated = False
                detail = _extract_login_failure(resp.text)
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(f"DIC Music session expired or authentication failed{suffix}")

            for result in self._parse_results(resp.text, track, aliases=aliases):
                key = result.info_hash or result.url or result.detail_url
                existing = results.get(key)
                if existing is None or result.score > existing.score:
                    results[key] = result
        return _sort_results(results.values())[:limit]

    def resolve(self, result: TorrentSearchResult) -> TorrentSearchResult:
        if _is_download_href(result.url) or result.url.startswith("magnet:"):
            return result
        self.login()
        detail_url = result.detail_url or result.url
        resp = self._session.get(detail_url, timeout=self._timeout)
        resp.raise_for_status()
        parser = TorrentHTMLParser()
        parser.feed(resp.text)
        for row in parser.rows:
            for link in row.links:
                if _is_download_href(link.href) or link.href.startswith("magnet:"):
                    return replace(result, url=_absolute_url(self._base_url, link.href))
        for link in parser.links:
            if _is_download_href(link.href) or link.href.startswith("magnet:"):
                return replace(result, url=_absolute_url(self._base_url, link.href))
        raise RuntimeError(f"DIC Music candidate has no downloadable torrent URL: {result.title}")

    def _parse_results(
        self,
        html_text: str,
        track: TrackInfo,
        aliases: QueryAliases | None = None,
    ) -> list[TorrentSearchResult]:
        parser = TorrentHTMLParser()
        parser.feed(html_text)
        parsed: list[TorrentSearchResult] = []
        current_group_title = ""
        current_group_detail_url = ""
        current_release_kind = ""
        for row in parser.rows:
            links = row.links
            download_link = next((l.href for l in links if l.href.startswith("magnet:") or _is_download_href(l.href)), "")
            group_link = next((l for l in links if _is_group_href(l.href)), None)
            torrent_detail_link = next((l.href for l in links if _is_torrent_detail_href(l.href)), "")
            if group_link:
                group_title = _group_title(row)
                if group_title:
                    current_group_title = group_title
                    current_group_detail_url = _absolute_url(self._base_url, group_link.href)
                    current_release_kind = _release_kind(row.text)

            if not download_link and not group_link and not torrent_detail_link:
                continue

            if download_link:
                title = current_group_title or _best_title(row)
                format_title = _torrent_format_title(row)
                if format_title and format_title not in title:
                    title = f"{title} [{format_title}]"
                detail_link = torrent_detail_link or current_group_detail_url
            else:
                title = current_group_title or _best_title(row)
                detail_link = group_link.href if group_link else torrent_detail_link

            if not title:
                continue
            url = _absolute_url(self._base_url, download_link or detail_link)
            detail_url = _absolute_url(self._base_url, detail_link) if detail_link else ""
            seeders = _extract_seeders(row.text)
            size_bytes = _extract_size_bytes(row.text)
            release_kind = current_release_kind if (download_link or current_group_title == title) else _release_kind(row.text)
            parsed.append(
                TorrentSearchResult(
                    source=self.name,
                    title=title,
                    url=url,
                    detail_url=detail_url,
                    seeders=seeders,
                    size_bytes=size_bytes,
                    raw={"row_text": row.text, "release_kind": release_kind},
                ).with_score(
                    score_torrent_title(
                        track,
                        title,
                        seeders,
                        size_bytes=size_bytes,
                        release_kind=release_kind,
                        aliases=aliases,
                    )
                )
            )
        return parsed


@dataclass
class ParsedLink:
    href: str
    text: str


@dataclass
class ParsedRow:
    text: str
    links: list[ParsedLink] = field(default_factory=list)


class TorrentHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[ParsedLink] = []
        self.rows: list[ParsedRow] = []
        self._row_depth = 0
        self._row_text: list[str] = []
        self._row_links: list[ParsedLink] = []
        self._active_href = ""
        self._active_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tr":
            self._row_depth += 1
            if self._row_depth == 1:
                self._row_text = []
                self._row_links = []
        if tag == "a":
            self._active_href = attrs_dict.get("href") or ""
            self._active_link_text = []

    def handle_data(self, data: str) -> None:
        if self._row_depth > 0:
            self._row_text.append(data)
        if self._active_href:
            self._active_link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_href:
            link = ParsedLink(self._active_href, " ".join(self._active_link_text).strip())
            self.links.append(link)
            if self._row_depth > 0:
                self._row_links.append(link)
            self._active_href = ""
            self._active_link_text = []
        if tag == "tr" and self._row_depth > 0:
            self._row_depth -= 1
            if self._row_depth == 0 and self._row_links:
                text = " ".join(" ".join(self._row_text).split())
                self.rows.append(ParsedRow(text=text, links=list(self._row_links)))


def magnet_uri(info_hash: str, title: str) -> str:
    trackers = "".join(f"&tr={quote(tracker, safe='')}" for tracker in DEFAULT_TRACKERS)
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(title)}{trackers}"


def _sort_results(results: Iterable[TorrentSearchResult]) -> list[TorrentSearchResult]:
    return sorted(results, key=lambda r: (r.score, r.seeders, -r.size_bytes), reverse=True)


def _absolute_url(base_url: str, href: str) -> str:
    if href.startswith("magnet:"):
        return href
    return urljoin(base_url.rstrip("/") + "/", href)


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_retry_after(value: object) -> int | None:
    try:
        seconds = int(str(value or "").strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _looks_like_login_page(text: str) -> bool:
    return 'id="loginform"' in text or 'name="login"' in text and "password" in text


def _extract_login_failure(text: str) -> str:
    body = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    visible = " ".join(html.unescape(body).split())
    patterns = (
        r"你还剩余\s*\d+\s*次登录尝试机会.*?小时。",
        r"警告：.*?小时。",
        r"(?:错误|失败|无效|验证码|二次验证|两步验证)[^。.!?]*[。.!?]?",
        r"(?:error|failed|invalid|captcha|two-factor)[^.?!]*[.?!]?",
    )
    for pattern in patterns:
        match = re.search(pattern, visible, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return visible[:180].strip()


def _is_download_href(href: str) -> bool:
    lower = href.lower()
    return ".torrent" in lower or "download" in lower or "action=download" in lower


def _is_group_href(href: str) -> bool:
    lower = href.lower()
    return (
        lower.startswith("torrents.php?id=")
        and "torrentid=" not in lower
        and "action=" not in lower
        and "order_by=" not in lower
        and "searchstr=" not in lower
    )


def _is_torrent_detail_href(href: str) -> bool:
    lower = href.lower()
    return lower.startswith("torrents.php?") and "torrentid=" in lower and "download" not in lower


def _group_title(row: ParsedRow) -> str:
    artist = next((link.text.strip() for link in row.links if link.href.startswith("artist.php")), "")
    title = next((link.text.strip() for link in row.links if _is_group_href(link.href)), "")
    if artist and title:
        return f"{artist} - {title}"
    return title or artist


def _torrent_format_title(row: ParsedRow) -> str:
    ignored = {"dl", "fl", "rp", "download", "下载", "report"}
    for link in row.links:
        text = link.text.strip()
        if text and _is_torrent_detail_href(link.href) and text.lower() not in ignored:
            return text
    return ""


def _release_kind(row_text: str) -> str:
    matches = re.findall(r"[\[【]([^\]】]+)[\]】]", row_text)
    for value in matches:
        if OFFICIAL_OR_SPECIAL_RELEASE_KIND.search(value):
            return value.strip()
    return ""


def _best_title(row: ParsedRow) -> str:
    ignored = {"download", "下载", "snatch", "leech", "view", "torrent"}
    candidates = [
        link.text.strip()
        for link in row.links
        if link.text.strip() and link.text.strip().lower() not in ignored
    ]
    if candidates:
        return max(candidates, key=len)
    return row.text[:180].strip()


def _extract_seeders(row_text: str) -> int:
    # DIC Music is a private Gazelle-style tracker. The exact markup can vary by skin,
    # so this is a best-effort hint; text matching still drives selection.
    tokens = [token for token in row_text.split() if token.isdigit()]
    return _to_int(tokens[-2]) if len(tokens) >= 2 else 0


def _extract_size_bytes(row_text: str) -> int:
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", row_text, re.IGNORECASE)
    if not matches:
        return 0
    value, unit = matches[-1]
    multiplier = {
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
    }[unit.upper()]
    return int(float(value) * multiplier)
