"""Yandex Music API client wrapper."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from yandex_music import ClientAsync, Track

_LOGGER = logging.getLogger(__name__)

PREFERRED_CODEC = "mp3"
PREFERRED_BITRATE = 320


@dataclass
class TrackInfo:
    """Minimal track info for queue and display."""

    track_id: str
    title: str
    artists: str
    album: str
    duration_ms: int
    cover_url: str | None


class YandexMusicClient:
    """Async wrapper around yandex-music library."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._client: ClientAsync | None = None
        self._user_id: str | None = None

    async def authenticate(self) -> str:
        """Authenticate and return account display name."""
        self._client = ClientAsync(self._token)
        await self._client.init()
        account = self._client.me.account
        self._user_id = str(account.uid)
        name = account.display_name or account.login or "Unknown"
        _LOGGER.info("Yandex Music authenticated as: %s (uid=%s)", name, self._user_id)
        return name

    @property
    def user_id(self) -> str:
        if self._user_id is None:
            raise RuntimeError("Client not authenticated.")
        return self._user_id

    @property
    def client(self) -> ClientAsync:
        if self._client is None:
            raise RuntimeError("Client not authenticated. Call authenticate() first.")
        return self._client

    async def search_tracks(self, query: str, count: int = 10) -> list[TrackInfo]:
        """Search for tracks by query string."""
        result = await self.client.search(query, type_="track")
        if not result or not result.tracks:
            return []
        return [
            self._to_track_info(track)
            for track in result.tracks.results[:count]
        ]

    async def get_direct_url(self, track_id: str) -> str:
        """Get direct streaming URL for a track. URL expires in ~1 minute."""
        download_info_list = await self.client.tracks_download_info(
            track_id, get_direct_links=True
        )

        # Prefer mp3 320, then mp3 any, then best available
        best = None
        for info in download_info_list:
            if info.codec == PREFERRED_CODEC and info.bitrate_in_kbps == PREFERRED_BITRATE:
                best = info
                break
            if info.codec == PREFERRED_CODEC and (best is None or info.bitrate_in_kbps > best.bitrate_in_kbps):
                best = info
        if best is None and download_info_list:
            best = max(download_info_list, key=lambda x: x.bitrate_in_kbps)

        if best is None:
            raise ValueError(f"No download info available for track {track_id}")

        url = best.direct_link
        if not url:
            url = await best.get_direct_link_async()

        _LOGGER.debug(
            "Direct URL for track %s: codec=%s, bitrate=%d, url=%s...",
            track_id, best.codec, best.bitrate_in_kbps, url[:80],
        )
        return url

    async def get_liked_tracks(self, count: int = 50) -> list[TrackInfo]:
        """Get user's liked tracks."""
        likes = await self.client.users_likes_tracks()
        if not likes:
            return []
        track_ids = [f"{like.track_id}" for like in likes[:count]]
        tracks = await self.client.tracks(track_ids)
        return [self._to_track_info(t) for t in tracks]

    async def get_user_playlists(self) -> list[dict]:
        """Get list of user playlists (id, title, track_count)."""
        playlists = await self.client.users_playlists_list()
        return [
            {
                "kind": pl.kind,
                "title": pl.title,
                "track_count": pl.track_count,
            }
            for pl in playlists
        ]

    async def get_playlist_tracks(self, user_id: str, kind: int) -> list[TrackInfo]:
        """Get tracks from a specific playlist."""
        playlist = await self.client.users_playlists(kind, user_id)
        if not playlist or not playlist.tracks:
            return []
        tracks = [st.track for st in playlist.tracks if st.track]
        return [self._to_track_info(t) for t in tracks]

    @staticmethod
    def _to_track_info(track: Track) -> TrackInfo:
        artists = ", ".join(a.name for a in (track.artists or []))
        album = ""
        if track.albums:
            album = track.albums[0].title or ""

        cover_url = None
        if track.cover_uri:
            cover_url = f"https://{track.cover_uri.replace('%%', '400x400')}"

        return TrackInfo(
            track_id=str(track.id),
            title=track.title or "Unknown",
            artists=artists,
            album=album,
            duration_ms=track.duration_ms or 0,
            cover_url=cover_url,
        )
