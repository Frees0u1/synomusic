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
class ApiCallStat:
    endpoint: str
    calls: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def record(self, latency_ms: float, failed: bool = False) -> None:
        self.calls += 1
        self.latencies_ms.append(latency_ms)
        if failed:
            self.failed += 1

    @property
    def avg_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0


@dataclass
class CreatePlaylistResult:
    playlist_id: str
    playlist_name: str
    track_count: int
    created: bool          # True = new playlist, False = updated existing
    stats: list[ApiCallStat] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return sum(s.avg_ms * s.calls for s in self.stats)

    @property
    def failed_calls(self) -> int:
        return sum(s.failed for s in self.stats)


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

    def _timed_get(self, stat: ApiCallStat, endpoint: str, params: Optional[dict] = None) -> dict:
        t0 = time.monotonic()
        failed = False
        try:
            result = self._get(endpoint, params=params)
            return result
        except Exception:
            failed = True
            raise
        finally:
            stat.record((time.monotonic() - t0) * 1000, failed=failed)

    def _timed_raw(self, stat: ApiCallStat, endpoint: str, params: list) -> None:
        """For repeated-key params (songIdToAdd, songIndexToRemove)."""
        t0 = time.monotonic()
        failed = False
        try:
            auth = self._auth_params()
            resp = self._session.get(
                f"{self._base}/{endpoint}",
                params=list(auth.items()) + params,
                timeout=30,
            )
            resp.raise_for_status()
            root = resp.json().get("subsonic-response", {})
            if root.get("status") != "ok":
                error = root.get("error", {})
                raise RuntimeError(f"Subsonic {endpoint} error {error.get('code')}: {error.get('message')}")
        except Exception:
            failed = True
            raise
        finally:
            stat.record((time.monotonic() - t0) * 1000, failed=failed)

    def get_playlists(self) -> list[dict]:
        data = self._get("getPlaylists")
        playlists = data.get("playlists", {})
        if isinstance(playlists, dict):
            return playlists.get("playlist", []) or []
        return []

    def get_playlist_song_count(self, playlist_id: str) -> int:
        data = self._get("getPlaylist", params={"id": playlist_id})
        return int(data.get("playlist", {}).get("songCount", 0))

    def create_playlist(self, name: str, track_ids: list[str]) -> CreatePlaylistResult:
        stat_get_playlists = ApiCallStat("getPlaylists")
        stat_create = ApiCallStat("createPlaylist")
        stat_get_playlist = ApiCallStat("getPlaylist")
        stat_update_clear = ApiCallStat("updatePlaylist (clear)")
        stat_update_add = ApiCallStat("updatePlaylist (add)")

        # findOrCreate
        t0 = time.monotonic()
        try:
            existing = self.get_playlists()
        finally:
            stat_get_playlists.record((time.monotonic() - t0) * 1000)

        existing_id = next((p["id"] for p in existing if p.get("name") == name), None)
        created = existing_id is None

        if created:
            data = self._timed_get(stat_create, "createPlaylist", params={"name": name})
            playlist_id = str(data["playlist"]["id"])
        else:
            playlist_id = existing_id
            # Clear existing songs
            t1 = time.monotonic()
            try:
                song_count = self.get_playlist_song_count(playlist_id)
            finally:
                stat_get_playlist.record((time.monotonic() - t1) * 1000)

            if song_count > 0:
                chunk_size = 200
                for i in range(0, song_count, chunk_size):
                    indices = list(range(i, min(i + chunk_size, song_count)))
                    self._timed_raw(
                        stat_update_clear, "updatePlaylist",
                        [("playlistId", playlist_id)] + [("songIndexToRemove", idx) for idx in indices],
                    )

        # Add all tracks in chunks of 200
        chunk_size = 200
        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i:i + chunk_size]
            self._timed_raw(
                stat_update_add, "updatePlaylist",
                [("playlistId", playlist_id)] + [("songIdToAdd", tid) for tid in chunk],
            )

        stats = [s for s in [stat_create, stat_update_clear, stat_update_add] if s.calls > 0]
        return CreatePlaylistResult(
            playlist_id=playlist_id,
            playlist_name=name,
            track_count=len(track_ids),
            created=created,
            stats=stats,
        )
