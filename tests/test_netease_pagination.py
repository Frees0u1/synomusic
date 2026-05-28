from unittest.mock import patch
from src.netease import NeteaseClient, PlaylistPage, NeteasePlaylist


def _make_api_response(n=2, more=True):
    playlists = [
        {
            "id": i,
            "name": f"歌单{i}",
            "description": "",
            "trackCount": 10,
            "playCount": 1000,
            "coverImgUrl": "",
            "updateTime": 1700000000 + i,
        }
        for i in range(n)
    ]
    return {"code": 200, "playlists": playlists, "more": more}


def test_get_top_playlists_returns_playlist_page():
    client = NeteaseClient("http://localhost:3000")
    with patch.object(client, "_get", return_value=_make_api_response(2, more=True)) as mock_get:
        page = client.get_top_playlists(limit=2)
        mock_get.assert_called_once_with("/top/playlist/highquality", params={"limit": 2})
    assert isinstance(page, PlaylistPage)
    assert len(page.playlists) == 2
    assert page.has_more is True
    assert page.next_before == 1700000001  # updateTime of last playlist (id=1)


def test_get_top_playlists_with_before_param():
    client = NeteaseClient("http://localhost:3000")
    with patch.object(client, "_get", return_value=_make_api_response(2, more=False)) as mock_get:
        page = client.get_top_playlists(limit=2, before=1700000001)
        mock_get.assert_called_once_with(
            "/top/playlist/highquality", params={"limit": 2, "before": 1700000001}
        )
    assert page.has_more is False


def test_get_top_playlists_empty_page():
    client = NeteaseClient("http://localhost:3000")
    with patch.object(client, "_get", return_value={"code": 200, "playlists": [], "more": False}):
        page = client.get_top_playlists(limit=20)
    assert page.playlists == []
    assert page.has_more is False
    assert page.next_before is None
