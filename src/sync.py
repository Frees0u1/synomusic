from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.config import Config
from src.netease import NeteaseClient, NeteasePlaylist
from src.navidrome import NavidromeClient, CreatePlaylistResult
from src.downloader.batch import BatchDownloadContext, print_batch_download_summary, submit_best_downloads
from src.downloader.models import TrackInfo
from src.downloader.selection import TorrentSelector
from src.downloader.settings import DownloaderSettings
from src.downloader.synology import normalize_download_destination
from src.matcher import Matcher, MatchStatus
from src.report import SyncSummary, save_report, print_preview, print_collisions
from src.history import History


def run_sync(
    playlist: NeteasePlaylist,
    netease: NeteaseClient,
    navi: NavidromeClient,
    cfg: Config,
    console: Console,
) -> None:
    try:
        _run_sync_inner(playlist, netease, navi, cfg, console)
    except Exception as e:
        console.print(f"\n[red]同步失败：{e}[/red]")


def _run_sync_inner(
    playlist: NeteasePlaylist,
    netease: NeteaseClient,
    navi: NavidromeClient,
    cfg: Config,
    console: Console,
) -> None:
    history = History(cfg.history_file)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t1 = progress.add_task("正在拉取歌曲列表...", total=None)
        netease_tracks = netease.get_playlist_tracks(playlist.id)
        progress.update(t1, description=f"✅ 已获取 {len(netease_tracks)} 首歌曲")
        progress.stop_task(t1)

        t2 = progress.add_task("正在加载曲库...", total=None)
        library = navi.get_all_tracks()
        matcher = Matcher(
            [{"id": t.id, "title": t.title, "artist": t.artist, "path": t.path} for t in library],
            fuzzy_threshold=cfg.fuzzy_match_threshold,
        )
        progress.update(t2, description=f"✅ 曲库共 {len(library)} 首")
        progress.stop_task(t2)

        t3 = progress.add_task("正在匹配...", total=None)
        strict_matches = []
        fuzzy_matches = []
        unmatched_list = []
        unmatched_tracks: list[TrackInfo] = []
        matched_ids = []

        for track in netease_tracks:
            result = matcher.match(track.title, track.artist)
            if result.status == MatchStatus.STRICT:
                strict_matches.append((track.title, track.artist, result))
                matched_ids.append(result.track_id)
            elif result.status == MatchStatus.FUZZY:
                fuzzy_matches.append((track.title, track.artist, result))
                matched_ids.append(result.track_id)
            else:
                unmatched_list.append((track.title, track.artist))
                unmatched_tracks.append(TrackInfo.from_netease(track))

        progress.update(t3, description="✅ 匹配完成")
        progress.stop_task(t3)

    # Detect collisions: multiple netease tracks mapped to the same navidrome track
    # group: track_id -> {navi info, [netease songs]}
    seen: dict[str, dict] = {}
    deduped_ids: list[str] = []
    all_matches = strict_matches + fuzzy_matches
    for (title, artist, result) in all_matches:
        tid = result.track_id
        if tid not in seen:
            seen[tid] = {
                "navi_title": result.matched_title,
                "navi_artist": result.matched_artist,
                "navi_path": result.matched_path,
                "netease": [],
            }
            deduped_ids.append(tid)
        seen[tid]["netease"].append((title, artist))
    collisions = {tid: info for tid, info in seen.items() if len(info["netease"]) > 1}
    matched_ids = deduped_ids
    total = len(netease_tracks)

    existing_playlists = navi.get_playlists()
    playlist_exists = any(p.get("name") == playlist.name for p in existing_playlists)

    console.print()
    print_preview(strict_matches, fuzzy_matches, unmatched_list, playlist.name, console,
                  playlist_exists=playlist_exists, unique_track_count=len(matched_ids),
                  collisions=collisions)

    if unmatched_tracks and _offer_missing_downloads(
        unmatched_tracks,
        console,
        playlist_name=playlist.name,
        playlist_total=total,
        library_total=len(library),
        strict_count=len(strict_matches),
        fuzzy_count=len(fuzzy_matches),
        unique_matched_count=len(matched_ids),
    ):
        return

    if not matched_ids:
        console.print("[yellow]曲库中没有匹配的歌曲，取消创建播放列表。[/yellow]")
        return

    action = "更新" if playlist_exists else "创建"
    if not _confirm(console, f"\n确认{action}播放列表「{playlist.name}」？", default=True):
        console.print("[yellow]已取消。[/yellow]")
        return

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t4 = progress.add_task(f"正在{action}播放列表...", total=None)
        result: CreatePlaylistResult = navi.create_playlist(playlist.name, matched_ids)
        progress.update(t4, description=f"✅ 播放列表已{action}")
        progress.stop_task(t4)

    action = "[green]新建[/green]" if result.created else "[yellow]更新[/yellow]"
    overall_status = "[red]有失败[/red]" if result.failed_calls else "[green]全部成功[/green]"
    lines = [
        f"歌单「{result.playlist_name}」{action}，共 [bold]{result.track_count}[/bold] 首  {overall_status}",
        "",
    ]
    for s in result.stats:
        failed_tag = f"  [red]{s.failed} 次失败[/red]" if s.failed else ""
        lines.append(
            f"  {s.endpoint:<30} {s.calls} 次  avg {s.avg_ms:.0f}ms  max {s.max_ms:.0f}ms{failed_tag}"
        )
    lines.append(f"\n  总耗时  {result.total_ms:.0f} ms")
    console.print("\n[bold]创建统计[/bold]\n" + "\n".join(lines))

    summary = SyncSummary(
        playlist_name=playlist.name,
        netease_id=playlist.id,
        total=total,
        strict_matches=strict_matches,
        fuzzy_matches=fuzzy_matches,
        unmatched=unmatched_list,
        navidrome_playlist_id=result.playlist_id,
    )
    report_path = save_report(summary, "./data/reports")
    console.print(f"  报告已保存  [dim]{report_path}[/dim]")

    record = History.make_record(
        playlist_name=playlist.name,
        netease_id=playlist.id,
        total=total,
        matched_strict=len(strict_matches),
        matched_fuzzy=len(fuzzy_matches),
        unmatched=len(unmatched_list),
        navidrome_playlist_id=result.playlist_id,
    )
    history.append(record)


def _offer_missing_downloads(
    unmatched_tracks: list[TrackInfo],
    console: Console,
    playlist_name: str,
    playlist_total: int,
    library_total: int,
    strict_count: int,
    fuzzy_count: int,
    unique_matched_count: int,
) -> bool:
    """Return True when this sync run should stop before creating a playlist."""
    console.print(f"\n[yellow]发现 {len(unmatched_tracks)} 首网易云歌曲在曲库中缺失。[/yellow]")
    if not _confirm(console, "是否现在搜索种子并批量提交到 Synology Download Station？", default=True):
        return False

    try:
        settings = DownloaderSettings.from_env()
    except ValueError as exc:
        console.print(f"[red]下载配置错误：{exc}[/red]")
        console.print("[yellow]已选择处理缺失下载，本轮不会创建播放列表。[/yellow]")
        return True

    service = settings.build_service()
    selector = TorrentSelector(
        auto_threshold=settings.auto_threshold,
        min_threshold=settings.min_threshold,
    )
    summary = submit_best_downloads(
        unmatched_tracks,
        service,
        selector,
        console=console,
    )
    print_batch_download_summary(
        console,
        summary,
        BatchDownloadContext(
            playlist_name=playlist_name,
            playlist_total=playlist_total,
            library_total=library_total,
            strict_count=strict_count,
            fuzzy_count=fuzzy_count,
            unmatched_count=len(unmatched_tracks),
            unique_matched_count=unique_matched_count,
            destination=normalize_download_destination(settings.destination),
        ),
    )

    if summary.submitted_count > 0:
        console.print(
            "\n[yellow]已添加下载任务。请等待 Synology 下载完成，并让 Navidrome 扫描新文件后，"
            "重新导入这个网易云歌单。[/yellow]"
        )
        return True

    console.print(
        "\n[yellow]没有添加任何下载任务；由于已选择处理缺失下载，本轮不会创建播放列表。"
        "如需只创建当前已匹配歌曲的播放列表，请重新运行并在下载提示处选择否。[/yellow]"
    )
    return True


def _confirm(console: Console, prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = console.input(f"{prompt}{suffix} ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        console.print("[red]请输入 Y/N、yes/no，或直接回车。[/red]")
