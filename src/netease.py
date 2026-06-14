import requests
from dataclasses import dataclass
from typing import Optional


@dataclass
class NeteaseTrack:
    id: str
    title: str
    artist: str
    album: str
    duration_ms: int


@dataclass
class NeteasePlaylist:
    id: str
    name: str
    description: str
    track_count: int
    play_count: int
    cover_url: str


@dataclass
class PlaylistPage:
    playlists: list[NeteasePlaylist]
    has_more: bool
    next_before: Optional[int]


class NeteaseClient:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = self._session.get(f"{self._base}{path}", params=params, timeout=15)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            body = (resp.text or "").strip().replace("\n", " ")[:240]
            content_type = resp.headers.get("content-type", "")
            hint = ""
            lowered = body.lower()
            if "<html" in lowered or "open webui" in lowered:
                hint = (
                    "；当前 NETEASE_API_URL 似乎指向了一个网页应用，"
                    "请确认它指向 NeteaseCloudMusicApi 服务端口"
                )
            raise RuntimeError(
                f"Netease API returned non-JSON response: path={path} "
                f"status={resp.status_code} content-type={content_type!r} body={body!r}{hint}"
            )
        if data.get("code") not in (200, None):
            message = data.get("msg") or data.get("message") or data.get("error") or ""
            suffix = f": {message}" if message else ""
            raise RuntimeError(f"Netease API error {data.get('code')}: {path}{suffix}")
        return data

    def get_top_playlists(self, limit: int = 20, before: Optional[int] = None) -> PlaylistPage:
        params: dict = {"limit": limit}
        if before is not None:
            params["before"] = before
        data = self._get("/top/playlist/highquality", params=params)
        raw_playlists = data.get("playlists", [])
        playlists = []
        for p in raw_playlists:
            playlists.append(NeteasePlaylist(
                id=str(p["id"]),
                name=p["name"],
                description=p.get("description") or "",
                track_count=p.get("trackCount", 0),
                play_count=p.get("playCount", 0),
                cover_url=p.get("coverImgUrl", ""),
            ))
        has_more = bool(data.get("more", False))
        next_before = raw_playlists[-1].get("updateTime") if (raw_playlists and has_more) else None
        return PlaylistPage(playlists=playlists, has_more=has_more, next_before=next_before)

    def get_hot_playlists(self, limit: int = 20, cat: str = "全部", offset: int = 0) -> PlaylistPage:
        data = self._get("/top/playlist", params={"limit": limit, "cat": cat, "order": "hot", "offset": offset})
        raw_playlists = data.get("playlists", [])
        playlists = []
        for p in raw_playlists:
            playlists.append(NeteasePlaylist(
                id=str(p["id"]),
                name=p["name"],
                description=p.get("description") or "",
                track_count=p.get("trackCount", 0),
                play_count=p.get("playCount", 0),
                cover_url=p.get("coverImgUrl", ""),
            ))
        has_more = bool(data.get("more", False))
        next_before = offset + limit if has_more else None
        return PlaylistPage(playlists=playlists, has_more=has_more, next_before=next_before)

    def get_playlist_tracks(self, playlist_id: str) -> list[NeteaseTrack]:
        detail = self._get("/playlist/detail", params={"id": playlist_id})
        track_ids = [str(t["id"]) for t in detail.get("playlist", {}).get("trackIds", [])]
        if not track_ids:
            return []

        tracks = []
        # Fetch in batches of 100 (API limit)
        for i in range(0, len(track_ids), 100):
            batch = track_ids[i:i + 100]
            data = self._get("/song/detail", params={"ids": ",".join(batch)})
            for s in data.get("songs", []):
                ar = s.get("ar", [{}])
                tracks.append(NeteaseTrack(
                    id=str(s["id"]),
                    title=s.get("name", ""),
                    artist=ar[0].get("name", "") if ar else "",
                    album=s.get("al", {}).get("name", ""),
                    duration_ms=s.get("dt", 0),
                ))
        return tracks

    def get_track_by_id(self, song_id: str) -> NeteaseTrack:
        data = self._get("/song/detail", params={"ids": str(song_id)})
        songs = data.get("songs", [])
        if not songs:
            raise RuntimeError(f"Song {song_id} not found")
        s = songs[0]
        ar = s.get("ar", [{}])
        return NeteaseTrack(
            id=str(s["id"]),
            title=s.get("name", ""),
            artist=ar[0].get("name", "") if ar else "",
            album=s.get("al", {}).get("name", ""),
            duration_ms=s.get("dt", 0),
        )

    def get_playlist_by_id(self, playlist_id: str) -> NeteasePlaylist:
        data = self._get("/playlist/detail", params={"id": playlist_id})
        p = data.get("playlist")
        if p is None:
            raise RuntimeError(f"Playlist {playlist_id} not found")
        return NeteasePlaylist(
            id=str(p["id"]),
            name=p["name"],
            description=p.get("description") or "",
            track_count=p.get("trackCount", 0),
            play_count=p.get("playCount", 0),
            cover_url=p.get("coverImgUrl", ""),
        )
