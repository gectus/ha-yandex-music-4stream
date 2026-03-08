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
from .yandex_client import AlbumInfo, TrackInfo, YandexMusicClient

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

        # Parse identifier and optional page: "section:page" or "section:param:page"
        handler = self._resolve_handler(identifier)
        if handler:
            return await handler(ym)

        return self._build_root()

    def _resolve_handler(self, identifier: str):
        """Route identifier to handler method."""
        # Simple identifiers
        simple = {
            "liked": self._make_handler(self._browse_liked, 0),
            "playlists": lambda ym: self._browse_playlists(ym),
            "podcasts": lambda ym: self._browse_podcasts_root(ym),
            "podcasts_top": self._make_handler(self._browse_podcasts_top, 0),
            "podcasts_my": self._make_handler(self._browse_podcasts_my, 0),
            "books": lambda ym: self._browse_books_root(ym),
            "books_my": self._make_handler(self._browse_books_my, 0),
            "books_popular": self._make_handler(self._browse_books_popular, 0),
        }
        if identifier in simple:
            return simple[identifier]

        # Paginated identifiers: "section:page"
        if identifier.startswith("liked:"):
            return self._make_handler(self._browse_liked, int(identifier.split(":", 1)[1]))
        if identifier.startswith("podcasts_top:"):
            return self._make_handler(self._browse_podcasts_top, int(identifier.split(":", 1)[1]))
        if identifier.startswith("podcasts_my:"):
            return self._make_handler(self._browse_podcasts_my, int(identifier.split(":", 1)[1]))
        if identifier.startswith("books_my:"):
            return self._make_handler(self._browse_books_my, int(identifier.split(":", 1)[1]))
        if identifier.startswith("books_popular:"):
            return self._make_handler(self._browse_books_popular, int(identifier.split(":", 1)[1]))

        # Parameterized: "playlist:kind" or "playlist:kind:page"
        if identifier.startswith("playlist:"):
            parts = identifier.split(":")
            kind = int(parts[1])
            page = int(parts[2]) if len(parts) > 2 else 0
            return lambda ym: self._browse_playlist_tracks(ym, kind, page)

        if identifier.startswith("album:"):
            album_id = identifier.split(":", 1)[1]
            return lambda ym: self._browse_album_episodes(ym, album_id)

        return None

    @staticmethod
    def _make_handler(method, page):
        return lambda ym: method(ym, page)

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
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="podcasts",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.PODCAST,
                    title="Подкасты",
                    can_play=False,
                    can_expand=True,
                ),
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="books",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.MUSIC,
                    title="Книги",
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

    # --- Liked tracks ---

    async def _browse_liked(self, ym: YandexMusicClient, page: int = 0) -> BrowseMediaSource:
        tracks, has_more = await ym.get_liked_tracks(page=page)
        children = [self._track_to_source(t) for t in tracks]
        if has_more:
            children.append(self._more_item(f"liked:{page + 1}"))
        title = "Мне нравится" if page == 0 else f"Мне нравится (стр. {page + 1})"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"liked:{page}" if page else "liked",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    # --- Playlists ---

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
        self, ym: YandexMusicClient, kind: int, page: int = 0
    ) -> BrowseMediaSource:
        tracks, has_more = await ym.get_playlist_tracks(ym.user_id, kind, page=page)
        playlists = await ym.get_user_playlists()
        base_title = next(
            (pl["title"] for pl in playlists if pl["kind"] == kind),
            f"Плейлист {kind}",
        )
        children = [self._track_to_source(t) for t in tracks]
        if has_more:
            children.append(self._more_item(f"playlist:{kind}:{page + 1}"))
        title = base_title if page == 0 else f"{base_title} (стр. {page + 1})"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"playlist:{kind}:{page}" if page else f"playlist:{kind}",
            media_class=MediaClass.PLAYLIST,
            media_content_type=MediaType.PLAYLIST,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    # --- Podcasts ---

    async def _browse_podcasts_root(self, ym: YandexMusicClient) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="podcasts",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PODCAST,
            title="Подкасты",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="podcasts_my",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.PODCAST,
                    title="Мои подкасты",
                    can_play=False,
                    can_expand=True,
                ),
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="podcasts_top",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.PODCAST,
                    title="Топ подкастов",
                    can_play=False,
                    can_expand=True,
                ),
            ],
        )

    async def _browse_podcasts_top(self, ym: YandexMusicClient, page: int = 0) -> BrowseMediaSource:
        podcasts, has_more = await ym.get_podcasts(page=page)
        children = [self._album_to_source(a) for a in podcasts]
        if has_more:
            children.append(self._more_item(f"podcasts_top:{page + 1}"))
        title = "Топ подкастов" if page == 0 else f"Топ подкастов (стр. {page + 1})"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"podcasts_top:{page}" if page else "podcasts_top",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PODCAST,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_podcasts_my(self, ym: YandexMusicClient, page: int = 0) -> BrowseMediaSource:
        podcasts, has_more = await ym.get_my_podcasts(page=page)
        children = [self._album_to_source(a) for a in podcasts]
        if has_more:
            children.append(self._more_item(f"podcasts_my:{page + 1}"))
        title = "Мои подкасты" if page == 0 else f"Мои подкасты (стр. {page + 1})"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"podcasts_my:{page}" if page else "podcasts_my",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PODCAST,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    # --- Books ---

    async def _browse_books_root(self, ym: YandexMusicClient) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="books",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title="Книги",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="books_my",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.MUSIC,
                    title="Мои книги",
                    can_play=False,
                    can_expand=True,
                ),
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="books_popular",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.MUSIC,
                    title="Популярные книги",
                    can_play=False,
                    can_expand=True,
                ),
            ],
        )

    async def _browse_books_my(self, ym: YandexMusicClient, page: int = 0) -> BrowseMediaSource:
        books, has_more = await ym.get_my_audiobooks(page=page)
        children = [self._album_to_source(a) for a in books]
        if has_more:
            children.append(self._more_item(f"books_my:{page + 1}"))
        title = "Мои книги" if page == 0 else f"Мои книги (стр. {page + 1})"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"books_my:{page}" if page else "books_my",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_books_popular(self, ym: YandexMusicClient, page: int = 0) -> BrowseMediaSource:
        books, has_more = await ym.search_audiobooks(page=page)
        children = [self._album_to_source(a) for a in books]
        if has_more:
            children.append(self._more_item(f"books_popular:{page + 1}"))
        title = "Популярные книги" if page == 0 else f"Популярные книги (стр. {page + 1})"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"books_popular:{page}" if page else "books_popular",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    # --- Album episodes ---

    async def _browse_album_episodes(
        self, ym: YandexMusicClient, album_id: str
    ) -> BrowseMediaSource:
        title, tracks = await ym.get_album_episodes(album_id)
        children = [self._track_to_source(t) for t in tracks]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"album:{album_id}",
            media_class=MediaClass.ALBUM,
            media_content_type=MediaType.MUSIC,
            title=title,
            can_play=True,
            can_expand=True,
            children=children,
        )

    # --- Helpers ---

    def _more_item(self, identifier: str) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.MUSIC,
            title="Ещё...",
            can_play=False,
            can_expand=True,
        )

    def _album_to_source(self, album: AlbumInfo) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"album:{album.album_id}",
            media_class=MediaClass.ALBUM,
            media_content_type=MediaType.MUSIC,
            title=f"{album.title}" + (f" — {album.artists}" if album.artists else ""),
            can_play=True,
            can_expand=True,
            thumbnail=album.cover_url,
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
