from __future__ import annotations

import argparse
from collections.abc import Sequence

from rich import box
from rich.console import Console
from rich.table import Table

from src.downloader.models import DownloadProgress, DownloadTask, TorrentSearchResult, TrackInfo
from src.downloader.settings import DownloaderSettings
from src.downloader.synology import normalize_download_destination


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Search torrent providers by song metadata and submit the selected result to Synology",
    )
    parser.add_argument("title", nargs="?", help="song title; prompted when omitted")
    parser.add_argument("--artist", default="", help="artist name")
    parser.add_argument("--album", default="", help="album name")
    parser.add_argument("--limit", type=int, default=8, help="max candidates per provider")
    parser.add_argument("--wait", action="store_true", help="poll Download Station until the task finishes")
    parser.add_argument("--poll-interval", type=int, default=None, help="override polling interval in seconds")
    parser.add_argument("--timeout-seconds", type=int, default=None, help="override download wait timeout")
    args = parser.parse_args(argv)

    console = Console()
    try:
        settings = DownloaderSettings.from_env()
    except ValueError as exc:
        console.print(f"[red]下载配置错误：{exc}[/red]")
        return

    track = _track_from_args_or_prompt(args, console)
    if not track.title:
        console.print("[red]歌曲名不能为空。[/red]")
        return

    try:
        run_search_confirm_submit(
            track,
            settings,
            console,
            limit_per_provider=args.limit,
            wait=args.wait,
            poll_interval=args.poll_interval or settings.poll_interval,
            timeout_seconds=args.timeout_seconds if args.timeout_seconds is not None else settings.timeout_seconds,
        )
    except Exception as exc:
        console.print(f"[red]下载提交失败：{exc}[/red]")


def run_search_confirm_submit(
    track: TrackInfo,
    settings: DownloaderSettings,
    console: Console,
    limit_per_provider: int = 8,
    wait: bool = False,
    poll_interval: int = 15,
    timeout_seconds: int | None = None,
) -> DownloadProgress | DownloadTask | None:
    service = settings.build_service()
    with console.status("正在检索 DIC Music 和 Pirate Bay..."):
        result_sets = service.search_by_provider(track, limit_per_provider=limit_per_provider)

    candidates = _ordered_candidates(result_sets)
    if not candidates:
        console.print(f"[yellow]未找到种子候选：{track.display_name}[/yellow]")
        return None

    _print_results(console, result_sets)
    selected = _prompt_selection(console, candidates)
    if selected is None:
        console.print("[dim]已取消。[/dim]")
        return None

    console.print(
        f"将提交到 Synology： [bold]{selected.source}[/bold] "
        f"[dim]score={selected.score} seeders={selected.seeders}[/dim]\n{selected.title}"
    )
    api_destination = normalize_download_destination(settings.destination)
    if api_destination != settings.destination:
        console.print(
            f"[dim]Download Station destination: {api_destination} "
            f"(from {settings.destination})[/dim]"
        )
    confirm = console.input(f"确认提交到 {api_destination}？[y/N] ").strip().lower()
    if confirm not in {"y", "yes"}:
        console.print("[dim]已取消。[/dim]")
        return None

    result = service.submit_result(
        selected,
        console=console,
        wait=wait,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
        completion_title=track.display_name,
    )
    if isinstance(result, DownloadTask):
        console.print(
            f"[green]已提交[/green] task_id={result.id or '-'} "
            f"destination={result.destination}"
        )
    return result


def _track_from_args_or_prompt(args: argparse.Namespace, console: Console) -> TrackInfo:
    title = (args.title or "").strip()
    artist = (args.artist or "").strip()
    album = (args.album or "").strip()

    if not title:
        title = console.input("歌曲名：").strip()
    if not artist:
        artist = console.input("歌手（可空）：").strip()
    if not album:
        album = console.input("专辑（可空）：").strip()

    return TrackInfo(title=title, artist=artist, album=album)


def _ordered_candidates(result_sets: dict[str, list[TorrentSearchResult]]) -> list[TorrentSearchResult]:
    candidates: list[TorrentSearchResult] = []
    for results in result_sets.values():
        candidates.extend(results)
    return sorted(candidates, key=lambda r: (r.score, r.seeders), reverse=True)


def _print_results(console: Console, result_sets: dict[str, list[TorrentSearchResult]]) -> None:
    for provider_name, results in result_sets.items():
        if not results:
            console.print(f"[yellow]{provider_name}: no results[/yellow]")

    table = Table(box=box.SIMPLE)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("来源")
    table.add_column("匹配", justify="right")
    table.add_column("Seed", justify="right")
    table.add_column("大小", justify="right")
    table.add_column("标题")
    for idx, result in enumerate(_ordered_candidates(result_sets), 1):
        table.add_row(
            str(idx),
            result.source,
            str(result.score),
            str(result.seeders),
            _format_size(result.size_bytes),
            result.title,
        )
    console.print(table)


def _prompt_selection(
    console: Console,
    candidates: list[TorrentSearchResult],
) -> TorrentSearchResult | None:
    while True:
        raw = console.input("选择要提交的编号，输入 n 取消：").strip().lower()
        if raw in {"n", "no", "q", ""}:
            return None
        try:
            idx = int(raw) - 1
        except ValueError:
            console.print("[red]请输入候选编号或 n。[/red]")
            continue
        if 0 <= idx < len(candidates):
            return candidates[idx]
        console.print("[red]编号超出范围。[/red]")


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


if __name__ == "__main__":
    main()
