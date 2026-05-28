from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.config import Config
from src.netease import NeteaseClient, NeteasePlaylist
from src.navidrome import NavidromeClient, CreatePlaylistResult
from src.matcher import Matcher, MatchStatus
from src.report import SyncSummary, save_report, print_preview
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

        progress.update(t3, description="✅ 匹配完成")
        progress.stop_task(t3)

    # Detect collisions: multiple netease tracks mapped to the same navidrome track
    seen_ids: dict[str, tuple[str, str]] = {}  # track_id -> (title, artist) of first match
    collisions: list[tuple[str, str, str, str]] = []  # (title1, artist1, title2, artist2)
    deduped_ids: list[str] = []
    all_matches = strict_matches + fuzzy_matches
    for (title, artist, result) in all_matches:
        tid = result.track_id
        if tid in seen_ids:
            collisions.append((seen_ids[tid][0], seen_ids[tid][1], title, artist))
        else:
            seen_ids[tid] = (title, artist)
            deduped_ids.append(tid)
    matched_ids = deduped_ids
    total = len(netease_tracks)

    existing_playlists = navi.get_playlists()
    playlist_exists = any(p.get("name") == playlist.name for p in existing_playlists)

    console.print()
    if collisions:
        console.print(f"[yellow]⚠ {len(collisions)} 首网易云歌曲被合并到同一首曲库记录（已去重）：[/yellow]")
        for t1, a1, t2, a2 in collisions:
            console.print(f"  [dim]{a1} - {t1}[/dim]  →  [dim]{a2} - {t2}[/dim]  [red]（后者被丢弃）[/red]")
        console.print()
    print_preview(strict_matches, fuzzy_matches, unmatched_list, playlist.name, console,
                  playlist_exists=playlist_exists, unique_track_count=len(matched_ids))

    if not matched_ids:
        console.print("[yellow]曲库中没有匹配的歌曲，取消创建播放列表。[/yellow]")
        return

    action = "更新" if playlist_exists else "创建"
    confirm = console.input(f"\n确认{action}播放列表「{playlist.name}」？[Y/n] ").strip().lower()
    if confirm == "n":
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
