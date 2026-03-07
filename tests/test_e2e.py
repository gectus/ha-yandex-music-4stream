"""End-to-end test: search on Yandex Music, play on 4STREAM device."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

from yandex_music_4stream.yandex_client import YandexMusicClient
from yandex_music_4stream.arylic_client import ArylicClient


async def main():
    token = os.environ.get("YANDEX_MUSIC_TOKEN")
    host = os.environ.get("ARYLIC_HOST")

    if not token or not host:
        print("Required environment variables:")
        print("  YANDEX_MUSIC_TOKEN - Yandex Music OAuth token")
        print("  ARYLIC_HOST - IP address of 4STREAM device")
        print()
        print("Example:")
        print("  YANDEX_MUSIC_TOKEN=xxx ARYLIC_HOST=192.168.1.100 python tests/test_e2e.py")
        sys.exit(1)

    query = sys.argv[1] if len(sys.argv) > 1 else "Кино Группа крови"

    # 1. Authenticate Yandex Music
    ym = YandexMusicClient(token)
    name = await ym.authenticate()
    print(f"Yandex Music: logged in as {name}")

    # 2. Check 4STREAM device
    arylic = ArylicClient(host)
    info = await arylic.get_device_info()
    print(f"4STREAM: {info.name} ({info.model})")

    # 3. Search for track
    print(f"\nSearching: '{query}'")
    tracks = await ym.search_tracks(query, count=5)
    if not tracks:
        print("No tracks found!")
        sys.exit(1)

    for i, t in enumerate(tracks):
        print(f"  {i + 1}. {t.artists} — {t.title} ({t.duration_ms // 1000}s)")

    # 4. Get direct URL for first track
    track = tracks[0]
    print(f"\nGetting stream URL for: {track.artists} — {track.title}")
    url = await ym.get_direct_url(track.track_id)
    print(f"URL obtained ({len(url)} chars)")

    # 5. Play on device
    print(f"Sending to {info.name}...")
    await arylic.play_url(url)
    print("Play command sent!")

    # 6. Wait for playback to start (device may buffer first)
    print("Waiting for playback to start...")
    for attempt in range(10):
        await asyncio.sleep(2)
        status = await arylic.get_player_status()
        print(f"  [{attempt + 1}] status={status.status}")
        if status.status == "play":
            break
    else:
        print(f"\nDevice did not start playing (last status: {status.status}).")
        print("Check device and network connectivity.")
        sys.exit(1)

    print(f"\nSUCCESS! Music is playing on {info.name}.")
    print(f"Volume: {status.volume}")
    print("Press Ctrl+C to stop, or wait 15 seconds...")
    try:
        await asyncio.sleep(15)
    except KeyboardInterrupt:
        pass
    await arylic.stop()
    print("Stopped.")


if __name__ == "__main__":
    asyncio.run(main())
