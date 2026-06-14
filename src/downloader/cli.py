from rich.console import Console

from src.downloader.models import TrackInfo
from src.downloader.search import run_search_confirm_submit
from src.downloader.settings import DownloaderSettings


def run_track_download_from_env(track: object, console: Console) -> None:
    info = TrackInfo.from_netease(track)
    run_manual_track_download_from_env(info, console, wait=True)


def run_manual_track_download_from_env(
    track: TrackInfo,
    console: Console,
    wait: bool = False,
) -> None:
    try:
        settings = DownloaderSettings.from_env()
    except ValueError as exc:
        console.print(f"[red]下载配置错误：{exc}[/red]")
        return

    try:
        run_search_confirm_submit(
            track,
            settings,
            console,
            wait=wait,
            poll_interval=settings.poll_interval,
            timeout_seconds=settings.timeout_seconds,
        )
    except Exception as exc:
        console.print(f"[red]下载失败：{exc}[/red]")
