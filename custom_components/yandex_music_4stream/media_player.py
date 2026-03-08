"""Media player platform for Yandex Music 4STREAM."""

from __future__ import annotations

import asyncio
import logging
import random
import socket

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import utcnow

from .arylic_client import ArylicClient
from .const import (
    CONF_DEVICES,
    CONF_DEVICE_HOST,
    CONF_DEVICE_NAME,
    DOMAIN,
    POLL_INTERVAL,
)
from .proxy import StreamProxy
from .yandex_client import TrackInfo, YandexMusicClient

_LOGGER = logging.getLogger(__name__)

ARYLIC_TO_STATE = {
    "play": MediaPlayerState.PLAYING,
    "pause": MediaPlayerState.PAUSED,
    "stop": MediaPlayerState.IDLE,
    "load": MediaPlayerState.BUFFERING,
    "none": MediaPlayerState.IDLE,
}

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.REPEAT_SET
    | MediaPlayerEntityFeature.SHUFFLE_SET
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up media player entities from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    ym_client: YandexMusicClient = data["ym_client"]
    proxy: StreamProxy = data["proxy"]

    local_ip = _get_local_ip()

    entities = []
    for device_conf in entry.data[CONF_DEVICES]:
        host = device_conf[CONF_DEVICE_HOST]
        name = device_conf[CONF_DEVICE_NAME]
        arylic = ArylicClient(host)
        entities.append(
            YandexMusic4StreamPlayer(
                ym_client=ym_client,
                arylic=arylic,
                proxy=proxy,
                local_ip=local_ip,
                device_name=name,
                device_host=host,
                entry_id=entry.entry_id,
            )
        )

    async_add_entities(entities, update_before_add=True)


def _get_local_ip() -> str:
    """Get local IP address visible to the network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()


class YandexMusic4StreamPlayer(MediaPlayerEntity):
    """Representation of a 4STREAM device playing Yandex Music."""

    _attr_has_entity_name = True

    def __init__(
        self,
        ym_client: YandexMusicClient,
        arylic: ArylicClient,
        proxy: StreamProxy,
        local_ip: str,
        device_name: str,
        device_host: str,
        entry_id: str,
    ) -> None:
        self._ym = ym_client
        self._arylic = arylic
        self._proxy = proxy
        self._local_ip = local_ip

        self._attr_name = f"{device_name} 4STREAM"
        self._attr_unique_id = f"{DOMAIN}_{device_host.replace('.', '_')}"
        self._attr_supported_features = SUPPORTED_FEATURES

        # State
        self._attr_state = MediaPlayerState.IDLE
        self._attr_volume_level: float = 0.5
        self._attr_is_volume_muted: bool = False
        self._attr_media_title: str | None = None
        self._attr_media_artist: str | None = None
        self._attr_media_album_name: str | None = None
        self._attr_media_image_url: str | None = None
        self._attr_media_duration: int | None = None
        self._attr_media_position: int | None = None
        self._attr_repeat: str = RepeatMode.OFF
        self._attr_shuffle: bool = False

        # Queue
        self._queue: list[TrackInfo] = []
        self._queue_index: int = -1
        self._poll_task: asyncio.Task | None = None
        self._advancing: bool = False

    @property
    def media_content_type(self) -> str | None:
        if self._queue:
            return MediaType.MUSIC
        return None

    async def async_added_to_hass(self) -> None:
        """Start polling when entity is added."""
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def async_will_remove_from_hass(self) -> None:
        """Stop polling when entity is removed."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        await self._arylic.close()

    async def _poll_loop(self) -> None:
        """Poll device status periodically."""
        while True:
            try:
                await self._update_status()
                self.async_write_ha_state()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("Poll error", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _update_status(self) -> None:
        """Fetch current status from device."""
        try:
            status = await self._arylic.get_player_status()
        except Exception:
            self._attr_state = MediaPlayerState.IDLE
            return

        new_state = ARYLIC_TO_STATE.get(status.status, MediaPlayerState.IDLE)
        self._attr_state = new_state
        self._attr_volume_level = status.volume / 100.0
        self._attr_is_volume_muted = status.muted

        if status.duration_ms > 0:
            self._attr_media_position = status.position_ms // 1000
            self._attr_media_duration = status.duration_ms // 1000
            self._attr_media_position_updated_at = utcnow()

        # Auto-advance: track ended
        if (
            self._queue
            and not self._advancing
            and new_state == MediaPlayerState.IDLE
            and self._queue_index >= 0
            and status.duration_ms > 0
            and status.position_ms >= status.duration_ms - 500
        ):
            self._advancing = True
            asyncio.create_task(self._advance_track())

    async def _play_track(self, track: TrackInfo) -> None:
        """Get direct URL and play track on device."""
        self._attr_media_title = track.title
        self._attr_media_artist = track.artists
        self._attr_media_album_name = track.album
        self._attr_media_image_url = track.cover_url
        self._attr_media_duration = track.duration_ms // 1000
        self._attr_media_position = 0
        self._attr_media_position_updated_at = utcnow()

        try:
            # Get fresh direct URL (TTL ~1 min)
            direct_url = await self._ym.get_direct_url(track.track_id)
        except Exception:
            _LOGGER.warning("Failed to get direct URL for track %s (%s — %s), skipping",
                            track.track_id, track.artists, track.title)
            # Skip to next track if available
            if len(self._queue) > 1:
                next_idx = self._next_queue_index()
                if next_idx is not None and next_idx != self._queue_index:
                    self._queue_index = next_idx
                    await self._play_track(self._queue[next_idx])
                    return
            self._attr_state = MediaPlayerState.IDLE
            self.async_write_ha_state()
            return

        # Register with proxy and get HTTP URL
        token = self._proxy.register_url(direct_url)
        proxy_url = self._proxy.get_proxy_url(token, self._local_ip)

        # Send to device
        await self._arylic.play_url(proxy_url)
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def _advance_track(self) -> None:
        """Advance to next track in queue."""
        try:
            next_index = self._next_queue_index()
            if next_index is None:
                self._attr_state = MediaPlayerState.IDLE
                self._clear_media_attrs()
                self.async_write_ha_state()
                return
            self._queue_index = next_index
            await self._play_track(self._queue[self._queue_index])
        except Exception:
            _LOGGER.exception("Error advancing track")
        finally:
            self._advancing = False

    def _next_queue_index(self) -> int | None:
        """Get next queue index based on repeat/shuffle mode."""
        if not self._queue:
            return None

        if self._attr_repeat == RepeatMode.ONE:
            return self._queue_index

        if self._attr_shuffle:
            candidates = list(range(len(self._queue)))
            if len(candidates) > 1:
                candidates.remove(self._queue_index)
            return random.choice(candidates)

        next_idx = self._queue_index + 1
        if next_idx >= len(self._queue):
            if self._attr_repeat == RepeatMode.ALL:
                return 0
            return None
        return next_idx

    def _prev_queue_index(self) -> int | None:
        """Get previous queue index."""
        if not self._queue:
            return None
        if self._attr_repeat == RepeatMode.ONE:
            return self._queue_index
        prev_idx = self._queue_index - 1
        if prev_idx < 0:
            if self._attr_repeat == RepeatMode.ALL:
                return len(self._queue) - 1
            return 0
        return prev_idx

    def _find_in_queue(self, track_id: str) -> int | None:
        """Find track in current queue by ID. Returns index or None."""
        for i, t in enumerate(self._queue):
            if t.track_id == track_id:
                return i
        return None

    def _clear_media_attrs(self) -> None:
        self._attr_media_title = None
        self._attr_media_artist = None
        self._attr_media_album_name = None
        self._attr_media_image_url = None
        self._attr_media_duration = None
        self._attr_media_position = None
        self._attr_media_position_updated_at = None

    # --- MediaPlayerEntity interface ---

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Implement the media browser."""
        if media_content_type is None or media_content_id is None:
            return self._build_root()

        handler = self._resolve_browse(media_content_id)
        if handler:
            return await handler()
        return self._build_root()

    def _resolve_browse(self, cid: str):
        """Route content_id to browse handler."""
        def _parse_page(s, prefix):
            rest = s[len(prefix):]
            return int(rest) if rest else 0

        simple = {
            "playlists": self._browse_playlists,
            "podcasts": self._browse_podcasts_root,
            "books": self._browse_books_root,
        }
        if cid in simple:
            return simple[cid]

        # Paginated sections
        for prefix, method in [
            ("liked", self._browse_liked),
            ("podcasts_top", self._browse_podcasts_top),
            ("podcasts_my", self._browse_podcasts_my),
            ("books_my", self._browse_books_my),
            ("books_popular", self._browse_books_popular),
        ]:
            if cid == prefix or cid.startswith(f"{prefix}:"):
                page = _parse_page(cid, f"{prefix}:")
                return lambda p=page, m=method: m(p)

        # playlist:kind or playlist:kind:page
        if cid.startswith("playlist:"):
            parts = cid.split(":")
            kind = int(parts[1])
            page = int(parts[2]) if len(parts) > 2 else 0
            return lambda: self._browse_playlist_tracks(kind, page)

        if cid.startswith("album:"):
            album_id = cid.split(":", 1)[1]
            return lambda: self._browse_album_episodes(album_id)

        if cid.startswith("search:"):
            query = cid.split(":", 1)[1]
            return lambda: self._browse_search(query)

        return None

    def _build_root(self) -> BrowseMedia:
        """Build root browse menu."""
        return BrowseMedia(
            media_class="directory",
            media_content_id="root",
            media_content_type="music",
            title="Yandex Music",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(
                    media_class="directory",
                    media_content_id="liked",
                    media_content_type="music",
                    title="Мне нравится",
                    can_play=True,
                    can_expand=True,
                    thumbnail=None,
                ),
                BrowseMedia(
                    media_class="directory",
                    media_content_id="playlists",
                    media_content_type="music",
                    title="Мои плейлисты",
                    can_play=False,
                    can_expand=True,
                    thumbnail=None,
                ),
                BrowseMedia(
                    media_class="directory",
                    media_content_id="podcasts",
                    media_content_type="podcast",
                    title="Подкасты",
                    can_play=False,
                    can_expand=True,
                    thumbnail=None,
                ),
                BrowseMedia(
                    media_class="directory",
                    media_content_id="books",
                    media_content_type="music",
                    title="Книги",
                    can_play=False,
                    can_expand=True,
                    thumbnail=None,
                ),
            ],
        )

    async def _browse_liked(self, page: int = 0) -> BrowseMedia:
        tracks, has_more = await self._ym.get_liked_tracks(page=page)
        children = [self._track_to_browse(t) for t in tracks]
        if has_more:
            children.append(self._more_item(f"liked:{page + 1}"))
        title = "Мне нравится" if page == 0 else f"Мне нравится (стр. {page + 1})"
        return BrowseMedia(
            media_class="directory",
            media_content_id=f"liked:{page}" if page else "liked",
            media_content_type="music",
            title=title,
            can_play=True,
            can_expand=True,
            children=children,
        )

    async def _browse_playlists(self) -> BrowseMedia:
        playlists = await self._ym.get_user_playlists()
        children = [
            BrowseMedia(
                media_class="playlist",
                media_content_id=f"playlist:{pl['kind']}",
                media_content_type="playlist",
                title=f"{pl['title']} ({pl['track_count']})",
                can_play=True,
                can_expand=True,
                thumbnail=None,
            )
            for pl in playlists
        ]
        return BrowseMedia(
            media_class="directory",
            media_content_id="playlists",
            media_content_type="music",
            title="Мои плейлисты",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_playlist_tracks(self, kind: int, page: int = 0) -> BrowseMedia:
        tracks, has_more = await self._ym.get_playlist_tracks(self._ym.user_id, kind, page=page)
        playlists = await self._ym.get_user_playlists()
        base_title = next(
            (pl["title"] for pl in playlists if pl["kind"] == kind),
            f"Плейлист {kind}",
        )
        children = [self._track_to_browse(t) for t in tracks]
        if has_more:
            children.append(self._more_item(f"playlist:{kind}:{page + 1}"))
        title = base_title if page == 0 else f"{base_title} (стр. {page + 1})"
        return BrowseMedia(
            media_class="playlist",
            media_content_id=f"playlist:{kind}:{page}" if page else f"playlist:{kind}",
            media_content_type="playlist",
            title=title,
            can_play=True,
            can_expand=True,
            children=children,
        )

    async def _browse_podcasts_root(self) -> BrowseMedia:
        return BrowseMedia(
            media_class="directory",
            media_content_id="podcasts",
            media_content_type="podcast",
            title="Подкасты",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(media_class="directory", media_content_id="podcasts_my",
                            media_content_type="podcast", title="Мои подкасты",
                            can_play=False, can_expand=True, thumbnail=None),
                BrowseMedia(media_class="directory", media_content_id="podcasts_top",
                            media_content_type="podcast", title="Топ подкастов",
                            can_play=False, can_expand=True, thumbnail=None),
            ],
        )

    async def _browse_podcasts_top(self, page: int = 0) -> BrowseMedia:
        podcasts, has_more = await self._ym.get_podcasts(page=page)
        children = [self._album_to_browse(p) for p in podcasts]
        if has_more:
            children.append(self._more_item(f"podcasts_top:{page + 1}"))
        title = "Топ подкастов" if page == 0 else f"Топ подкастов (стр. {page + 1})"
        return BrowseMedia(
            media_class="directory",
            media_content_id=f"podcasts_top:{page}" if page else "podcasts_top",
            media_content_type="podcast",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_podcasts_my(self, page: int = 0) -> BrowseMedia:
        podcasts, has_more = await self._ym.get_my_podcasts(page=page)
        children = [self._album_to_browse(p) for p in podcasts]
        if has_more:
            children.append(self._more_item(f"podcasts_my:{page + 1}"))
        title = "Мои подкасты" if page == 0 else f"Мои подкасты (стр. {page + 1})"
        return BrowseMedia(
            media_class="directory",
            media_content_id=f"podcasts_my:{page}" if page else "podcasts_my",
            media_content_type="podcast",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_books_root(self) -> BrowseMedia:
        return BrowseMedia(
            media_class="directory",
            media_content_id="books",
            media_content_type="music",
            title="Книги",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(media_class="directory", media_content_id="books_my",
                            media_content_type="music", title="Мои книги",
                            can_play=False, can_expand=True, thumbnail=None),
                BrowseMedia(media_class="directory", media_content_id="books_popular",
                            media_content_type="music", title="Популярные книги",
                            can_play=False, can_expand=True, thumbnail=None),
            ],
        )

    async def _browse_books_my(self, page: int = 0) -> BrowseMedia:
        books, has_more = await self._ym.get_my_audiobooks(page=page)
        children = [self._album_to_browse(b) for b in books]
        if has_more:
            children.append(self._more_item(f"books_my:{page + 1}"))
        title = "Мои книги" if page == 0 else f"Мои книги (стр. {page + 1})"
        return BrowseMedia(
            media_class="directory",
            media_content_id=f"books_my:{page}" if page else "books_my",
            media_content_type="music",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_books_popular(self, page: int = 0) -> BrowseMedia:
        books, has_more = await self._ym.search_audiobooks(page=page)
        children = [self._album_to_browse(b) for b in books]
        if has_more:
            children.append(self._more_item(f"books_popular:{page + 1}"))
        title = "Популярные книги" if page == 0 else f"Популярные книги (стр. {page + 1})"
        return BrowseMedia(
            media_class="directory",
            media_content_id=f"books_popular:{page}" if page else "books_popular",
            media_content_type="music",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_album_episodes(self, album_id: str) -> BrowseMedia:
        """Browse episodes/chapters in a podcast or audiobook."""
        title, tracks = await self._ym.get_album_episodes(album_id)
        children = [self._track_to_browse(t) for t in tracks]
        return BrowseMedia(
            media_class="album",
            media_content_id=f"album:{album_id}",
            media_content_type="music",
            title=title,
            can_play=True,
            can_expand=True,
            children=children,
        )

    async def _browse_search(self, query: str) -> BrowseMedia:
        """Browse search results."""
        tracks = await self._ym.search_tracks(query, count=20)
        children = [self._track_to_browse(t) for t in tracks]
        return BrowseMedia(
            media_class="directory",
            media_content_id=f"search:{query}",
            media_content_type="music",
            title=f"Поиск: {query}",
            can_play=True,
            can_expand=True,
            children=children,
        )

    @staticmethod
    def _more_item(content_id: str) -> BrowseMedia:
        return BrowseMedia(
            media_class="directory",
            media_content_id=content_id,
            media_content_type="music",
            title="Ещё...",
            can_play=False,
            can_expand=True,
            thumbnail=None,
        )

    @staticmethod
    def _album_to_browse(album) -> BrowseMedia:
        """Convert AlbumInfo to BrowseMedia."""
        return BrowseMedia(
            media_class="album",
            media_content_id=f"album:{album.album_id}",
            media_content_type="music",
            title=f"{album.title}" + (f" — {album.artists}" if album.artists else ""),
            can_play=True,
            can_expand=True,
            thumbnail=album.cover_url,
        )

    @staticmethod
    def _track_to_browse(track: TrackInfo) -> BrowseMedia:
        """Convert TrackInfo to BrowseMedia."""
        duration_str = ""
        if track.duration_ms:
            mins, secs = divmod(track.duration_ms // 1000, 60)
            duration_str = f" ({mins}:{secs:02d})"
        return BrowseMedia(
            media_class="track",
            media_content_id=f"track:{track.track_id}",
            media_content_type="music",
            title=f"{track.artists} — {track.title}{duration_str}",
            can_play=True,
            can_expand=False,
            thumbnail=track.cover_url,
        )

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs
    ) -> None:
        """Play media from browse, search query, or URL."""
        # Handle media_source:// URIs from HA Media panel
        if media_id.startswith("media-source://yandex_music_4stream/track:"):
            track_id = media_id.split("/track:", 1)[1]
            # If track is already in the current queue, just jump to it
            idx = self._find_in_queue(track_id)
            if idx is not None:
                self._queue_index = idx
                await self._play_track(self._queue[idx])
                return
            # Otherwise fetch track info and play as single
            tracks = await self._ym.client.tracks([track_id])
            if tracks:
                self._queue = [self._ym._to_track_info(t) for t in tracks]
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        # Handle media_source:// URIs for liked/playlist — load full context
        if media_id.startswith("media-source://yandex_music_4stream/liked"):
            tracks = await self._ym.get_liked_tracks_all()
            if tracks:
                self._queue = tracks
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        if media_id.startswith("media-source://yandex_music_4stream/playlist:"):
            kind = int(media_id.split("/playlist:", 1)[1])
            tracks = await self._ym.get_playlist_tracks_all(self._ym.user_id, kind)
            if tracks:
                self._queue = tracks
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        if media_id.startswith("media-source://yandex_music_4stream/album:"):
            album_id = media_id.split("/album:", 1)[1]
            _, tracks = await self._ym.get_album_episodes(album_id)
            if tracks:
                self._queue = tracks
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        if media_id.startswith("media-source://"):
            from homeassistant.components.media_source import async_resolve_media
            resolved = await async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = resolved.url

        # Handle direct HTTPS URLs (proxy them for 4STREAM)
        if media_id.startswith("https://"):
            token = self._proxy.register_url(media_id)
            proxy_url = self._proxy.get_proxy_url(token, self._local_ip)
            await self._arylic.play_url(proxy_url)
            self._attr_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()
            return

        # Handle direct HTTP URLs
        if media_id.startswith("http://"):
            await self._arylic.play_url(media_id)
            self._attr_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()
            return

        if media_id.startswith("track:"):
            track_id = media_id.split(":", 1)[1]
            # If track is in current queue, jump to it
            idx = self._find_in_queue(track_id)
            if idx is not None:
                self._queue_index = idx
                await self._play_track(self._queue[idx])
                return
            # Otherwise fetch and play as single
            tracks = await self._ym.client.tracks([track_id])
            if tracks:
                self._queue = [self._ym._to_track_info(t) for t in tracks]
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        if media_id == "liked":
            tracks = await self._ym.get_liked_tracks_all()
            if tracks:
                self._queue = tracks
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        if media_id.startswith("playlist:"):
            kind = int(media_id.split(":", 1)[1])
            tracks = await self._ym.get_playlist_tracks_all(self._ym.user_id, kind)
            if tracks:
                self._queue = tracks
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        if media_id.startswith("album:"):
            album_id = media_id.split(":", 1)[1]
            _, tracks = await self._ym.get_album_episodes(album_id)
            if tracks:
                self._queue = tracks
                self._queue_index = 0
                await self._play_track(self._queue[0])
            return

        # Fallback: treat as search query
        tracks = await self._ym.search_tracks(media_id, count=20)
        if not tracks:
            _LOGGER.warning("No tracks found for: %s", media_id)
            return

        self._queue = tracks
        self._queue_index = 0
        await self._play_track(self._queue[0])

    async def async_media_play(self) -> None:
        """Resume playback."""
        if self._queue and self._attr_state == MediaPlayerState.IDLE:
            # Restart current track
            if 0 <= self._queue_index < len(self._queue):
                await self._play_track(self._queue[self._queue_index])
                return
        await self._arylic.resume()

    async def async_media_pause(self) -> None:
        await self._arylic.pause()

    async def async_media_stop(self) -> None:
        await self._arylic.stop()
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_media_next_track(self) -> None:
        if not self._queue:
            return
        next_idx = self._next_queue_index()
        if next_idx is not None:
            self._queue_index = next_idx
            await self._play_track(self._queue[self._queue_index])

    async def async_media_previous_track(self) -> None:
        if not self._queue:
            return
        # If more than 5s into the track, restart current track
        if self._attr_media_position and self._attr_media_position > 5:
            await self._play_track(self._queue[self._queue_index])
            return
        prev_idx = self._prev_queue_index()
        if prev_idx is not None:
            self._queue_index = prev_idx
            await self._play_track(self._queue[self._queue_index])

    async def async_set_volume_level(self, volume: float) -> None:
        await self._arylic.set_volume(int(volume * 100))
        self._attr_volume_level = volume

    async def async_mute_volume(self, mute: bool) -> None:
        await self._arylic.mute(mute)
        self._attr_is_volume_muted = mute

    async def async_media_seek(self, position: float) -> None:
        await self._arylic.seek(int(position))
        self._attr_media_position = int(position)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        self._attr_repeat = repeat

    async def async_set_shuffle(self, shuffle: bool) -> None:
        self._attr_shuffle = shuffle
