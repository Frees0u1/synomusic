from __future__ import annotations

import logging

from rich import box
from rich.console import Console
from rich.table import Table

from src.downloader.models import TorrentSearchResult, TrackInfo


class TorrentSelector:
    def __init__(self, auto_threshold: int = 88, min_threshold: int = 70, display_limit: int = 10):
        self.auto_threshold = auto_threshold
        self.min_threshold = min_threshold
        self.display_limit = display_limit

    def select(
        self,
        track: TrackInfo,
        results: list[TorrentSearchResult],
        console: Console | None = None,
        interactive: bool = True,
    ) -> TorrentSearchResult | None:
        if not results:
            logging.getLogger(__name__).warning("未找到种子候选：%s", track.display_name)
            if console:
                console.print(f"[yellow]未找到种子候选：{track.display_name}[/yellow]")
            return None

        ordered = sorted(results, key=lambda r: (r.score, r.seeders), reverse=True)
        best = ordered[0]
        runner_up = ordered[1].score if len(ordered) > 1 else 0
        if best.score >= self.auto_threshold and best.score - runner_up >= 4:
            if console:
                console.print(
                    f"[green]自动选择[/green] {best.source}: {best.title} "
                    f"([bold]{best.score}[/bold], seeders {best.seeders})"
                )
            return best

        if not interactive or console is None:
            if best.score >= self.min_threshold:
                return best
            logging.getLogger(__name__).warning(
                "种子候选最高匹配度低于阈值：%s score=%s threshold=%s",
                track.display_name,
                best.score,
                self.min_threshold,
            )
            return None

        self._print_candidates(console, ordered)
        raw = console.input(
            f"选择种子编号，回车使用最高匹配（最低 {self.min_threshold}），输入 n 取消："
        ).strip().lower()
        if raw == "n":
            return None
        if raw == "":
            return best if best.score >= self.min_threshold else None
        try:
            idx = int(raw) - 1
        except ValueError:
            console.print("[red]无效输入，已取消。[/red]")
            return None
        if not 0 <= idx < min(len(ordered), self.display_limit):
            console.print("[red]编号超出范围，已取消。[/red]")
            return None
        return ordered[idx]

    def _print_candidates(self, console: Console, results: list[TorrentSearchResult]) -> None:
        table = Table(box=box.SIMPLE)
        table.add_column("#", justify="right", style="cyan")
        table.add_column("来源")
        table.add_column("匹配", justify="right")
        table.add_column("Seed", justify="right")
        table.add_column("大小", justify="right")
        table.add_column("标题")
        for idx, result in enumerate(results[: self.display_limit], 1):
            table.add_row(
                str(idx),
                result.source,
                str(result.score),
                str(result.seeders),
                _format_size(result.size_bytes),
                result.title,
            )
        console.print(table)


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"
