"""Yandex Music API client wrapper."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from yandex_music import ClientAsync, Track, Album

_LOGGER = logging.getLogger(__name__)

PREFERRED_CODEC = "mp3"
PREFERRED_BITRATE = 320
PAGE_SIZE = 20


@dataclass
class TrackInfo:
    """Minimal track info for queue and display."""

    track_id: str
    title: str
    artists: str
    album: str
    duration_ms: int
    cover_url: str | None


@dataclass
class AlbumInfo:
    """Minimal album/podcast/audiobook info for browsing."""

    album_id: str
    title: str
    artists: str
    track_count: int
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

    async def get_liked_tracks(self, page: int = 0) -> tuple[list[TrackInfo], bool]:
        """Get user's liked tracks with pagination. Returns (tracks, has_more)."""
        likes = await self.client.users_likes_tracks()
        if not likes:
            return ([], False)
        start = page * PAGE_SIZE
        page_likes = likes[start:start + PAGE_SIZE]
        if not page_likes:
            return ([], False)
        track_ids = [f"{like.track_id}" for like in page_likes]
        tracks = await self.client.tracks(track_ids)
        has_more = start + PAGE_SIZE < len(likes)
        return ([self._to_track_info(t) for t in tracks], has_more)

    async def get_liked_tracks_all(self, count: int = 100) -> list[TrackInfo]:
        """Get all liked tracks (for queue playback)."""
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

    async def get_playlist_tracks(self, user_id: str, kind: int, page: int = 0) -> tuple[list[TrackInfo], bool]:
        """Get tracks from a playlist with pagination. Returns (tracks, has_more)."""
        playlist = await self.client.users_playlists(kind, user_id)
        if not playlist or not playlist.tracks:
            return ([], False)
        all_tracks = [st.track for st in playlist.tracks if st.track]
        start = page * PAGE_SIZE
        page_tracks = all_tracks[start:start + PAGE_SIZE]
        has_more = start + PAGE_SIZE < len(all_tracks)
        return ([self._to_track_info(t) for t in page_tracks], has_more)

    async def get_playlist_tracks_all(self, user_id: str, kind: int) -> list[TrackInfo]:
        """Get all tracks from a playlist (for queue playback)."""
        playlist = await self.client.users_playlists(kind, user_id)
        if not playlist or not playlist.tracks:
            return []
        tracks = [st.track for st in playlist.tracks if st.track]
        return [self._to_track_info(t) for t in tracks]

    async def get_podcasts(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get popular podcasts from landing with pagination. Returns (podcasts, has_more)."""
        landing = await self.client.podcasts()
        if not landing or not landing.podcasts:
            return ([], False)
        all_podcasts = [pid for pid in landing.podcasts]
        start = page * PAGE_SIZE
        page_ids = all_podcasts[start:start + PAGE_SIZE]
        if not page_ids:
            return ([], False)
        albums = await self.client.albums(page_ids)
        result = [self._to_album_info(a) for a in albums if a and a.type == "podcast"]
        has_more = start + PAGE_SIZE < len(all_podcasts)
        return (result, has_more)

    async def get_my_podcasts(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get user's liked podcasts with pagination. Returns (podcasts, has_more)."""
        likes = await self.client.users_likes_albums()
        if not likes:
            return ([], False)
        all_podcasts = [like.album for like in likes if like.album and like.album.type == "podcast"]
        start = page * PAGE_SIZE
        page_items = all_podcasts[start:start + PAGE_SIZE]
        has_more = start + PAGE_SIZE < len(all_podcasts)
        return ([self._to_album_info(a) for a in page_items], has_more)

    async def get_my_audiobooks(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get user's liked audiobooks with pagination. Returns (books, has_more)."""
        likes = await self.client.users_likes_albums()
        if not likes:
            return ([], False)
        all_books = [like.album for like in likes if like.album and like.album.type == "audiobook"]
        start = page * PAGE_SIZE
        page_items = all_books[start:start + PAGE_SIZE]
        has_more = start + PAGE_SIZE < len(all_books)
        return ([self._to_album_info(a) for a in page_items], has_more)

    async def search_audiobooks(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Search for audiobooks. Returns (albums, has_more)."""
        result = await self.client.search("аудиокниги", type_="podcast", page=page)
        if not result or not result.podcasts:
            return ([], False)
        albums = [self._to_album_info(a) for a in result.podcasts.results if a]
        has_more = len(result.podcasts.results) >= (result.per_page or 10)
        return (albums, has_more)

    async def get_album_episodes(self, album_id: str) -> tuple[str, list[TrackInfo]]:
        """Get tracks/episodes from an album (podcast/audiobook).

        Returns (album_title, tracks).
        """
        album = await self.client.albums_with_tracks(int(album_id))
        if not album or not album.volumes:
            return ("", [])
        tracks = []
        for volume in album.volumes:
            tracks.extend(volume)
        title = album.title or f"Album {album_id}"
        return (title, [self._to_track_info(t) for t in tracks if t])

    @staticmethod
    def _to_album_info(album: Album) -> AlbumInfo:
        artists = ", ".join(a.name for a in (album.artists or []))
        cover_url = None
        if album.cover_uri:
            cover_url = f"https://{album.cover_uri.replace('%%', '400x400')}"
        return AlbumInfo(
            album_id=str(album.id),
            title=album.title or "Unknown",
            artists=artists,
            track_count=album.track_count or 0,
            cover_url=cover_url,
        )

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
