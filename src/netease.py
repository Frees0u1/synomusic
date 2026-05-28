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


class NeteaseClient:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._session = requests.Session()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = self._session.get(f"{self._base}{path}", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (200, None):
            raise RuntimeError(f"Netease API error {data.get('code')}: {path}")
        return data

    def get_top_playlists(self, limit: int = 20) -> list[NeteasePlaylist]:
        data = self._get("/top/playlist/highquality", params={"limit": limit})
        playlists = []
        for p in data.get("playlists", []):
            playlists.append(NeteasePlaylist(
                id=str(p["id"]),
                name=p["name"],
                description=p.get("description") or "",
                track_count=p.get("trackCount", 0),
                play_count=p.get("playCount", 0),
                cover_url=p.get("coverImgUrl", ""),
            ))
        return playlists

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
