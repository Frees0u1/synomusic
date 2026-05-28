from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.config import Config
from src.netease import NeteaseClient, NeteasePlaylist
from src.navidrome import NavidromeClient
from src.matcher import Matcher, MatchStatus
from src.report import SyncSummary, print_report, save_report, print_preview
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

    matched_ids = list(dict.fromkeys(matched_ids))

    console.print()
    print_preview(strict_matches, fuzzy_matches, unmatched_list, playlist.name, console)

    if not matched_ids:
        console.print("[yellow]曲库中没有匹配的歌曲，取消创建播放列表。[/yellow]")
        return

    confirm = console.input(f"\n确认创建播放列表「{playlist.name}」？[Y/n] ").strip().lower()
    if confirm == "n":
        console.print("[yellow]已取消。[/yellow]")
        return

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t4 = progress.add_task("正在创建播放列表...", total=None)
        playlist_id = navi.create_playlist(playlist.name, matched_ids)
        progress.update(t4, description="✅ 播放列表已创建")
        progress.stop_task(t4)

    summary = SyncSummary(
        playlist_name=playlist.name,
        netease_id=playlist.id,
        total=total,
        strict_matches=strict_matches,
        fuzzy_matches=fuzzy_matches,
        unmatched=unmatched_list,
        navidrome_playlist_id=playlist_id,
    )

    console.print()
    print_report(summary, console)
    report_path = save_report(summary, "./data/reports")
    console.print(f"\n报告已保存：[dim]{report_path}[/dim]")

    record = History.make_record(
        playlist_name=playlist.name,
        netease_id=playlist.id,
        total=total,
        matched_strict=len(strict_matches),
        matched_fuzzy=len(fuzzy_matches),
        unmatched=len(unmatched_list),
        navidrome_playlist_id=playlist_id,
    )
    history.append(record)
