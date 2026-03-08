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
from .browse import BrowseItem, resolve_browse
from .const import (
    CONF_DEVICES,
    CONF_DEVICE_HOST,
    CONF_DEVICE_NAME,
    CONSECUTIVE_POLL_ERRORS_THRESHOLD,
    DOMAIN,
    MAX_SKIP_ON_ERROR,
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

MEDIA_SOURCE_PREFIX = "media-source://yandex_music_4stream/"


def _get_local_ip() -> str:
    """Get local IP address visible to the network."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up media player entities from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    ym_client: YandexMusicClient = data["ym_client"]
    proxy: StreamProxy = data["proxy"]

    local_ip = await hass.async_add_executor_job(_get_local_ip)

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
            )
        )

    async_add_entities(entities)


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

    # --- Lifecycle ---

    async def async_added_to_hass(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def async_will_remove_from_hass(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        await self._arylic.close()

    # --- Polling ---

    async def _poll_loop(self) -> None:
        consecutive_errors = 0
        while True:
            try:
                await self._update_status()
                if consecutive_errors > 0:
                    consecutive_errors = 0
                    self._attr_available = True
                self.async_write_ha_state()
            except asyncio.CancelledError:
                return
            except Exception:
                consecutive_errors += 1
                if consecutive_errors >= CONSECUTIVE_POLL_ERRORS_THRESHOLD:
                    if self._attr_available:
                        _LOGGER.warning(
                            "Device %s unavailable after %d consecutive poll errors",
                            self._attr_name, consecutive_errors,
                        )
                        self._attr_available = False
                        self.async_write_ha_state()
                else:
                    _LOGGER.debug("Poll error", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _update_status(self) -> None:
        status = await self._arylic.get_player_status()

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

    # --- Queue management ---

    async def _play_track(self, track: TrackInfo, _skip_count: int = 0) -> None:
        """Get direct URL and play track on device."""
        self._attr_media_title = track.title
        self._attr_media_artist = track.artists
        self._attr_media_album_name = track.album
        self._attr_media_image_url = track.cover_url
        self._attr_media_duration = track.duration_ms // 1000
        self._attr_media_position = 0
        self._attr_media_position_updated_at = utcnow()

        try:
            direct_url = await self._ym.get_direct_url(track.track_id)
        except Exception:
            _LOGGER.warning(
                "Failed to get URL for track %s (%s — %s), skipping",
                track.track_id, track.artists, track.title,
            )
            if _skip_count < MAX_SKIP_ON_ERROR and len(self._queue) > 1:
                next_idx = self._next_queue_index()
                if next_idx is not None and next_idx != self._queue_index:
                    self._queue_index = next_idx
                    await self._play_track(self._queue[next_idx], _skip_count + 1)
                    return
            self._attr_state = MediaPlayerState.IDLE
            self.async_write_ha_state()
            return

        token = self._proxy.register_url(direct_url)
        proxy_url = self._proxy.get_proxy_url(token, self._local_ip)

        await self._arylic.play_url(proxy_url)
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def _load_queue_and_play(self, tracks: list[TrackInfo]) -> None:
        """Load tracks into queue and start playback."""
        if not tracks:
            _LOGGER.warning("No tracks to play")
            return
        self._queue = tracks
        self._queue_index = 0
        await self._play_track(self._queue[0])

    async def _advance_track(self) -> None:
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
        if not self._queue:
            return None
        if self._attr_repeat == RepeatMode.ONE:
            return self._queue_index
        prev_idx = self._queue_index - 1
        if prev_idx < 0:
            if self._attr_repeat == RepeatMode.ALL:
                return len(self._queue) - 1
            return None
        return prev_idx

    def _find_in_queue(self, track_id: str) -> int | None:
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

    # --- Browse media ---

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        identifier = media_content_id or ""
        result = await resolve_browse(self._ym, identifier)
        if result:
            return self._to_browse(result)
        root = await resolve_browse(self._ym, "")
        return self._to_browse(root or BrowseItem("", "directory", "music", "Yandex Music", False, True))

    @staticmethod
    def _to_browse(item: BrowseItem) -> BrowseMedia:
        """Convert BrowseItem tree to BrowseMedia tree."""
        children = [YandexMusic4StreamPlayer._to_browse(c) for c in item.children] if item.children else None
        return BrowseMedia(
            media_class=item.media_class,
            media_content_id=item.identifier,
            media_content_type=item.content_type,
            title=item.title,
            can_play=item.can_play,
            can_expand=item.can_expand,
            thumbnail=item.thumbnail,
            children=children,
        )

    # --- Play media ---

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs
    ) -> None:
        """Play media from browse, search query, or URL."""
        # Normalize: strip media-source prefix
        if media_id.startswith(MEDIA_SOURCE_PREFIX):
            media_id = media_id[len(MEDIA_SOURCE_PREFIX):]
        elif media_id.startswith("media-source://"):
            # Other media sources — resolve via HA
            from homeassistant.components.media_source import async_resolve_media
            resolved = await async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = resolved.url

        # Direct URLs
        if media_id.startswith("https://"):
            token = self._proxy.register_url(media_id)
            proxy_url = self._proxy.get_proxy_url(token, self._local_ip)
            await self._arylic.play_url(proxy_url)
            self._attr_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()
            return

        if media_id.startswith("http://"):
            await self._arylic.play_url(media_id)
            self._attr_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()
            return

        # Single track
        if media_id.startswith("track:"):
            track_id = media_id.split(":", 1)[1]
            idx = self._find_in_queue(track_id)
            if idx is not None:
                self._queue_index = idx
                await self._play_track(self._queue[idx])
                return
            tracks = await self._ym.get_tracks_by_ids([track_id])
            await self._load_queue_and_play(tracks)
            return

        # Collections
        tracks = await self._resolve_collection(media_id)
        if tracks is not None:
            await self._load_queue_and_play(tracks)
            return

        # Fallback: treat as search query
        tracks = await self._ym.search_tracks(media_id, count=20)
        await self._load_queue_and_play(tracks)

    async def _resolve_collection(self, media_id: str) -> list[TrackInfo] | None:
        """Resolve a collection identifier to a list of tracks for queue playback."""
        if media_id == "liked" or media_id.startswith("liked:"):
            return await self._ym.get_liked_tracks_all()

        if media_id.startswith("playlist:"):
            kind = int(media_id.split(":")[1])
            return await self._ym.get_playlist_tracks_all(self._ym.user_id, kind)

        if media_id == "music_chart" or media_id.startswith("music_chart:"):
            return await self._ym.get_chart_tracks_all()

        if media_id.startswith("album:"):
            album_id = media_id.split(":", 1)[1]
            _, tracks = await self._ym.get_album_tracks(album_id)
            return tracks

        return None

    # --- Playback controls ---

    async def async_media_play(self) -> None:
        if self._queue and self._attr_state == MediaPlayerState.IDLE:
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
