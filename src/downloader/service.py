from __future__ import annotations

import logging
import re

from rich.console import Console

from src.downloader.models import DownloadProgress, DownloadTask, TorrentSearchResult, TrackInfo
from src.downloader.musicbrainz import MusicBrainzResolver
from src.downloader.providers import TorrentProvider
from src.downloader.scoring import QueryAliases, merge_aliases
from src.downloader.selection import TorrentSelector
from src.downloader.synology import DownloadStationClient


class TorrentDownloadService:
    def __init__(
        self,
        providers: list[TorrentProvider],
        download_client: DownloadStationClient,
        destination: str = "music/lib",
        selector: TorrentSelector | None = None,
        query_aliases: QueryAliases | None = None,
        metadata_resolver: MusicBrainzResolver | None = None,
        logger: logging.Logger | None = None,
    ):
        self._providers = {provider.name: provider for provider in providers}
        self._download_client = download_client
        self._destination = destination
        self._selector = selector or TorrentSelector()
        self._query_aliases = dict(query_aliases or {})
        self._metadata_resolver = metadata_resolver
        self._logger = logger or logging.getLogger(__name__)

    def search(self, track: TrackInfo, limit_per_provider: int = 10) -> list[TorrentSearchResult]:
        all_results: list[TorrentSearchResult] = []
        for results in self.search_by_provider(track, limit_per_provider).values():
            all_results.extend(results)
        return sorted(all_results, key=lambda r: (r.score, r.seeders), reverse=True)

    def search_by_provider(
        self,
        track: TrackInfo,
        limit_per_provider: int = 10,
    ) -> dict[str, list[TorrentSearchResult]]:
        result_sets: dict[str, list[TorrentSearchResult]] = {}
        base_aliases = dict(self._query_aliases)
        active_aliases = base_aliases

        dic_missing = "dicmusic" not in self._providers
        dic_provider = self._providers.get("dicmusic")
        if dic_provider is not None:
            dic_results = self._search_provider(
                dic_provider,
                track,
                limit_per_provider,
                aliases=base_aliases or None,
                log_empty=False,
            )
            dic_missing = not dic_results
            result_sets["dicmusic"] = dic_results

        if dic_missing and _track_has_cjk(track):
            active_aliases = self._resolve_aliases(track)
            if active_aliases != base_aliases and dic_provider is not None:
                alias_results = self._search_provider(
                    dic_provider,
                    track,
                    limit_per_provider,
                    aliases=active_aliases,
                    log_empty=False,
                )
                if alias_results:
                    result_sets["dicmusic"] = alias_results
                    dic_missing = False

        for provider_name, provider in self._providers.items():
            if provider_name == "dicmusic":
                continue
            result_sets[provider_name] = self._search_provider(
                provider,
                track,
                limit_per_provider,
                aliases=active_aliases,
                log_empty=False,
            )
        if not any(result_sets.values()):
            self._logger.warning("未找到种子候选：%s", track.search_label)
        return result_sets

    def _search_provider(
        self,
        provider: TorrentProvider,
        track: TrackInfo,
        limit_per_provider: int,
        aliases: QueryAliases | None = None,
        log_empty: bool = True,
    ) -> list[TorrentSearchResult]:
        try:
            results = provider.search(track, limit=limit_per_provider, aliases=aliases)
        except Exception as exc:
            self._logger.warning("%s 检索失败：%s", provider.name, exc)
            return []
        if not results:
            if log_empty:
                self._logger.warning("%s 未找到候选：%s", provider.name, track.search_label)
            return []
        return sorted(
            results,
            key=lambda r: (r.score, r.seeders),
            reverse=True,
        )

    def _resolve_aliases(self, track: TrackInfo) -> dict[str, list[str]]:
        resolved = {}
        if self._metadata_resolver is not None:
            try:
                resolved = self._metadata_resolver.resolve(track).aliases
            except Exception as exc:
                self._logger.warning("MusicBrainz metadata resolve failed: %s", exc)
        return merge_aliases(self._query_aliases, resolved)

    def download_track(
        self,
        track: TrackInfo,
        console: Console | None = None,
        interactive: bool = True,
        wait: bool = True,
        poll_interval: int = 15,
        timeout_seconds: int | None = None,
    ) -> DownloadProgress | DownloadTask | None:
        results = self.search(track)
        selected = self._selector.select(track, results, console=console, interactive=interactive)
        if selected is None:
            return None

        return self.submit_result(
            selected,
            console=console,
            wait=wait,
            poll_interval=poll_interval,
            timeout_seconds=timeout_seconds,
            completion_title=track.display_name,
        )

    def submit_result(
        self,
        result: TorrentSearchResult,
        console: Console | None = None,
        wait: bool = True,
        poll_interval: int = 15,
        timeout_seconds: int | None = None,
        completion_title: str = "",
    ) -> DownloadProgress | DownloadTask:
        provider = self._providers.get(result.source)
        if provider is None:
            raise ValueError(f"Unknown torrent provider: {result.source}")

        resolved = provider.resolve(result)
        if console:
            console.print(f"[cyan]提交下载[/cyan] {resolved.source}: {resolved.title}")
        task = self._download_client.create_task(resolved.url, self._destination)
        if not wait:
            return task

        progress = self._download_client.wait_for_completion(
            task.id,
            uri=task.uri,
            poll_interval=poll_interval,
            timeout_seconds=timeout_seconds,
            on_progress=lambda p: _print_progress(console, p),
        )
        if console:
            console.print(f"[green]下载完成[/green] {progress.title or completion_title}")
        return progress


def _print_progress(console: Console | None, progress: DownloadProgress) -> None:
    if not console:
        return
    speed = _format_size(progress.download_speed) + "/s" if progress.download_speed else "-"
    console.print(
        f"[dim]{progress.status:<12}[/dim] "
        f"{progress.percent:5.1f}%  "
        f"{_format_size(progress.downloaded_bytes)}/{_format_size(progress.size_bytes)}  "
        f"{speed}  {progress.title}"
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def _track_has_cjk(track: TrackInfo) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", " ".join([track.title, track.artist, track.album])))
