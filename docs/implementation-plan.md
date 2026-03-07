# Implementation Plan: Yandex Music for 4STREAM

## Overview

Custom integration for Home Assistant that creates `media_player` entities for 4STREAM (Arylic) audio devices and enables streaming music from Yandex Music.

## Architecture

```
Home Assistant
  custom_components/yandex_music_4stream/
    __init__.py            # Integration setup
    manifest.json          # Metadata and dependencies
    config_flow.py         # UI configuration
    const.py               # Constants
    media_player.py        # MediaPlayerEntity implementation
    yandex_client.py       # Yandex Music API wrapper
    arylic_client.py       # Arylic/4STREAM HTTP API client
    strings.json           # UI strings
    translations/
      en.json
      ru.json
```

### Data Flow

```
User selects track in HA UI
        |
        v
yandex_client.get_download_info(track_id)
        |
        v
download_info.get_direct_link()  -->  direct URL (mp3/aac, TTL ~1 min)
        |
        v
arylic_client.play_url(direct_url)
        |
        v
4STREAM device plays audio
        |
        v
Polling getPlayerStatus every 5s  -->  update HA entity state
        |
        v
Track ends  -->  next track from internal queue
```

---

## Stages

### Stage 1: API Clients

#### 1.1 Yandex Music Client (`yandex_client.py`)

Wrapper around `yandex-music` Python library.

**Responsibilities:**
- Authentication via token
- Track/album/playlist search
- Get direct streaming URL for a track
- Access user library: liked tracks, playlists, personal mixes
- Station/radio playback support

**Key implementation details:**
- Direct link has a TTL of ~1 minute — must be fetched just-in-time before playback
- Without Yandex Plus subscription only 30-second previews are available
- Use async variant (`ClientAsync`) for HA compatibility
- Prefer `mp3` codec for maximum compatibility with 4STREAM devices

**Core methods:**
```python
class YandexMusicClient:
    async def authenticate(token: str) -> bool
    async def search(query: str, type: str) -> SearchResult
    async def get_track_direct_url(track_id: str) -> str
    async def get_user_playlists() -> list[Playlist]
    async def get_playlist_tracks(playlist_id: str) -> list[Track]
    async def get_liked_tracks() -> list[Track]
    async def get_station_tracks(station_id: str) -> list[Track]
```

#### 1.2 Arylic/4STREAM Client (`arylic_client.py`)

HTTP client for Arylic devices using their HTTP API (`/httpapi.asp`).

**API reference:** https://developer.arylic.com/httpapi/

**Core methods:**
```python
class ArylicClient:
    def __init__(host: str, port: int = 80)

    # Playback
    async def play_url(url: str) -> None          # setPlayerCmd:play:<url>
    async def pause() -> None                      # setPlayerCmd:pause
    async def resume() -> None                     # setPlayerCmd:resume
    async def stop() -> None                       # setPlayerCmd:stop
    async def next_track() -> None                 # setPlayerCmd:next
    async def prev_track() -> None                 # setPlayerCmd:prev
    async def seek(position: int) -> None          # setPlayerCmd:seek:<pos>

    # Volume
    async def set_volume(level: int) -> None       # setPlayerCmd:vol:<0-100>
    async def mute(flag: bool) -> None             # setPlayerCmd:mute:<0|1>

    # Status
    async def get_player_status() -> dict          # getPlayerStatus
    async def get_device_info() -> dict            # getStatusEx

    # Multiroom
    async def join_group(master_ip: str) -> None   # ConnectMasterAp:JoinGroupMaster
    async def get_slaves() -> list[dict]           # multiroom:getSlaveList
    async def kick_slave(ip: str) -> None          # multiroom:SlaveKickout
    async def ungroup() -> None                    # multiroom:Ungroup
```

### Stage 2: Media Player Entity

#### 2.1 `media_player.py` — `MediaPlayerEntity` implementation

**Supported features:**
- `PLAY`, `PAUSE`, `STOP`
- `NEXT_TRACK`, `PREVIOUS_TRACK`
- `VOLUME_SET`, `VOLUME_MUTE`, `VOLUME_STEP`
- `SEEK`
- `BROWSE_MEDIA`, `PLAY_MEDIA`
- `GROUPING` (multiroom)

**Entity attributes:**
- `media_title` — track name
- `media_artist` — artist name
- `media_album_name` — album name
- `media_image_url` — cover art from Yandex Music
- `media_duration` — track length
- `media_position` — current position
- `volume_level` — 0.0–1.0
- `is_volume_muted` — mute state

#### 2.2 Playback Queue

Since direct links expire in ~1 minute, the integration maintains an internal queue of track IDs (not URLs).

```
Internal queue: [track_id_1, track_id_2, track_id_3, ...]
                     ^
                current_index

On play:
  1. Take track_id at current_index
  2. Fetch direct URL from Yandex Music (just-in-time)
  3. Send URL to 4STREAM device
  4. Poll device status
  5. When track ends → current_index++ → repeat from step 1
```

**Queue modes:**
- Normal (play through, stop at end)
- Repeat all (loop playlist)
- Repeat one (loop current track)
- Shuffle (randomize order)

### Stage 3: Config Flow (UI Setup)

#### 3.1 `config_flow.py`

**Step 1 — Yandex Music authentication:**
- User enters Yandex Music OAuth token
- Integration validates the token by calling the API
- Show account name on success

**Step 2 — Add 4STREAM devices:**
- Option A: Auto-discovery via SSDP/mDNS (Arylic devices advertise themselves)
- Option B: Manual IP address entry
- Validate by calling `getStatusEx` on the device
- Show device name and model

**Step 3 — Confirmation:**
- Summary of configured account and devices
- Create entity per device

**Options flow (post-setup):**
- Add/remove devices
- Change Yandex Music token
- Configure polling interval

### Stage 4: Media Browser

#### 4.1 `async_browse_media()` implementation

Navigation tree:
```
Yandex Music
  |-- Search
  |-- Liked Tracks
  |-- My Playlists
  |   |-- Playlist 1
  |   |-- Playlist 2
  |   +-- ...
  |-- Personal Mixes (Moя Волна, etc.)
  |   |-- Daily playlist
  |   |-- Premiere
  |   +-- ...
  |-- Radio Stations
  |   |-- By genre
  |   |-- By mood
  |   +-- ...
  +-- New Releases
```

Each level returns `BrowseMedia` objects with:
- `media_content_type`: `music`, `playlist`, `album`, `artist`, `track`
- `media_content_id`: Yandex Music ID
- `title`, `thumbnail`
- `can_play`, `can_expand`

### Stage 5: Advanced Playback

- Infinite radio mode via Yandex Music stations API
- Crossfade between tracks (if supported by device)
- Gapless playback
- "Play similar" — start radio based on current track
- Resume playback after HA restart (persist queue state)

### Stage 6: Multiroom (Optional)

- Group/ungroup devices through HA UI
- Synchronized playback across multiple 4STREAM speakers
- Per-device volume control within a group
- Expose groups as separate media_player entities

---

## Dependencies

```json
{
  "domain": "yandex_music_4stream",
  "name": "Yandex Music for 4STREAM",
  "version": "0.1.0",
  "requirements": ["yandex-music>=2.0.0"],
  "dependencies": [],
  "codeowners": ["@gectus"],
  "iot_class": "local_polling",
  "config_flow": true
}
```

## Risks and Limitations

| Risk | Impact | Mitigation |
|---|---|---|
| Direct link TTL ~1 min | Cannot pre-build URL playlists | Fetch URL just-in-time before each track |
| Unofficial Yandex Music API | May break without notice | Pin library version, monitor releases |
| Yandex Plus required | Only 30s previews without subscription | Document requirement, validate on setup |
| Codec compatibility | 4STREAM may not support all formats | Default to mp3, test aac support |
| Rate limiting | Yandex may throttle frequent requests | Add request throttling, cache where possible |
| Network latency | Delay between tracks while fetching URL | Pre-fetch next track URL ~10s before current track ends |

## Implementation Order

```
Stage 1.1  Yandex Music client     ← start here
Stage 1.2  Arylic HTTP client      ← can be done in parallel
Stage 2    Media Player entity     ← core integration
Stage 3    Config Flow             ← UI setup
Stage 4    Media Browser           ← enhanced UX
Stage 5    Advanced playback       ← polish
Stage 6    Multiroom               ← optional
```

Minimum viable product = Stages 1 + 2 + 3.
