import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from src.matcher import MatchResult, MatchStatus


@dataclass
class SyncSummary:
    playlist_name: str
    netease_id: str
    total: int
    strict_matches: list[tuple[str, str, MatchResult]]   # (title, artist, result)
    fuzzy_matches: list[tuple[str, str, MatchResult]]
    unmatched: list[tuple[str, str]]
    navidrome_playlist_id: Optional[str]


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def print_fuzzy_details(fuzzy_matches: list[tuple[str, str, MatchResult]], console: Console) -> None:
    if not fuzzy_matches:
        return
    table = Table(title="模糊匹配详情", box=box.SIMPLE)
    table.add_column("歌曲", style="yellow")
    table.add_column("艺术家", style="yellow")
    table.add_column("得分", justify="right")
    table.add_column("置信度")
    for title, artist, result in fuzzy_matches:
        conf = "[red]低[/red]" if result.low_confidence else "[green]正常[/green]"
        table.add_row(title, artist, str(result.score), conf)
    console.print(table)


def print_unmatched_list(unmatched: list[tuple[str, str]], console: Console) -> None:
    if not unmatched:
        return
    console.print(f"\n[red]缺失歌曲（{len(unmatched)}首）：[/red]")
    for title, artist in unmatched:
        console.print(f"  · {artist} - {title}")


def print_report(summary: SyncSummary, console: Console) -> None:
    panel_lines = [
        f"歌单共 [bold]{summary.total}[/bold] 首",
        f"✅ 严格匹配  [green]{len(summary.strict_matches)}[/green] 首",
        f"🔶 模糊匹配  [yellow]{len(summary.fuzzy_matches)}[/yellow] 首",
        f"❌ 未匹配    [red]{len(summary.unmatched)}[/red] 首",
    ]
    if summary.navidrome_playlist_id:
        panel_lines.append(f"\n播放列表已创建：[bold]{summary.playlist_name}[/bold]")
    else:
        panel_lines.append("\n[yellow]播放列表未创建（用户取消或无匹配）[/yellow]")

    console.print(Panel("\n".join(panel_lines), title=f"同步报告：{summary.playlist_name}", expand=False))
    print_fuzzy_details(summary.fuzzy_matches, console)
    print_unmatched_list(summary.unmatched, console)


def save_report(summary: SyncSummary, reports_dir: str) -> str:
    os.makedirs(reports_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_name = _safe_filename(summary.playlist_name)
    path = os.path.join(reports_dir, f"{date_str}_{safe_name}.txt")

    lines = [
        f"同步报告：{summary.playlist_name}",
        f"时间：{datetime.now().isoformat(timespec='seconds')}",
        f"歌单共 {summary.total} 首",
        f"✅ 严格匹配  {len(summary.strict_matches)} 首",
        f"🔶 模糊匹配  {len(summary.fuzzy_matches)} 首",
        f"❌ 未匹配    {len(summary.unmatched)} 首",
        "",
    ]
    if summary.fuzzy_matches:
        lines.append("--- 模糊匹配 ---")
        for title, artist, result in summary.fuzzy_matches:
            conf = "（低置信度）" if result.low_confidence else ""
            lines.append(f"  [{result.score}分{conf}] {artist} - {title}")
        lines.append("")

    if summary.unmatched:
        lines.append("--- 缺失歌曲 ---")
        for title, artist in summary.unmatched:
            lines.append(f"  {artist} - {title}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return path
