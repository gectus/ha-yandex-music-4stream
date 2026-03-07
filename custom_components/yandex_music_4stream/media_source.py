"""Yandex Music media source for browsing in HA Media panel."""

from __future__ import annotations

import logging

from homeassistant.components.media_player import BrowseMedia, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .yandex_client import TrackInfo, YandexMusicClient

_LOGGER = logging.getLogger(__name__)


async def async_get_media_source(hass: HomeAssistant) -> YandexMusicMediaSource:
    """Set up Yandex Music media source."""
    return YandexMusicMediaSource(hass)


class YandexMusicMediaSource(MediaSource):
    """Provide Yandex Music as a media source."""

    name = "Yandex Music"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _get_ym_client(self) -> YandexMusicClient | None:
        """Get Yandex Music client from any config entry."""
        if DOMAIN not in self.hass.data:
            return None
        for key, entry_data in self.hass.data[DOMAIN].items():
            if key.startswith("_"):
                continue
            if isinstance(entry_data, dict) and "ym_client" in entry_data:
                return entry_data["ym_client"]
        return None

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        ym = self._get_ym_client()
        if not ym:
            raise ValueError("Yandex Music not configured")

        # item.identifier is like "track:12345"
        if item.identifier and item.identifier.startswith("track:"):
            track_id = item.identifier.split(":", 1)[1]
            url = await ym.get_direct_url(track_id)
            return PlayMedia(url=url, mime_type="audio/mpeg")

        raise ValueError(f"Unknown media item: {item.identifier}")

    async def async_browse_media(
        self, item: MediaSourceItem
    ) -> BrowseMediaSource:
        """Browse Yandex Music content."""
        ym = self._get_ym_client()
        if not ym:
            return self._build_error("Yandex Music не настроен")

        identifier = item.identifier or ""

        if not identifier:
            return self._build_root()
        elif identifier == "liked":
            return await self._browse_liked(ym)
        elif identifier == "playlists":
            return await self._browse_playlists(ym)
        elif identifier.startswith("playlist:"):
            kind = int(identifier.split(":", 1)[1])
            return await self._browse_playlist_tracks(ym, kind)

        return self._build_root()

    def _build_root(self) -> BrowseMediaSource:
        """Build root menu."""
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title="Yandex Music",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="liked",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.MUSIC,
                    title="Мне нравится",
                    can_play=False,
                    can_expand=True,
                ),
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="playlists",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.MUSIC,
                    title="Мои плейлисты",
                    can_play=False,
                    can_expand=True,
                ),
            ],
        )

    def _build_error(self, message: str) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title=message,
            can_play=False,
            can_expand=False,
            children=[],
        )

    async def _browse_liked(self, ym: YandexMusicClient) -> BrowseMediaSource:
        tracks = await ym.get_liked_tracks(count=100)
        children = [self._track_to_source(t) for t in tracks]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="liked",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title="Мне нравится",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_playlists(self, ym: YandexMusicClient) -> BrowseMediaSource:
        playlists = await ym.get_user_playlists()
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"playlist:{pl['kind']}",
                media_class=MediaClass.PLAYLIST,
                media_content_type=MediaType.PLAYLIST,
                title=f"{pl['title']} ({pl['track_count']})",
                can_play=False,
                can_expand=True,
            )
            for pl in playlists
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="playlists",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title="Мои плейлисты",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_playlist_tracks(
        self, ym: YandexMusicClient, kind: int
    ) -> BrowseMediaSource:
        tracks = await ym.get_playlist_tracks(ym.user_id, kind)
        playlists = await ym.get_user_playlists()
        title = next(
            (pl["title"] for pl in playlists if pl["kind"] == kind),
            f"Плейлист {kind}",
        )
        children = [self._track_to_source(t) for t in tracks]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"playlist:{kind}",
            media_class=MediaClass.PLAYLIST,
            media_content_type=MediaType.PLAYLIST,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _track_to_source(self, track: TrackInfo) -> BrowseMediaSource:
        duration_str = ""
        if track.duration_ms:
            mins, secs = divmod(track.duration_ms // 1000, 60)
            duration_str = f" ({mins}:{secs:02d})"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"track:{track.track_id}",
            media_class=MediaClass.TRACK,
            media_content_type=MediaType.MUSIC,
            title=f"{track.artists} — {track.title}{duration_str}",
            can_play=True,
            can_expand=False,
            thumbnail=track.cover_url,
        )
