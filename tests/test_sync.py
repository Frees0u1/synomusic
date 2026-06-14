from io import StringIO

from rich.console import Console

import src.sync as sync
from src.downloader.batch import BatchDownloadSummary, SkippedDownload
from src.downloader.models import TrackInfo


class FakeConsole(Console):
    def __init__(self, *answers: str):
        super().__init__(file=StringIO(), width=160, highlight=False, markup=False)
        self.answers = list(answers)

    def input(self, prompt: str = "", **kwargs) -> str:
        self.print(prompt, end="")
        return self.answers.pop(0)


class FakeDownloaderSettings:
    auto_threshold = 88
    min_threshold = 70
    destination = "music/lib"

    def build_service(self):
        return object()


def test_offer_missing_downloads_stops_sync_even_when_no_tasks_submitted(monkeypatch):
    summary = BatchDownloadSummary(
        total=1,
        submitted=[],
        skipped=[
            SkippedDownload(
                track=TrackInfo(title="missing", artist="artist"),
                reason="无候选",
            )
        ],
        failed=[],
    )

    monkeypatch.setattr(sync.DownloaderSettings, "from_env", lambda: FakeDownloaderSettings())
    monkeypatch.setattr(sync, "submit_best_downloads", lambda *args, **kwargs: summary)

    should_stop = sync._offer_missing_downloads(
        [TrackInfo(title="missing", artist="artist")],
        FakeConsole("y"),
        playlist_name="Playlist",
        playlist_total=2,
        library_total=10,
        strict_count=1,
        fuzzy_count=0,
        unique_matched_count=1,
    )

    assert should_stop is True


def test_confirm_supports_case_insensitive_yes_no_and_default_enter():
    assert sync._confirm(FakeConsole("YES"), "确认？", default=True) is True
    assert sync._confirm(FakeConsole("NO"), "确认？", default=True) is False
    assert sync._confirm(FakeConsole(""), "确认？", default=True) is True


def test_offer_missing_downloads_prompt_defaults_to_yes(monkeypatch):
    summary = BatchDownloadSummary(total=0, submitted=[], skipped=[], failed=[])
    console = FakeConsole("")

    monkeypatch.setattr(sync.DownloaderSettings, "from_env", lambda: FakeDownloaderSettings())
    monkeypatch.setattr(sync, "submit_best_downloads", lambda *args, **kwargs: summary)

    should_stop = sync._offer_missing_downloads(
        [],
        console,
        playlist_name="Playlist",
        playlist_total=0,
        library_total=0,
        strict_count=0,
        fuzzy_count=0,
        unique_matched_count=0,
    )

    assert should_stop is True
    assert "[Y/n]" in console.file.getvalue()
