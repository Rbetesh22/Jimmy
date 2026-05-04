from pathlib import Path
from collections import defaultdict
from .base import Document, _h
from ..config import JIMMY_DATA_DIR

CACHE_PATH = JIMMY_DATA_DIR / "spotify_token.json"
SCOPES = "user-read-recently-played user-library-read user-follow-read"


class SpotifyIngester:
    def __init__(self, client_id: str, client_secret: str):
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri="http://127.0.0.1:8888/callback",
            scope=SCOPES,
            cache_path=str(CACHE_PATH),
            open_browser=True,
        ))

    def ingest(self) -> list[Document]:
        docs = []
        docs.extend(self._saved_tracks())
        docs.extend(self._saved_shows())
        docs.extend(self._recently_played())
        return docs

    def _saved_tracks(self) -> list[Document]:
        """Fetch all liked songs, grouped by artist."""
        artists: dict[str, list[dict]] = defaultdict(list)

        offset = 0
        while True:
            result = self.sp.current_user_saved_tracks(limit=50, offset=offset)
            items = result.get("items", [])
            if not items:
                break
            for item in items:
                track = item.get("track") or {}
                artist_names = [a["name"] for a in track.get("artists", [])]
                artist = artist_names[0] if artist_names else "Unknown Artist"
                artists[artist].append({
                    "name": track.get("name", ""),
                    "album": (track.get("album") or {}).get("name", ""),
                    "added_at": item.get("added_at", "")[:10],
                })
            offset += len(items)
            if not result.get("next"):
                break

        docs = []
        for artist, tracks in artists.items():
            if len(tracks) < 2:
                continue
            track_lines = [
                f"- {t['name']}" + (f" (added {t['added_at']})" if t["added_at"] else "")
                for t in sorted(tracks, key=lambda x: x["added_at"], reverse=True)[:20]
            ]
            content = (
                f"Artist: {artist}\nLiked tracks: {len(tracks)}\n\nTop liked songs:\n"
                + "\n".join(track_lines)
            )
            most_recent = max((t["added_at"] for t in tracks if t["added_at"]), default="")
            docs.append(Document(
                id=f"spotify_liked_{_h(artist)}",
                content=content,
                source="spotify",
                title=f"Spotify Liked: {artist}",
                metadata={"type": "music", "artist": artist, "liked_count": len(tracks), "date": most_recent, "status": "saved"},
            ))
        return docs

    def _saved_shows(self) -> list[Document]:
        """Fetch saved podcast shows and their recent episodes."""
        docs = []
        offset = 0
        while True:
            result = self.sp.current_user_saved_shows(limit=50, offset=offset)
            items = result.get("items", [])
            if not items:
                break

            for item in items:
                show = (item.get("show") or {})
                name = show.get("name", "Unknown Show")
                show_id = show.get("id", "")
                description = show.get("description", "")[:200]

                # Fetch recent episodes
                episode_lines = []
                if show_id:
                    try:
                        ep_result = self.sp.show_episodes(show_id, limit=10)
                        for ep in (ep_result.get("items") or []):
                            ep_name = ep.get("name", "")
                            ep_date = ep.get("release_date", "")
                            if ep_name:
                                episode_lines.append(f"- {ep_name}" + (f" ({ep_date})" if ep_date else ""))
                    except Exception:
                        pass

                content = f"Podcast: {name}\n"
                if description:
                    content += f"{description}\n"
                if episode_lines:
                    content += f"\nRecent episodes:\n" + "\n".join(episode_lines)

                docs.append(Document(
                    id=f"spotify_podcast_{_h(name)}",
                    content=content,
                    source="spotify",
                    title=f"Podcast: {name}",
                    metadata={"type": "podcast", "show": name, "status": "saved"},
                ))

            offset += len(items)
            if not result.get("next"):
                break

        return docs

    def _recently_played(self) -> list[Document]:
        """Fetch last 50 recently played tracks, grouped by artist."""
        result = self.sp.current_user_recently_played(limit=50)
        items = result.get("items", [])

        artists: dict[str, list[str]] = defaultdict(list)
        for item in items:
            track = item.get("track") or {}
            artist_names = [a["name"] for a in track.get("artists", [])]
            artist = artist_names[0] if artist_names else "Unknown Artist"
            track_name = track.get("name", "")
            played_at = item.get("played_at", "")[:10]
            if track_name:
                artists[artist].append(f"- {track_name} ({played_at})" if played_at else f"- {track_name}")

        if not artists:
            return []

        lines = []
        for artist, plays in sorted(artists.items(), key=lambda x: -len(x[1])):
            lines.append(f"\n{artist} ({len(plays)} plays):")
            lines.extend(plays[:5])

        content = "Recently played on Spotify (last 50 tracks):\n" + "\n".join(lines)
        return [Document(
            id="spotify_recent",
            content=content,
            source="spotify",
            title="Spotify Recently Played",
            metadata={"type": "recently_played", "status": "consumed"},
        )]
