"""Yandex Music API client wrapper."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from yandex_music import Album, ClientAsync, Track

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

    # --- Tracks ---

    async def get_tracks_by_ids(self, track_ids: list[str]) -> list[TrackInfo]:
        """Get tracks by IDs."""
        tracks = await self.client.tracks(track_ids)
        return [self._to_track_info(t) for t in tracks if t]

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

    # --- Liked tracks ---

    async def get_liked_tracks(self, page: int = 0) -> tuple[list[TrackInfo], bool]:
        """Get user's liked tracks with pagination."""
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

    # --- Playlists ---

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
        """Get tracks from a playlist with pagination."""
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

    # --- Chart ---

    async def get_chart_tracks(self, page: int = 0) -> tuple[list[TrackInfo], bool]:
        """Get chart/top tracks with pagination."""
        chart_info = await self.client.chart()
        if not chart_info or not chart_info.chart or not chart_info.chart.tracks:
            return ([], False)
        all_tracks = [st.track for st in chart_info.chart.tracks if st.track]
        start = page * PAGE_SIZE
        page_tracks = all_tracks[start:start + PAGE_SIZE]
        has_more = start + PAGE_SIZE < len(all_tracks)
        return ([self._to_track_info(t) for t in page_tracks], has_more)

    async def get_chart_tracks_all(self) -> list[TrackInfo]:
        """Get all chart tracks (for queue playback)."""
        chart_info = await self.client.chart()
        if not chart_info or not chart_info.chart or not chart_info.chart.tracks:
            return []
        tracks = [st.track for st in chart_info.chart.tracks if st.track]
        return [self._to_track_info(t) for t in tracks]

    # --- Liked albums (podcasts, audiobooks, music) ---

    async def _get_liked_albums(
        self, predicate: Callable[[Album], bool], page: int = 0
    ) -> tuple[list[AlbumInfo], bool]:
        """Get user's liked albums filtered by predicate, with pagination."""
        likes = await self.client.users_likes_albums()
        if not likes:
            return ([], False)
        filtered = [like.album for like in likes if like.album and predicate(like.album)]
        start = page * PAGE_SIZE
        page_items = filtered[start:start + PAGE_SIZE]
        has_more = start + PAGE_SIZE < len(filtered)
        return ([self._to_album_info(a) for a in page_items], has_more)

    async def get_my_podcasts(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get user's liked podcasts with pagination."""
        return await self._get_liked_albums(lambda a: a.type == "podcast", page)

    async def get_my_audiobooks(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get user's liked audiobooks with pagination."""
        return await self._get_liked_albums(lambda a: a.type == "audiobook", page)

    async def get_my_albums(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get user's liked music albums (excluding podcasts/audiobooks)."""
        return await self._get_liked_albums(
            lambda a: a.type not in ("podcast", "audiobook"), page
        )

    async def get_new_podcast_episodes(self, page: int = 0) -> tuple[list[TrackInfo], bool]:
        """Get newest episodes from subscribed podcasts.

        Fetches all subscribed podcasts, then gets the first episode from each
        (ordered newest-first by the API). Results are combined and paginated.
        """
        likes = await self.client.users_likes_albums()
        if not likes:
            return ([], False)
        podcast_ids = [
            str(like.album.id) for like in likes
            if like.album and like.album.type == "podcast"
        ]
        if not podcast_ids:
            return ([], False)

        # Fetch albums with tracks in batches to get episodes
        BATCH_SIZE = 10
        all_episodes: list[TrackInfo] = []
        for i in range(0, len(podcast_ids), BATCH_SIZE):
            batch = podcast_ids[i:i + BATCH_SIZE]
            tasks = [self.client.albums_with_tracks(int(pid)) for pid in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    continue
                if not result or not result.volumes:
                    continue
                # Take first episode (newest) from each podcast
                episodes = result.volumes[0]
                if episodes:
                    all_episodes.append(self._to_track_info(episodes[0]))

        # Sort by duration descending as a rough proxy (no publish date in TrackInfo)
        # In practice episodes come in API order which is newest-first per podcast
        start = page * PAGE_SIZE
        page_episodes = all_episodes[start:start + PAGE_SIZE]
        has_more = start + PAGE_SIZE < len(all_episodes)
        return (page_episodes, has_more)

    # --- Landing lists ---

    async def get_podcasts(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get popular podcasts from landing with pagination."""
        landing = await self.client.podcasts()
        if not landing or not landing.podcasts:
            return ([], False)
        all_ids = list(landing.podcasts)
        start = page * PAGE_SIZE
        page_ids = all_ids[start:start + PAGE_SIZE]
        if not page_ids:
            return ([], False)
        albums = await self.client.albums(page_ids)
        result = [self._to_album_info(a) for a in albums if a and a.type == "podcast"]
        has_more = start + PAGE_SIZE < len(all_ids)
        return (result, has_more)

    async def get_new_releases(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Get new music releases with pagination."""
        landing = await self.client.new_releases()
        if not landing or not landing.new_releases:
            return ([], False)
        all_ids = list(landing.new_releases)
        start = page * PAGE_SIZE
        page_ids = all_ids[start:start + PAGE_SIZE]
        if not page_ids:
            return ([], False)
        albums = await self.client.albums(page_ids)
        result = [self._to_album_info(a) for a in albums if a]
        has_more = start + PAGE_SIZE < len(all_ids)
        return (result, has_more)

    async def search_audiobooks(self, page: int = 0) -> tuple[list[AlbumInfo], bool]:
        """Search for audiobooks."""
        result = await self.client.search("аудиокниги", type_="podcast", page=page)
        if not result or not result.podcasts:
            return ([], False)
        albums = [self._to_album_info(a) for a in result.podcasts.results if a]
        has_more = len(result.podcasts.results) >= (result.per_page or 10)
        return (albums, has_more)

    # --- Album/episodes ---

    async def get_album_tracks(self, album_id: str) -> tuple[str, list[TrackInfo]]:
        """Get tracks from an album/podcast/audiobook. Returns (title, tracks)."""
        album = await self.client.albums_with_tracks(int(album_id))
        if not album or not album.volumes:
            return ("", [])
        tracks = []
        for volume in album.volumes:
            tracks.extend(volume)
        title = album.title or f"Album {album_id}"
        return (title, [self._to_track_info(t) for t in tracks if t])

    # --- Converters ---

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
