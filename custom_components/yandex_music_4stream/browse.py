"""Shared browse logic for Yandex Music media browser.

Used by both media_source.py (HA Media panel) and media_player.py (entity browser).
Returns generic BrowseItem trees that consumers convert to their native types.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .yandex_client import AlbumInfo, TrackInfo, YandexMusicClient


@dataclass
class BrowseItem:
    """Generic browse item convertible to BrowseMedia or BrowseMediaSource."""

    identifier: str
    media_class: str
    content_type: str
    title: str
    can_play: bool
    can_expand: bool
    thumbnail: str | None = None
    children: list[BrowseItem] = field(default_factory=list)


# --- Helpers ---


def _paginated_title(base: str, page: int) -> str:
    return base if page == 0 else f"{base} (стр. {page + 1})"


def _paginated_id(base: str, page: int) -> str:
    return f"{base}:{page}" if page else base


def _more_item(identifier: str) -> BrowseItem:
    return BrowseItem(identifier, "directory", "music", "Ещё...", False, True)


def _track_item(track: TrackInfo) -> BrowseItem:
    duration_str = ""
    if track.duration_ms:
        mins, secs = divmod(track.duration_ms // 1000, 60)
        duration_str = f" ({mins}:{secs:02d})"
    return BrowseItem(
        identifier=f"track:{track.track_id}",
        media_class="track",
        content_type="music",
        title=f"{track.artists} — {track.title}{duration_str}",
        can_play=True,
        can_expand=False,
        thumbnail=track.cover_url,
    )


def _album_item(album: AlbumInfo) -> BrowseItem:
    title = album.title
    if album.artists:
        title += f" — {album.artists}"
    return BrowseItem(
        identifier=f"album:{album.album_id}",
        media_class="album",
        content_type="music",
        title=title,
        can_play=True,
        can_expand=True,
        thumbnail=album.cover_url,
    )


def _tracks_section(
    section_id: str, title: str, content_type: str,
    tracks: list[TrackInfo], has_more: bool, page: int,
    can_play: bool = False,
) -> BrowseItem:
    children = [_track_item(t) for t in tracks]
    if has_more:
        children.append(_more_item(f"{section_id}:{page + 1}"))
    return BrowseItem(
        identifier=_paginated_id(section_id, page),
        media_class="directory",
        content_type=content_type,
        title=_paginated_title(title, page),
        can_play=can_play,
        can_expand=True,
        children=children,
    )


def _albums_section(
    section_id: str, title: str, content_type: str,
    albums: list[AlbumInfo], has_more: bool, page: int,
) -> BrowseItem:
    children = [_album_item(a) for a in albums]
    if has_more:
        children.append(_more_item(f"{section_id}:{page + 1}"))
    return BrowseItem(
        identifier=_paginated_id(section_id, page),
        media_class="directory",
        content_type=content_type,
        title=_paginated_title(title, page),
        can_play=False,
        can_expand=True,
        children=children,
    )


# --- Root ---


def build_root() -> BrowseItem:
    return BrowseItem(
        identifier="",
        media_class="directory",
        content_type="music",
        title="Yandex Music",
        can_play=False,
        can_expand=True,
        children=[
            BrowseItem("liked", "directory", "music", "Мне нравится", True, True),
            BrowseItem("playlists", "directory", "music", "Мои плейлисты", False, True),
            BrowseItem("music", "directory", "music", "Музыка", False, True),
            BrowseItem("podcasts", "directory", "podcast", "Подкасты", False, True),
            BrowseItem("books", "directory", "music", "Книги", False, True),
        ],
    )


# --- Liked ---


async def browse_liked(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    tracks, has_more = await ym.get_liked_tracks(page=page)
    return _tracks_section("liked", "Мне нравится", "music", tracks, has_more, page, can_play=True)


# --- Playlists ---


async def browse_playlists(ym: YandexMusicClient) -> BrowseItem:
    playlists = await ym.get_user_playlists()
    children = [
        BrowseItem(
            identifier=f"playlist:{pl['kind']}",
            media_class="playlist",
            content_type="playlist",
            title=f"{pl['title']} ({pl['track_count']})",
            can_play=True,
            can_expand=True,
        )
        for pl in playlists
    ]
    return BrowseItem("playlists", "directory", "music", "Мои плейлисты", False, True, children=children)


async def browse_playlist_tracks(ym: YandexMusicClient, kind: int, page: int = 0) -> BrowseItem:
    tracks, has_more = await ym.get_playlist_tracks(ym.user_id, kind, page=page)
    # TODO: cache playlist title to avoid extra API call on pagination
    playlists = await ym.get_user_playlists()
    base_title = next(
        (pl["title"] for pl in playlists if pl["kind"] == kind),
        f"Плейлист {kind}",
    )
    children = [_track_item(t) for t in tracks]
    if has_more:
        children.append(_more_item(f"playlist:{kind}:{page + 1}"))
    return BrowseItem(
        identifier=f"playlist:{kind}:{page}" if page else f"playlist:{kind}",
        media_class="playlist",
        content_type="playlist",
        title=_paginated_title(base_title, page),
        can_play=True,
        can_expand=True,
        children=children,
    )


# --- Music ---


def browse_music_root() -> BrowseItem:
    return BrowseItem(
        "music", "directory", "music", "Музыка", False, True,
        children=[
            BrowseItem("music_chart", "directory", "music", "Чарт", False, True),
            BrowseItem("music_new", "directory", "music", "Новые релизы", False, True),
            BrowseItem("music_albums", "directory", "music", "Мои альбомы", False, True),
        ],
    )


async def browse_music_chart(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    tracks, has_more = await ym.get_chart_tracks(page=page)
    return _tracks_section("music_chart", "Чарт", "music", tracks, has_more, page, can_play=True)


async def browse_music_new(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    albums, has_more = await ym.get_new_releases(page=page)
    return _albums_section("music_new", "Новые релизы", "music", albums, has_more, page)


async def browse_music_albums(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    albums, has_more = await ym.get_my_albums(page=page)
    return _albums_section("music_albums", "Мои альбомы", "music", albums, has_more, page)


# --- Podcasts ---


def browse_podcasts_root() -> BrowseItem:
    return BrowseItem(
        "podcasts", "directory", "podcast", "Подкасты", False, True,
        children=[
            BrowseItem("podcasts_new", "directory", "podcast", "Новые эпизоды", False, True),
            BrowseItem("podcasts_my", "directory", "podcast", "Мои подкасты", False, True),
            BrowseItem("podcasts_top", "directory", "podcast", "Топ подкастов", False, True),
        ],
    )


async def browse_podcasts_new_episodes(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    tracks, has_more = await ym.get_new_podcast_episodes(page=page)
    return _tracks_section("podcasts_new", "Новые эпизоды", "podcast", tracks, has_more, page)


async def browse_podcasts_top(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    podcasts, has_more = await ym.get_podcasts(page=page)
    return _albums_section("podcasts_top", "Топ подкастов", "podcast", podcasts, has_more, page)


async def browse_podcasts_my(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    podcasts, has_more = await ym.get_my_podcasts(page=page)
    return _albums_section("podcasts_my", "Мои подкасты", "podcast", podcasts, has_more, page)


# --- Books ---


def browse_books_root() -> BrowseItem:
    return BrowseItem(
        "books", "directory", "music", "Книги", False, True,
        children=[
            BrowseItem("books_my", "directory", "music", "Мои книги", False, True),
            BrowseItem("books_popular", "directory", "music", "Популярные книги", False, True),
        ],
    )


async def browse_books_my(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    books, has_more = await ym.get_my_audiobooks(page=page)
    return _albums_section("books_my", "Мои книги", "music", books, has_more, page)


async def browse_books_popular(ym: YandexMusicClient, page: int = 0) -> BrowseItem:
    books, has_more = await ym.search_audiobooks(page=page)
    return _albums_section("books_popular", "Популярные книги", "music", books, has_more, page)


# --- Album / Search ---


async def browse_album(ym: YandexMusicClient, album_id: str) -> BrowseItem:
    title, tracks = await ym.get_album_tracks(album_id)
    children = [_track_item(t) for t in tracks]
    return BrowseItem(
        identifier=f"album:{album_id}",
        media_class="album",
        content_type="music",
        title=title,
        can_play=True,
        can_expand=True,
        children=children,
    )


async def browse_search(ym: YandexMusicClient, query: str) -> BrowseItem:
    tracks = await ym.search_tracks(query, count=20)
    children = [_track_item(t) for t in tracks]
    return BrowseItem(
        identifier=f"search:{query}",
        media_class="directory",
        content_type="music",
        title=f"Поиск: {query}",
        can_play=True,
        can_expand=True,
        children=children,
    )


# --- Routing ---


def _parse_page(identifier: str, prefix: str) -> int:
    """Parse page number from 'prefix:N' identifier."""
    if not identifier.startswith(f"{prefix}:"):
        return 0
    rest = identifier[len(prefix) + 1:]
    return int(rest) if rest else 0


_PAGINATED_SECTIONS = [
    ("liked", browse_liked),
    ("music_chart", browse_music_chart),
    ("music_new", browse_music_new),
    ("music_albums", browse_music_albums),
    ("podcasts_new", browse_podcasts_new_episodes),
    ("podcasts_top", browse_podcasts_top),
    ("podcasts_my", browse_podcasts_my),
    ("books_my", browse_books_my),
    ("books_popular", browse_books_popular),
]

_STATIC_ROOTS = {
    "playlists": browse_playlists,
    "music": lambda _ym: browse_music_root(),
    "podcasts": lambda _ym: browse_podcasts_root(),
    "books": lambda _ym: browse_books_root(),
}


async def resolve_browse(ym: YandexMusicClient, identifier: str) -> BrowseItem | None:
    """Route identifier to browse handler and return result."""
    if not identifier:
        return build_root()

    # Static root sections (some sync, some async)
    if identifier in _STATIC_ROOTS:
        result = _STATIC_ROOTS[identifier](ym)
        if hasattr(result, "__await__"):
            return await result
        return result

    # Paginated sections: "section" or "section:page"
    for prefix, handler in _PAGINATED_SECTIONS:
        if identifier == prefix or identifier.startswith(f"{prefix}:"):
            page = _parse_page(identifier, prefix)
            return await handler(ym, page)

    # Parameterized: "playlist:kind" or "playlist:kind:page"
    if identifier.startswith("playlist:"):
        parts = identifier.split(":")
        kind = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
        return await browse_playlist_tracks(ym, kind, page)

    if identifier.startswith("album:"):
        album_id = identifier.split(":", 1)[1]
        return await browse_album(ym, album_id)

    if identifier.startswith("search:"):
        query = identifier.split(":", 1)[1]
        return await browse_search(ym, query)

    return None
