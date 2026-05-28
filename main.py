import glob
import os
import re
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from src.config import Config
from src.netease import NeteaseClient, NeteasePlaylist, PlaylistPage
from src.navidrome import NavidromeClient
from src.sync import run_sync
from src.history import History

console = Console()

HOT_PLAYLIST_CATS = [
    ("全部",   "全部"),
    ("华语",   "华语"),
    ("流行",   "流行"),
    ("摇滚",   "摇滚"),
    ("民谣",   "民谣"),
    ("电子",   "电子"),
    ("说唱",   "说唱"),
    ("轻音乐", "轻音乐"),
    ("影视原声", "影视原声"),
    ("ACG",   "ACG"),
    ("欧美",   "欧美"),
    ("日语",   "日语"),
    ("韩语",   "韩语"),
]


def print_menu() -> None:
    console.print(Panel(
        "  [bold cyan]1.[/bold cyan] 浏览编辑精选歌单\n"
        "  [bold cyan]2.[/bold cyan] 浏览热门歌单（按分类）\n"
        "  [bold cyan]3.[/bold cyan] 通过歌单 ID / URL 同步\n"
        "  [bold cyan]4.[/bold cyan] 查看历史同步记录\n"
        "  [bold cyan]5.[/bold cyan] 清除本地缓存\n"
        "  [bold cyan]6.[/bold cyan] 退出",
        title="[bold]SynoMusic[/bold]  Netease → Navidrome 同步",
        expand=False,
    ))


def _playlist_browse_loop(
    netease: NeteaseClient,
    navi: NavidromeClient,
    cfg: Config,
    fetch_page,
    status_text: str,
) -> None:
    pages: list[list[NeteasePlaylist]] = []
    current_page = 0
    has_more = True
    cursor = None

    def _load_next_page() -> bool:
        nonlocal has_more, cursor
        with console.status(status_text):
            try:
                page: PlaylistPage = fetch_page(cursor)
            except Exception as e:
                console.print(f"[red]获取歌单失败：{e}[/red]")
                return False
        pages.append(page.playlists)
        has_more = page.has_more
        cursor = page.next_before
        return True

    if not _load_next_page():
        return

    while True:
        playlists = pages[current_page]
        table = Table(box=box.ROUNDED)
        table.add_column("#", justify="right", style="dim")
        table.add_column("歌单名称")
        table.add_column("歌曲数", justify="right")
        table.add_column("播放量", justify="right")
        for i, p in enumerate(playlists, 1):
            table.add_row(str(i), p.name, str(p.track_count), f"{p.play_count:,}")
        console.print(table)

        nav_parts = []
        if has_more or current_page < len(pages) - 1:
            nav_parts.append("[cyan]n[/cyan] 下一页")
        if current_page > 0:
            nav_parts.append("[cyan]p[/cyan] 上一页")
        nav_parts.append("[cyan]编号[/cyan] 选择")
        nav_parts.append("[dim]回车 返回[/dim]")
        console.print("  ".join(nav_parts) + f"  （第 {current_page + 1} 页，共已加载 {len(pages)} 页）")

        choice = console.input("").strip().lower()

        if choice == "":
            return
        elif choice == "n":
            if current_page < len(pages) - 1:
                current_page += 1
            elif has_more:
                if _load_next_page():
                    current_page += 1
            else:
                console.print("[yellow]已是最后一页。[/yellow]")
        elif choice == "p":
            if current_page > 0:
                current_page -= 1
            else:
                console.print("[yellow]已是第一页。[/yellow]")
        else:
            try:
                idx = int(choice) - 1
                if not (0 <= idx < len(playlists)):
                    raise ValueError
            except ValueError:
                console.print("[red]无效输入。[/red]")
                continue
            run_sync(playlists[idx], netease, navi, cfg, console)


def menu_top_playlists(netease: NeteaseClient, navi: NavidromeClient, cfg: Config) -> None:
    _playlist_browse_loop(
        netease, navi, cfg,
        fetch_page=lambda cursor: netease.get_top_playlists(cfg.top_playlist_limit, before=cursor),
        status_text="正在获取编辑精选歌单...",
    )


def menu_hot_playlists(netease: NeteaseClient, navi: NavidromeClient, cfg: Config) -> None:
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("编号", justify="right", style="cyan")
    table.add_column("分类")
    for i, (label, _) in enumerate(HOT_PLAYLIST_CATS, 1):
        table.add_row(str(i), label)
    console.print(table)

    raw = console.input("选择分类（回车默认全部）：").strip()
    if not raw:
        cat_key = "全部"
        cat_label = "全部"
    else:
        try:
            idx = int(raw) - 1
            if not (0 <= idx < len(HOT_PLAYLIST_CATS)):
                raise ValueError
            cat_label, cat_key = HOT_PLAYLIST_CATS[idx]
        except ValueError:
            console.print("[red]无效选择。[/red]")
            return

    _playlist_browse_loop(
        netease, navi, cfg,
        fetch_page=lambda cursor: netease.get_hot_playlists(
            cfg.top_playlist_limit, cat=cat_key, offset=cursor or 0
        ),
        status_text=f"正在获取热门歌单（{cat_label}）...",
    )


def menu_by_id(netease: NeteaseClient, navi: NavidromeClient, cfg: Config) -> None:
    raw = console.input("请输入歌单 ID 或 URL：").strip()
    match = re.search(r"(\d{6,})", raw)
    if not match:
        console.print("[red]无法识别歌单 ID。[/red]")
        return
    playlist_id = match.group(1)
    with console.status("正在获取歌单信息..."):
        try:
            playlist = netease.get_playlist_by_id(playlist_id)
        except Exception as e:
            console.print(f"[red]获取歌单失败：{e}[/red]")
            return
    console.print(f"歌单：[bold]{playlist.name}[/bold]（{playlist.track_count} 首）")
    run_sync(playlist, netease, navi, cfg, console)


def menu_clear_cache(cfg: Config) -> None:
    history_file = cfg.history_file
    reports = glob.glob("./data/reports/*.txt")

    if not os.path.exists(history_file) and not reports:
        console.print("[dim]无本地缓存。[/dim]")
        return

    lines = []
    if os.path.exists(history_file):
        lines.append(f"  · 同步历史  {history_file}")
    if reports:
        lines.append(f"  · 同步报告  {len(reports)} 个文件（./data/reports/）")
    console.print("\n".join(lines))

    confirm = console.input("\n确认清除以上全部？[y/N] ").strip().lower()
    if confirm != "y":
        console.print("[dim]已取消。[/dim]")
        return

    if os.path.exists(history_file):
        os.remove(history_file)
        console.print(f"[green]✓[/green] 已删除 {history_file}")
    for path in reports:
        os.remove(path)
    if reports:
        console.print(f"[green]✓[/green] 已删除 {len(reports)} 个报告文件")


def menu_history(cfg: Config) -> None:
    history = History(cfg.history_file)
    records = sorted(history.all(), key=lambda r: r.synced_at, reverse=True)
    if not records:
        console.print("[yellow]暂无同步记录。[/yellow]")
        return

    table = Table(box=box.SIMPLE)
    table.add_column("时间")
    table.add_column("歌单名称")
    table.add_column("总数", justify="right")
    table.add_column("严格", justify="right", style="green")
    table.add_column("模糊", justify="right", style="yellow")
    table.add_column("缺失", justify="right", style="red")
    for r in records:
        table.add_row(
            r.synced_at,
            r.playlist_name,
            str(r.total),
            str(r.matched_strict),
            str(r.matched_fuzzy),
            str(r.unmatched),
        )
    console.print(table)


def main() -> None:
    try:
        cfg = Config()
    except ValueError as e:
        console.print(f"[red]配置错误：{e}[/red]")
        console.print("请复制 .env.example 为 .env 并填写配置。")
        sys.exit(1)

    netease = NeteaseClient(cfg.netease_api_url)
    navi = NavidromeClient(cfg.navidrome_url, cfg.navidrome_user, cfg.navidrome_password)

    console.print()
    with console.status("检查服务连接..."):
        navi_ok = navi.ping()
    if not navi_ok:
        console.print(f"[red]无法连接到 Navidrome：{cfg.navidrome_url}[/red]")
        sys.exit(1)
    console.print(f"[green]✅ Navidrome 连接正常[/green]")

    while True:
        console.print()
        print_menu()
        choice = console.input("\n请选择（1-6）：").strip()

        if choice == "1":
            menu_top_playlists(netease, navi, cfg)
        elif choice == "2":
            menu_hot_playlists(netease, navi, cfg)
        elif choice == "3":
            menu_by_id(netease, navi, cfg)
        elif choice == "4":
            menu_history(cfg)
        elif choice == "5":
            menu_clear_cache(cfg)
        elif choice == "6":
            console.print("再见！")
            break
        else:
            console.print("[red]无效选项，请输入 1-6。[/red]")


if __name__ == "__main__":
    main()
