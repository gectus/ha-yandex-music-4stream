"""Test: Yandex Music -> proxy -> 4STREAM device."""

import asyncio
import sys
import os
import socket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

from yandex_music_4stream.yandex_client import YandexMusicClient
from yandex_music_4stream.arylic_client import ArylicClient
from yandex_music_4stream.proxy import StreamProxy


def get_local_ip() -> str:
    """Get local IP address visible to the network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()


async def main():
    token = os.environ.get("YANDEX_MUSIC_TOKEN")
    host = os.environ.get("ARYLIC_HOST")

    if not token or not host:
        print("Required: YANDEX_MUSIC_TOKEN and ARYLIC_HOST")
        sys.exit(1)

    query = sys.argv[1] if len(sys.argv) > 1 else "Кино Группа крови"
    local_ip = get_local_ip()
    print(f"Local IP: {local_ip}")

    # 1. Start proxy
    proxy = StreamProxy(port=8479)
    await proxy.start()
    print(f"Proxy running on {local_ip}:{proxy.port}")

    try:
        # 2. Auth Yandex Music
        ym = YandexMusicClient(token)
        name = await ym.authenticate()
        print(f"Yandex Music: {name}")

        # 3. Check device
        arylic = ArylicClient(host)
        info = await arylic.get_device_info()
        print(f"4STREAM: {info.name} ({info.model})")

        # 4. Search
        print(f"\nSearching: '{query}'")
        tracks = await ym.search_tracks(query, count=5)
        if not tracks:
            print("No tracks found!")
            return

        for i, t in enumerate(tracks):
            print(f"  {i + 1}. {t.artists} — {t.title} ({t.duration_ms // 1000}s)")

        # 5. Get direct URL and register with proxy
        track = tracks[0]
        print(f"\nGetting stream URL for: {track.artists} — {track.title}")
        direct_url = await ym.get_direct_url(track.track_id)
        print(f"Direct URL: {len(direct_url)} chars (HTTPS)")

        proxy_token = proxy.register_url(direct_url)
        proxy_url = proxy.get_proxy_url(proxy_token, local_ip)
        print(f"Proxy URL: {proxy_url}")

        # 6. Play via proxy
        print(f"\nSending to {info.name}...")
        await arylic.play_url(proxy_url)
        print("Play command sent!")

        # 7. Wait for playback
        print("Waiting for playback...")
        for attempt in range(10):
            await asyncio.sleep(2)
            status = await arylic.get_player_status()
            print(f"  [{attempt + 1}] status={status.status}")
            if status.status == "play":
                break

        if status.status == "play":
            print(f"\nSUCCESS! Playing: {track.artists} — {track.title}")
            print(f"Volume: {status.volume}")
            print("Listening for 20 seconds... (Ctrl+C to stop)")
            try:
                await asyncio.sleep(20)
            except KeyboardInterrupt:
                pass
            await arylic.stop()
            print("Stopped.")
        else:
            print(f"\nFailed: status={status.status}")
    finally:
        await proxy.stop()


if __name__ == "__main__":
    asyncio.run(main())
