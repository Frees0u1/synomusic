from dataclasses import dataclass, field, replace
from typing import Any, Optional


@dataclass(frozen=True)
class TrackInfo:
    id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0

    @classmethod
    def from_netease(cls, track: Any) -> "TrackInfo":
        return cls(
            id=str(getattr(track, "id", "") or ""),
            title=getattr(track, "title", "") or "",
            artist=getattr(track, "artist", "") or "",
            album=getattr(track, "album", "") or "",
            duration_ms=int(getattr(track, "duration_ms", 0) or 0),
        )

    @property
    def display_name(self) -> str:
        artist = self.artist.strip()
        title = self.title.strip()
        if artist and title:
            return f"{artist} - {title}"
        return title or artist or self.id or "<unknown>"

    @property
    def search_label(self) -> str:
        album = self.album.strip()
        if album:
            return f"{self.display_name} [{album}]"
        return self.display_name


@dataclass(frozen=True)
class TorrentSearchResult:
    source: str
    title: str
    url: str
    score: int = 0
    seeders: int = 0
    leechers: int = 0
    size_bytes: int = 0
    category: str = ""
    info_hash: str = ""
    detail_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def with_score(self, score: int) -> "TorrentSearchResult":
        return replace(self, score=max(0, min(100, int(score))))


@dataclass(frozen=True)
class DownloadTask:
    id: Optional[str]
    uri: str
    destination: str


@dataclass(frozen=True)
class DownloadProgress:
    task_id: Optional[str]
    title: str
    status: str
    downloaded_bytes: int = 0
    size_bytes: int = 0
    download_speed: int = 0

    @property
    def percent(self) -> float:
        if self.size_bytes <= 0:
            return 100.0 if self.status in {"finished", "seeding"} else 0.0
        return min(100.0, self.downloaded_bytes / self.size_bytes * 100)
