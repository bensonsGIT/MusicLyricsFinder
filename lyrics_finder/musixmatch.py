import requests

BASE_URL = "https://api.musixmatch.com/ws/1.1"

class MusixmatchError(Exception):
    pass

class MusixmatchClient:
    STATUS_MESSAGES = {
        200: "OK",
        400: "Bad syntax or missing required parameters",
        401: "Authentication failed or API key not valid",
        402: "Subscription limit reached",
        404: "Track or lyrics not found",
        429: "Too many requests",
        500: "Server error",
    }

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.params = {"apikey": api_key, "format": "json"}

    def _get(self, endpoint: str, **params) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise MusixmatchError(f"Network error: {e}") from e

        data = resp.json()
        status = data.get("message", {}).get("header", {}).get("status_code", 0)
        if status != 200:
            msg = self.STATUS_MESSAGES.get(status, f"Unknown error (status {status})")
            raise MusixmatchError(f"Musixmatch API error: {msg}")
        return data["message"]["body"]

    def search_track(self, title: str, artist: str = "") -> list[dict]:
        params = {"q_track": title, "page_size": 5, "page": 1, "s_track_rating": "desc"}
        if artist:
            params["q_artist"] = artist
        body = self._get("track.search", **params)
        return body.get("track_list", [])

    def get_lyrics(self, track_id: int) -> str | None:
        body = self._get("track.lyrics.get", track_id=track_id)
        lyrics_body = body.get("lyrics", {}).get("lyrics_body", "")
        # Strip the Musixmatch copyright footer
        if lyrics_body:
            lines = lyrics_body.splitlines()
            cleaned = []
            for line in lines:
                if line.startswith("****") or "This Lyrics is NOT for Commercial use" in line:
                    break
                cleaned.append(line)
            return "\n".join(cleaned).strip() or None
        return None
