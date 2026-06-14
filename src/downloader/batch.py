from __future__ import annotations

from dataclasses import dataclass

from rich import box
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.downloader.models import DownloadTask, TorrentSearchResult, TrackInfo
from src.downloader.selection import TorrentSelector
from src.downloader.service import TorrentDownloadService


@dataclass(frozen=True)
class SubmittedDownload:
    track: TrackInfo
    result: TorrentSearchResult
    task: DownloadTask


@dataclass(frozen=True)
class SkippedDownload:
    track: TrackInfo
    reason: str
    best_score: int = 0
    best_title: str = ""


@dataclass(frozen=True)
class FailedDownload:
    track: TrackInfo
    error: str
    result: TorrentSearchResult | None = None


@dataclass(frozen=True)
class BatchDownloadSummary:
    total: int
    submitted: list[SubmittedDownload]
    skipped: list[SkippedDownload]
    failed: list[FailedDownload]

    @property
    def submitted_count(self) -> int:
        return len(self.submitted)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def submitted_size_bytes(self) -> int:
        return sum(max(0, item.result.size_bytes) for item in self.submitted)

    @property
    def unknown_size_count(self) -> int:
        return sum(1 for item in self.submitted if item.result.size_bytes <= 0)


@dataclass(frozen=True)
class BatchDownloadContext:
    playlist_name: str = ""
    playlist_total: int = 0
    library_total: int = 0
    strict_count: int = 0
    fuzzy_count: int = 0
    unmatched_count: int = 0
    unique_matched_count: int = 0
    destination: str = ""


def submit_best_downloads(
    tracks: list[TrackInfo],
    service: TorrentDownloadService,
    selector: TorrentSelector,
    console: Console | None = None,
    limit_per_provider: int = 8,
) -> BatchDownloadSummary:
    submitted: list[SubmittedDownload] = []
    skipped: list[SkippedDownload] = []
    failed: list[FailedDownload] = []

    def process(track: TrackInfo) -> None:
        try:
            results = service.search(track, limit_per_provider=limit_per_provider)
        except Exception as exc:
            failed.append(FailedDownload(track=track, error=str(exc)))
            return

        if not results:
            skipped.append(SkippedDownload(track=track, reason="无候选"))
            return

        selected = selector.select(track, results, interactive=False)
        if selected is None:
            best = results[0]
            skipped.append(
                SkippedDownload(
                    track=track,
                    reason="最高匹配度低于阈值",
                    best_score=best.score,
                    best_title=best.title,
                )
            )
            return

        try:
            task = service.submit_result(selected, wait=False)
        except Exception as exc:
            failed.append(FailedDownload(track=track, error=str(exc), result=selected))
            return

        if not isinstance(task, DownloadTask):
            failed.append(
                FailedDownload(
                    track=track,
                    error=f"unexpected download result: {type(task).__name__}",
                    result=selected,
                )
            )
            return
        submitted.append(SubmittedDownload(track=track, result=selected, task=task))

    if console is None:
        for track in tracks:
            process(track)
    else:
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
            task_id = progress.add_task("正在批量搜索缺失歌曲...", total=len(tracks))
            for track in tracks:
                progress.update(task_id, description=f"检索并提交：{track.search_label}")
                process(track)
                progress.advance(task_id)
            progress.update(task_id, description="批量下载任务处理完成")

    return BatchDownloadSummary(
        total=len(tracks),
        submitted=submitted,
        skipped=skipped,
        failed=failed,
    )


def print_batch_download_summary(
    console: Console,
    summary: BatchDownloadSummary,
    context: BatchDownloadContext | None = None,
) -> None:
    context = context or BatchDownloadContext(unmatched_count=summary.total)
    table = Table(
        title="Missing Download Summary",
        box=box.ASCII,
        show_header=True,
        header_style="bold",
    )
    table.add_column("Section")
    table.add_column("Metric")
    table.add_column("Value", justify="right")

    _add_stat(table, "Playlist", "Name", context.playlist_name or "-")
    _add_stat(table, "Playlist", "Netease tracks", _count(context.playlist_total))
    _add_stat(table, "Library", "Navidrome tracks", _count(context.library_total))
    _add_stat(table, "Match", "Strict", _count(context.strict_count))
    _add_stat(table, "Match", "Fuzzy", _count(context.fuzzy_count))
    _add_stat(table, "Match", "Missing", _count(context.unmatched_count or summary.total))
    _add_stat(table, "Match", "Unique matched", _count(context.unique_matched_count))
    _add_stat(table, "Downloads", "Tasks added", _count(summary.submitted_count))
    _add_stat(table, "Downloads", "Estimated size", _format_gb(summary.submitted_size_bytes))
    if summary.unknown_size_count:
        _add_stat(table, "Downloads", "Unknown sizes", _count(summary.unknown_size_count))
    _add_stat(table, "Downloads", "Skipped", _count(summary.skipped_count))
    _add_stat(table, "Downloads", "Failed", _count(summary.failed_count))
    _add_stat(table, "Synology", "Destination", context.destination or "-")
    console.print()
    console.print(table)

    if summary.submitted:
        table = Table(title="Submitted Tasks", box=box.ASCII)
        table.add_column("#", justify="right", style="cyan")
        table.add_column("歌曲")
        table.add_column("来源")
        table.add_column("匹配", justify="right")
        table.add_column("Seed", justify="right")
        table.add_column("大小", justify="right")
        table.add_column("任务")
        table.add_column("候选")
        for idx, item in enumerate(summary.submitted, 1):
            table.add_row(
                str(idx),
                item.track.search_label,
                item.result.source,
                str(item.result.score),
                str(item.result.seeders),
                _format_size(item.result.size_bytes),
                item.task.id or "-",
                item.result.title,
            )
        console.print(table)

    if summary.skipped:
        table = Table(title="Skipped Tracks", box=box.ASCII)
        table.add_column("歌曲")
        table.add_column("原因")
        table.add_column("最高分", justify="right")
        table.add_column("最佳候选")
        for item in summary.skipped:
            table.add_row(
                item.track.search_label,
                item.reason,
                str(item.best_score) if item.best_score else "-",
                item.best_title,
            )
        console.print(table)

    if summary.failed:
        table = Table(title="Failed Tracks", box=box.ASCII)
        table.add_column("歌曲")
        table.add_column("来源")
        table.add_column("候选")
        table.add_column("错误")
        for item in summary.failed:
            table.add_row(
                item.track.search_label,
                item.result.source if item.result else "-",
                item.result.title if item.result else "-",
                item.error,
            )
        console.print(table)


def _add_stat(table: Table, section: str, metric: str, value: str) -> None:
    table.add_row(section, metric, value)


def _count(value: int) -> str:
    return f"{value:,}"


def _format_gb(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0.00 GB"
    return f"{size_bytes / (1024 ** 3):.2f} GB"


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"
