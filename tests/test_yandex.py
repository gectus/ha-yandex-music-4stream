"""Manual test for Yandex Music client."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

from yandex_music_4stream.yandex_client import YandexMusicClient


async def main():
    token = os.environ.get("YANDEX_MUSIC_TOKEN")
    if not token:
        print("Set YANDEX_MUSIC_TOKEN environment variable")
        print("Example: YANDEX_MUSIC_TOKEN=your_token python tests/test_yandex.py")
        sys.exit(1)

    client = YandexMusicClient(token)

    # 1. Authenticate
    print("=== Authentication ===")
    name = await client.authenticate()
    print(f"Logged in as: {name}")

    # 2. Search
    print("\n=== Search: 'Кино Группа крови' ===")
    tracks = await client.search_tracks("Кино Группа крови", count=5)
    for t in tracks:
        print(f"  [{t.track_id}] {t.artists} — {t.title} ({t.duration_ms // 1000}s)")

    if not tracks:
        print("No tracks found, skipping direct URL test")
        return

    # 3. Get direct URL
    first = tracks[0]
    print(f"\n=== Direct URL for: {first.artists} — {first.title} ===")
    url = await client.get_direct_url(first.track_id)
    print(f"  URL: {url[:100]}...")

    # 4. Liked tracks
    print("\n=== Liked tracks (first 5) ===")
    liked = await client.get_liked_tracks(count=5)
    for t in liked:
        print(f"  {t.artists} — {t.title}")

    # 5. Playlists
    print("\n=== User playlists ===")
    playlists = await client.get_user_playlists()
    for pl in playlists:
        print(f"  [{pl['kind']}] {pl['title']} ({pl['track_count']} tracks)")

    print("\nAll tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
