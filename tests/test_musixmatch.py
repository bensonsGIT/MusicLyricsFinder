import pytest
from unittest.mock import MagicMock, patch

from lyrics_finder.musixmatch import MusixmatchClient, MusixmatchError


def _make_response(status_code: int, body: dict) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "message": {
            "header": {"status_code": status_code},
            "body": body,
        }
    }
    return mock


@pytest.fixture
def client():
    return MusixmatchClient("test_key")


def test_search_track_success(client):
    track_list = [{"track": {"track_id": 1, "track_name": "Song", "artist_name": "Artist"}}]
    mock_resp = _make_response(200, {"track_list": track_list})

    with patch.object(client.session, "get", return_value=mock_resp):
        results = client.search_track("Song", "Artist")

    assert len(results) == 1
    assert results[0]["track"]["track_name"] == "Song"


def test_search_track_not_found(client):
    mock_resp = _make_response(404, {})

    with patch.object(client.session, "get", return_value=mock_resp):
        with pytest.raises(MusixmatchError, match="not found"):
            client.search_track("Unknown Song")


def test_search_track_auth_error(client):
    mock_resp = _make_response(401, {})

    with patch.object(client.session, "get", return_value=mock_resp):
        with pytest.raises(MusixmatchError, match="Authentication failed"):
            client.search_track("Song")


def test_get_lyrics_strips_footer(client):
    raw = "Line 1\nLine 2\n******* This Lyrics is NOT for Commercial use *******"
    mock_resp = _make_response(200, {"lyrics": {"lyrics_body": raw}})

    with patch.object(client.session, "get", return_value=mock_resp):
        lyrics = client.get_lyrics(42)

    assert lyrics == "Line 1\nLine 2"


def test_get_lyrics_none_when_empty(client):
    mock_resp = _make_response(200, {"lyrics": {"lyrics_body": ""}})

    with patch.object(client.session, "get", return_value=mock_resp):
        lyrics = client.get_lyrics(42)

    assert lyrics is None


def test_network_error_raises(client):
    import requests

    with patch.object(client.session, "get", side_effect=requests.ConnectionError("timeout")):
        with pytest.raises(MusixmatchError, match="Network error"):
            client.search_track("Song")
