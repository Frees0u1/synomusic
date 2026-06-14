from io import StringIO
import requests

import pytest
from rich.console import Console

from src.downloader.batch import (
    BatchDownloadContext,
    BatchDownloadSummary,
    FailedDownload,
    SkippedDownload,
    SubmittedDownload,
    print_batch_download_summary,
    submit_best_downloads,
)
from src.downloader.models import DownloadTask, TorrentSearchResult, TrackInfo
from src.downloader.diagnostics import _selection_preview
from src.downloader.musicbrainz import MusicBrainzResolution, MusicBrainzResolver
from src.downloader.providers import DICMusicProvider, PirateBayProvider, _extract_login_failure
from src.downloader.scoring import build_search_queries, score_torrent_title
from src.downloader.selection import TorrentSelector
from src.downloader.service import TorrentDownloadService
from src.downloader.settings import _parse_query_aliases
from src.downloader.synology import _api_error_message, normalize_download_destination, progress_from_task


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200, headers=None):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return FakeResponse(self.payload)


class FlakySession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if len(self.calls) == 1:
            raise requests.Timeout("temporary timeout")
        return FakeResponse(self.payload)


class RateLimitedSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return FakeResponse(status_code=429, headers={"Retry-After": "60"})


class FakeDICSearchSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return FakeResponse(text="<table></table>")


class FakeMusicBrainzSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        path = url.rsplit("/ws/2/", 1)[-1]
        self.calls.append((path, params or {}))
        if path == "artist":
            return FakeResponse({
                "artists": [{
                    "id": "artist-1",
                    "score": 100,
                    "name": "周杰倫",
                    "aliases": [
                        {"name": "周杰伦", "locale": "zh_Hans", "type": "Artist name"},
                        {"name": "Jay Chou", "locale": "en", "type": "Artist name"},
                    ],
                }]
            })
        if path == "release-group":
            return FakeResponse({
                "release-groups": [{
                    "id": "rg-1",
                    "score": 100,
                    "title": "魔杰座",
                    "primary-type": "Album",
                }]
            })
        if path == "release-group/rg-1":
            return FakeResponse({
                "id": "rg-1",
                "title": "魔杰座",
                "aliases": [{"name": "Capricorn", "locale": "en"}],
                "annotation": "Official English title: Capricorn",
                "releases": [
                    {"title": "魔杰座", "status": "Official"},
                    {"title": "Capricorn", "status": "Official"},
                ],
            })
        if path == "recording":
            return FakeResponse({
                "recordings": [{
                    "id": "rec-1",
                    "score": 100,
                    "title": "稻香",
                    "aliases": [],
                    "releases": [],
                }]
            })
        raise AssertionError(path)


class FakeTorrentProvider:
    def __init__(self, name, results):
        self.name = name
        self.results = results
        self.search_calls = []
        self.resolved = []

    def search(self, track, limit=20, aliases=None):
        self.search_calls.append((track, limit, aliases))
        return self.results[:limit]

    def resolve(self, result):
        self.resolved.append(result)
        return TorrentSearchResult(
            source=result.source,
            title=result.title,
            url=f"resolved:{result.url}",
            score=result.score,
            seeders=result.seeders,
        )


class FakeDownloadClient:
    def __init__(self):
        self.created = []

    def create_task(self, uri, destination):
        self.created.append((uri, destination))
        return DownloadTask(id="task-1", uri=uri, destination=destination)


class CountingResolver:
    def __init__(self, aliases=None):
        self.aliases = aliases or {"周杰伦": ["Jay Chou"]}
        self.calls = []

    def resolve(self, track):
        self.calls.append(track)
        return MusicBrainzResolution(aliases=self.aliases)


def test_build_search_queries_prefers_artist_title():
    track = TrackInfo(title="The Bends", artist="Radiohead", album="The Bends")
    assert build_search_queries(track)[0] == "Radiohead The Bends"


def test_track_search_label_includes_album():
    track = TrackInfo(title="寻找山神", artist="五五身", album="再一次雄性浪漫")
    assert track.search_label == "五五身 - 寻找山神 [再一次雄性浪漫]"


def test_build_search_queries_with_aliases():
    track = TrackInfo(title="稻香", artist="周杰伦", album="魔杰座")
    queries = build_search_queries(track, aliases={"周杰伦": ["Jay Chou"], "魔杰座": ["Capricorn"]})
    assert "Jay Chou Capricorn" in queries
    assert "Jay Chou Capricorn 稻香" in queries


def test_build_search_queries_adds_apostrophe_variants():
    queries = build_search_queries(
        TrackInfo(title="Think of You", artist="Chris Young", album="I'm Comin' Over")
    )
    assert "Chris Young I’m Comin’ Over" in queries


def test_build_search_queries_adds_title_and_album_fallbacks_without_redundant_album_title():
    queries = build_search_queries(
        TrackInfo(title="Alonica", artist="LANY", album="Alonica")
    )
    assert "Alonica" in queries
    assert "LANY Alonica Alonica" not in queries

    album_queries = build_search_queries(
        TrackInfo(title="Whatcha Reckon", artist="Josh Turner", album="Punching Bag (Deluxe Edition)")
    )
    assert "Punching Bag (Deluxe Edition)" in album_queries


def test_build_search_queries_adds_terminal_dot_and_slash_variants():
    queries = build_search_queries(
        TrackInfo(title="song4love.", artist="8bite", album="song4love/me")
    )
    assert "8bite song4love" in queries
    assert "song4love me" in queries
    assert "song4love" in queries


def test_score_torrent_title_matches_artist_and_title():
    track = TrackInfo(title="The Bends", artist="Radiohead", album="The Bends")
    assert score_torrent_title(track, "Radiohead - The Bends (1995) FLAC 88", seeders=300) >= 85
    assert score_torrent_title(track, "Taylor Swift - 1989 FLAC", seeders=300) < 60


def test_score_prefers_album_match_over_unrequested_remix():
    track = TrackInfo(title="稻香", artist="周杰伦", album="魔杰座")
    remix = score_torrent_title(
        track,
        "周杰伦 (Jay Chou) - 稻香 (Remix摇滚版) [FLAC / 24bit Lossless]",
        seeders=7,
        size_bytes=34_057_748,
        release_kind="重混音",
    )
    album = score_torrent_title(
        track,
        "周杰伦 (Jay Chou) - 魔杰座 (Capricorn) [FLAC / Lossless / Log (100%) / Cue]",
        seeders=155,
        size_bytes=312_737_792,
        release_kind="专辑",
    )
    assert album > remix


def test_score_prefers_lossless_single_under_200mb():
    track = TrackInfo(title="稻香", artist="周杰伦", album="魔杰座")
    compact_lossless = score_torrent_title(
        track,
        "周杰伦 - 稻香 [FLAC / Lossless]",
        seeders=10,
        size_bytes=48 * 1024 * 1024,
        release_kind="单曲",
    )
    oversized_single = score_torrent_title(
        track,
        "周杰伦 - 稻香 [FLAC / Lossless]",
        seeders=10,
        size_bytes=420 * 1024 * 1024,
        release_kind="单曲",
    )
    assert compact_lossless > oversized_single


def test_score_allows_album_candidate_containing_track():
    track = TrackInfo(title="Whatcha Reckon", artist="Josh Turner", album="Punching Bag (Deluxe Edition)")
    album = score_torrent_title(
        track,
        "Josh Turner - Punching Bag (Deluxe Edition) [FLAC / Lossless]",
        seeders=5,
        size_bytes=430 * 1024 * 1024,
        release_kind="专辑",
    )
    assert album >= 80


def test_score_caps_same_artist_wrong_title_and_album():
    track = TrackInfo(title="When She's Gone", artist="Josh Fudge", album="When She's Gone")
    wrong_song = score_torrent_title(
        track,
        "Josh Fudge - There She Goes [FLAC / Lossless]",
        seeders=5,
        size_bytes=44 * 1024 * 1024,
        release_kind="单曲",
    )
    assert wrong_song < 70


def test_score_uses_aliases_for_english_index_titles():
    track = TrackInfo(title="稻香", artist="周杰伦", album="魔杰座")
    score = score_torrent_title(
        track,
        "Jay Chou - Capricorn",
        seeders=1,
        size_bytes=107_050_916,
        aliases={"周杰伦": ["Jay Chou"], "魔杰座": ["Capricorn"]},
    )
    assert score >= 80


def test_parse_query_aliases():
    assert _parse_query_aliases("周杰伦=Jay Chou;魔杰座=Capricorn|Capricorn Album") == {
        "周杰伦": ["Jay Chou"],
        "魔杰座": ["Capricorn", "Capricorn Album"],
    }


def test_musicbrainz_resolver_builds_aliases_from_artist_and_release_group():
    resolver = MusicBrainzResolver(
        session=FakeMusicBrainzSession(),
        rate_limit_seconds=0,
    )
    resolution = resolver.resolve(TrackInfo(title="稻香", artist="周杰伦", album="魔杰座"))
    assert resolution.aliases["周杰伦"] == ["周杰倫", "Jay Chou"]
    assert resolution.aliases["魔杰座"] == ["Capricorn"]
    assert "稻香" not in resolution.aliases
    assert resolution.notes == ["artist:周杰倫", "release-group:魔杰座", "recording:稻香"]


def test_service_passes_manual_and_musicbrainz_aliases_to_provider():
    class CapturingProvider:
        name = "capture"

        def __init__(self):
            self.aliases = None

        def search(self, track, limit=20, aliases=None):
            self.aliases = aliases
            return []

        def resolve(self, result):
            return result

    class FakeResolver:
        def resolve(self, track):
            return MusicBrainzResolution(aliases={"周杰伦": ["Jay Chou"]})

    from src.downloader.service import TorrentDownloadService

    provider = CapturingProvider()
    service = TorrentDownloadService(
        providers=[provider],
        download_client=object(),
        query_aliases={"魔杰座": ["Capricorn"]},
        metadata_resolver=FakeResolver(),
    )
    service.search(TrackInfo(title="稻香", artist="周杰伦", album="魔杰座"))
    assert provider.aliases == {
        "魔杰座": ["Capricorn"],
        "周杰伦": ["Jay Chou"],
    }


def test_service_does_not_resolve_aliases_for_non_cjk_track():
    provider = FakeTorrentProvider(
        "piratebay",
        [TorrentSearchResult(source="piratebay", title="pb", url="magnet:pb", score=90)],
    )
    resolver = CountingResolver()
    service = TorrentDownloadService(
        providers=[provider],
        download_client=object(),
        metadata_resolver=resolver,
    )
    service.search(TrackInfo(title="Stay Alive", artist="José González"))
    assert resolver.calls == []
    assert provider.search_calls[0][2] == {}


def test_service_uses_manual_aliases_for_non_cjk_without_musicbrainz():
    provider = FakeTorrentProvider(
        "piratebay",
        [TorrentSearchResult(source="piratebay", title="Dixie Chicks - Fly", url="magnet:pb", score=90)],
    )
    resolver = CountingResolver()
    service = TorrentDownloadService(
        providers=[provider],
        download_client=object(),
        query_aliases={"The Chicks": ["Dixie Chicks"]},
        metadata_resolver=resolver,
    )
    service.search(TrackInfo(title="Ready to Run", artist="The Chicks", album="Fly"))
    assert resolver.calls == []
    assert provider.search_calls[0][2] == {"The Chicks": ["Dixie Chicks"]}



def test_service_does_not_resolve_aliases_when_dicmusic_finds_cjk_track():
    dic = FakeTorrentProvider(
        "dicmusic",
        [TorrentSearchResult(source="dicmusic", title="dic", url="https://dic", score=90)],
    )
    piratebay = FakeTorrentProvider(
        "piratebay",
        [TorrentSearchResult(source="piratebay", title="pb", url="magnet:pb", score=80)],
    )
    resolver = CountingResolver()
    service = TorrentDownloadService(
        providers=[dic, piratebay],
        download_client=object(),
        metadata_resolver=resolver,
    )
    service.search(TrackInfo(title="稻香", artist="周杰伦", album="魔杰座"))
    assert resolver.calls == []
    assert dic.search_calls == [(TrackInfo(title="稻香", artist="周杰伦", album="魔杰座"), 10, None)]
    assert piratebay.search_calls[0][2] == {}


def test_service_resolves_aliases_only_after_dicmusic_misses_cjk_track():
    dic = FakeTorrentProvider("dicmusic", [])
    piratebay = FakeTorrentProvider(
        "piratebay",
        [TorrentSearchResult(source="piratebay", title="Jay Chou - Capricorn", url="magnet:pb", score=90)],
    )
    resolver = CountingResolver(aliases={"周杰伦": ["Jay Chou"], "魔杰座": ["Capricorn"]})
    track = TrackInfo(title="稻香", artist="周杰伦", album="魔杰座")
    service = TorrentDownloadService(
        providers=[dic, piratebay],
        download_client=object(),
        metadata_resolver=resolver,
    )
    service.search(track)
    assert resolver.calls == [track]
    assert dic.search_calls == [
        (track, 10, None),
        (track, 10, {"周杰伦": ["Jay Chou"], "魔杰座": ["Capricorn"]}),
    ]
    assert piratebay.search_calls[0][2] == {"周杰伦": ["Jay Chou"], "魔杰座": ["Capricorn"]}


def test_service_search_by_provider_keeps_provider_buckets():
    dic = FakeTorrentProvider(
        "dicmusic",
        [TorrentSearchResult(source="dicmusic", title="dic", url="https://dic", score=80)],
    )
    piratebay = FakeTorrentProvider(
        "piratebay",
        [TorrentSearchResult(source="piratebay", title="pb", url="magnet:pb", score=90)],
    )
    service = TorrentDownloadService(
        providers=[dic, piratebay],
        download_client=object(),
    )
    result_sets = service.search_by_provider(TrackInfo(title="南山南"), limit_per_provider=5)
    assert list(result_sets) == ["dicmusic", "piratebay"]
    assert result_sets["dicmusic"][0].title == "dic"
    assert result_sets["piratebay"][0].title == "pb"


def test_service_submit_result_resolves_then_creates_synology_task():
    provider = FakeTorrentProvider(
        "dicmusic",
        [TorrentSearchResult(source="dicmusic", title="dic", url="https://dic", score=80)],
    )
    client = FakeDownloadClient()
    service = TorrentDownloadService(
        providers=[provider],
        download_client=client,
        destination="/volumes/music/lib",
    )
    task = service.submit_result(provider.results[0], wait=False)
    assert task.id == "task-1"
    assert provider.resolved == [provider.results[0]]
    assert client.created == [("resolved:https://dic", "/volumes/music/lib")]


def test_batch_submit_best_downloads_uses_highest_scored_candidate():
    dic = FakeTorrentProvider(
        "dicmusic",
        [TorrentSearchResult(source="dicmusic", title="dic", url="https://dic", score=80)],
    )
    piratebay = FakeTorrentProvider(
        "piratebay",
        [TorrentSearchResult(source="piratebay", title="pb", url="magnet:pb", score=95)],
    )
    client = FakeDownloadClient()
    service = TorrentDownloadService(
        providers=[dic, piratebay],
        download_client=client,
        destination="music/lib",
    )
    summary = submit_best_downloads(
        [TrackInfo(title="The Saltwater Room", artist="Owl City")],
        service,
        TorrentSelector(auto_threshold=88, min_threshold=70),
    )
    assert summary.submitted_count == 1
    assert summary.submitted[0].result.source == "piratebay"
    assert client.created == [("resolved:magnet:pb", "music/lib")]


def test_batch_submit_best_downloads_skips_low_score_candidate():
    provider = FakeTorrentProvider(
        "dicmusic",
        [TorrentSearchResult(source="dicmusic", title="weak", url="https://dic", score=40)],
    )
    client = FakeDownloadClient()
    service = TorrentDownloadService(
        providers=[provider],
        download_client=client,
    )
    summary = submit_best_downloads(
        [TrackInfo(title="missing", artist="artist")],
        service,
        TorrentSelector(auto_threshold=88, min_threshold=70),
    )
    assert summary.submitted_count == 0
    assert summary.skipped_count == 1
    assert summary.skipped[0].reason == "最高匹配度低于阈值"
    assert client.created == []


def test_batch_summary_renders_ascii_statistics_and_lists():
    console = Console(file=StringIO(), width=180, highlight=False, markup=False)
    summary = BatchDownloadSummary(
        total=3,
        submitted=[
            SubmittedDownload(
                track=TrackInfo(title="The Saltwater Room", artist="Owl City"),
                result=TorrentSearchResult(
                    source="piratebay",
                    title="Owl City - Ocean Eyes (2009) Flac",
                    url="magnet:pb",
                    score=96,
                    seeders=13,
                    size_bytes=325_071_672,
                ),
                task=DownloadTask(id="task-1", uri="magnet:pb", destination="music/lib"),
            )
        ],
        skipped=[
            SkippedDownload(
                track=TrackInfo(title="weak", artist="artist"),
                reason="最高匹配度低于阈值",
                best_score=40,
                best_title="weak candidate",
            )
        ],
        failed=[
            FailedDownload(
                track=TrackInfo(title="failed", artist="artist"),
                error="Synology API error 403: destination does not exist",
            )
        ],
    )
    print_batch_download_summary(
        console,
        summary,
        BatchDownloadContext(
            playlist_name="Test Playlist",
            playlist_total=10,
            library_total=1234,
            strict_count=4,
            fuzzy_count=3,
            unmatched_count=3,
            unique_matched_count=7,
            destination="music/lib",
        ),
    )
    output = console.file.getvalue()
    assert "+-" in output
    assert "Test Playlist" in output
    assert "Netease tracks" in output
    assert "Navidrome tracks" in output
    assert "Estimated size" in output
    assert "0.30 GB" in output
    assert "Skipped Tracks" in output
    assert "Failed Tracks" in output
    assert "Synology API error 403" in output


def test_piratebay_provider_filters_audio_music_and_builds_magnet():
    payload = [
        {
            "id": "1",
            "name": "Radiohead - The Bends (1995) FLAC",
            "info_hash": "abc123",
            "seeders": "10",
            "leechers": "2",
            "size": "123",
            "category": "104",
        },
        {
            "id": "2",
            "name": "Radiohead documentary",
            "info_hash": "def456",
            "seeders": "100",
            "leechers": "2",
            "size": "123",
            "category": "200",
        },
    ]
    provider = PirateBayProvider(session=FakeSession(payload))
    results = provider.search(TrackInfo(title="The Bends", artist="Radiohead"), limit=5)
    assert len(results) == 1
    assert results[0].url.startswith("magnet:?xt=urn:btih:ABC123")
    assert results[0].seeders == 10


def test_piratebay_provider_retries_temporary_query_failure():
    payload = [{
        "id": "1",
        "name": "Chris Young - I'm Comin' Over [2015] [MP3-VBR]",
        "info_hash": "abc123",
        "seeders": "2",
        "leechers": "1",
        "size": "70803222",
        "category": "101",
    }]
    session = FlakySession(payload)
    provider = PirateBayProvider(session=session, retries=2)
    results = provider.search(TrackInfo(title="Think of You", artist="Chris Young", album="I'm Comin' Over"))
    assert len(results) == 1
    assert len(session.calls) >= 2


def test_piratebay_provider_cools_down_on_rate_limit_without_retrying():
    session = RateLimitedSession()
    provider = PirateBayProvider(session=session, retries=3, rate_limit_cooldown_seconds=180)
    track = TrackInfo(title="Love Is A Long Road", artist="Tom Petty")
    with pytest.raises(RuntimeError, match="rate limited"):
        provider.search(track)

    assert len(session.calls) == 1
    assert provider.search(track) == []
    assert len(session.calls) == 1


def test_selector_auto_selects_clear_best_match():
    selector = TorrentSelector(auto_threshold=88, min_threshold=70)
    result = selector.select(
        TrackInfo(title="The Bends", artist="Radiohead"),
        [
            TorrentSearchResult(source="piratebay", title="best", url="magnet:a", score=92, seeders=10),
            TorrentSearchResult(source="piratebay", title="second", url="magnet:b", score=80, seeders=100),
        ],
        interactive=False,
    )
    assert result is not None
    assert result.title == "best"


def test_diagnostics_selection_preview_marks_noninteractive_fallback():
    results = [
        TorrentSearchResult(source="dicmusic", title="best", url="https://example.test/a", score=100, seeders=10),
        TorrentSearchResult(source="dicmusic", title="second", url="https://example.test/b", score=98, seeders=20),
    ]
    selected = TorrentSelector(auto_threshold=88, min_threshold=70).select(
        TrackInfo(title="南山南", artist="马頔"),
        results,
        interactive=False,
    )
    preview = _selection_preview(results, selected, auto_threshold=88, min_threshold=70)
    assert "non-interactive fallback" in preview
    assert "best" in preview


def test_dicmusic_parser_extracts_downloadable_row():
    provider = DICMusicProvider(cookie="session=abc")
    html = """
    <table>
      <tr>
        <td><a href="torrents.php?id=42">Radiohead - The Bends [FLAC]</a></td>
        <td><a href="torrents.php?action=download&id=99">下载</a></td>
        <td>10</td><td>2</td>
      </tr>
    </table>
    """
    results = provider._parse_results(html, TrackInfo(title="The Bends", artist="Radiohead"))
    assert len(results) == 1
    assert results[0].title == "Radiohead - The Bends [FLAC]"
    assert results[0].url == "https://dicmusic.com/torrents.php?action=download&id=99"


def test_dicmusic_search_uses_alias_queries():
    session = FakeDICSearchSession()
    provider = DICMusicProvider(cookie="session=abc", session=session)
    provider.search(
        TrackInfo(title="稻香", artist="周杰伦", album="魔杰座"),
        aliases={"周杰伦": ["Jay Chou"], "魔杰座": ["Capricorn"]},
    )
    queries = [call[1]["searchstr"] for call in session.calls]
    assert "Jay Chou Capricorn" in queries


def test_dicmusic_parser_uses_group_title_for_format_rows():
    provider = DICMusicProvider(cookie="session=abc")
    html = """
    <table>
      <tr>
        <td><a href="artist.php?id=108">周杰伦 (Jay Chou)</a></td>
        <td><a href="torrents.php?id=4850">魔杰座 (Capricorn)</a></td>
      </tr>
      <tr>
        <td><a href="torrents.php?action=download&id=15756">DL</a></td>
        <td><a href="torrents.php?id=4850&torrentid=15756">FLAC / Lossless</a></td>
        <td>299.39 MB</td><td>77</td><td>42</td><td>0</td>
      </tr>
    </table>
    """
    results = provider._parse_results(
        html,
        TrackInfo(title="稻香", artist="周杰伦", album="魔杰座"),
    )
    assert results[0].title == "周杰伦 (Jay Chou) - 魔杰座 (Capricorn)"
    assert results[1].title == "周杰伦 (Jay Chou) - 魔杰座 (Capricorn) [FLAC / Lossless]"
    assert results[1].seeders == 42
    assert results[1].size_bytes == 313933168
    assert results[1].score >= 80


def test_synology_progress_from_task():
    progress = progress_from_task({
        "id": "dbid_1",
        "title": "Radiohead - The Bends",
        "status": "downloading",
        "size": "1000",
        "additional": {
            "transfer": {
                "size_downloaded": "250",
                "speed_download": "50",
            }
        },
    })
    assert progress.task_id == "dbid_1"
    assert progress.percent == 25
    assert progress.download_speed == 50


def test_synology_normalizes_absolute_destination_path():
    assert normalize_download_destination("/volume1/music/lib") == "music/lib"
    assert normalize_download_destination("/Volumes/music/lib") == "music/lib"
    assert normalize_download_destination("music/lib") == "music/lib"


def test_synology_task_create_error_message_for_destination_missing():
    assert (
        _api_error_message("SYNO.DownloadStation.Task", "create", 403)
        == " (destination does not exist)"
    )


def test_extract_dicmusic_login_failure_message():
    html = """
    <form id="loginform">
      <span>你还剩余 <span class="info">2</span> 次登录尝试机会。</span>
      <span>警告：超过登录次数限制将被限制 6 小时。</span>
    </form>
    """
    assert "2" in _extract_login_failure(html)
    assert "6 小时" in _extract_login_failure(html)
