"""Yandex Music media source for browsing in HA Media panel."""

from __future__ import annotations

import logging

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .browse import BrowseItem, resolve_browse
from .const import DOMAIN
from .yandex_client import YandexMusicClient

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
        result = await resolve_browse(ym, identifier)
        if result:
            return self._to_source(result)
        return self._to_source(await resolve_browse(ym, "") or self._empty_root())

    @staticmethod
    def _empty_root() -> BrowseItem:
        return BrowseItem("", "directory", "music", "Yandex Music", False, True)

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

    def _to_source(self, item: BrowseItem) -> BrowseMediaSource:
        """Convert BrowseItem tree to BrowseMediaSource tree."""
        children = [self._to_source(c) for c in item.children] if item.children else None
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=item.identifier,
            media_class=item.media_class,
            media_content_type=item.content_type,
            title=item.title,
            can_play=item.can_play,
            can_expand=item.can_expand,
            thumbnail=item.thumbnail,
            children=children,
        )
