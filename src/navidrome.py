import hashlib
import os
import time
import requests
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NaviTrack:
    id: str
    title: str
    artist: str
    album: str
    path: str = ""


@dataclass
class CreatePlaylistResult:
    playlist_id: str
    track_count: int
    api_calls: int
    failed_calls: int
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return sum(self.latencies_ms)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0


class NavidromeClient:
    def __init__(self, base_url: str, username: str, password: str):
        self._base = base_url.rstrip("/") + "/rest"
        self._user = username
        self._password = password
        self._session = requests.Session()

    def _auth_params(self) -> dict:
        salt = os.urandom(6).hex()
        token = hashlib.md5((self._password + salt).encode()).hexdigest()
        return {
            "u": self._user,
            "t": token,
            "s": salt,
            "v": "1.16.0",
            "c": "synomusic",
            "f": "json",
        }

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        p = self._auth_params()
        if params:
            p.update(params)
        resp = self._session.get(f"{self._base}/{endpoint}", params=p, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        root = data.get("subsonic-response", {})
        if root.get("status") != "ok":
            error = root.get("error", {})
            raise RuntimeError(f"Subsonic error {error.get('code')}: {error.get('message')}")
        return root

    def ping(self) -> bool:
        try:
            self._get("ping")
            return True
        except Exception:
            return False

    def search_track(self, title: str, artist: str) -> list[NaviTrack]:
        query = f"{title} {artist}"
        data = self._get("search3", params={"query": query, "songCount": 20, "albumCount": 0, "artistCount": 0})
        songs = data.get("searchResult3", {}).get("song", [])
        return [
            NaviTrack(
                id=str(s["id"]),
                title=s.get("title", ""),
                artist=s.get("artist", ""),
                album=s.get("album", ""),
            )
            for s in songs
        ]

    def get_all_tracks(self) -> list[NaviTrack]:
        """Return all tracks via search with wildcard."""
        tracks = []
        offset = 0
        size = 500
        while True:
            data = self._get("search3", params={
                "query": "",
                "songCount": size,
                "songOffset": offset,
                "albumCount": 0,
                "artistCount": 0,
            })
            songs = data.get("searchResult3", {}).get("song", [])
            tracks.extend([
                NaviTrack(
                    id=str(s["id"]),
                    title=s.get("title", ""),
                    artist=s.get("artist", ""),
                    album=s.get("album", ""),
                    path=s.get("path", ""),
                )
                for s in songs
            ])
            if len(songs) < size:
                break
            offset += size
        return tracks

    def create_playlist(self, name: str, track_ids: list[str]) -> CreatePlaylistResult:
        t0 = time.monotonic()
        data = self._get("createPlaylist", params={"name": name})
        create_latency = (time.monotonic() - t0) * 1000
        playlist_id = str(data["playlist"]["id"])

        latencies: list[float] = [create_latency]
        failed = 0
        chunk_size = 200
        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i:i + chunk_size]
            auth = self._auth_params()
            auth["playlistId"] = playlist_id
            song_params = [("songIdToAdd", tid) for tid in chunk]
            t1 = time.monotonic()
            try:
                resp = self._session.get(
                    f"{self._base}/updatePlaylist",
                    params=list(auth.items()) + song_params,
                    timeout=30,
                )
                resp.raise_for_status()
                update_root = resp.json().get("subsonic-response", {})
                if update_root.get("status") != "ok":
                    error = update_root.get("error", {})
                    raise RuntimeError(f"Subsonic updatePlaylist error {error.get('code')}: {error.get('message')}")
            except Exception:
                failed += 1
                raise
            finally:
                latencies.append((time.monotonic() - t1) * 1000)

        return CreatePlaylistResult(
            playlist_id=playlist_id,
            track_count=len(track_ids),
            api_calls=len(latencies),
            failed_calls=failed,
            latencies_ms=latencies,
        )
