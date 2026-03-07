"""Manual test for Arylic/4STREAM client."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

from yandex_music_4stream.arylic_client import ArylicClient


async def main():
    host = os.environ.get("ARYLIC_HOST")
    if not host:
        print("Set ARYLIC_HOST environment variable (IP address of 4STREAM device)")
        print("Example: ARYLIC_HOST=192.168.1.100 python tests/test_arylic.py")
        sys.exit(1)

    client = ArylicClient(host)

    # 1. Device info
    print("=== Device Info ===")
    info = await client.get_device_info()
    print(f"  Name: {info.name}")
    print(f"  Model: {info.model}")
    print(f"  Firmware: {info.firmware}")
    print(f"  UUID: {info.uuid}")

    # 2. Player status
    print("\n=== Player Status ===")
    status = await client.get_player_status()
    print(f"  Status: {status.status}")
    print(f"  Volume: {status.volume}")
    print(f"  Muted: {status.muted}")
    print(f"  Title: {status.title}")
    print(f"  Position: {status.position_ms}ms / {status.duration_ms}ms")

    # 3. Volume test
    print("\n=== Volume Test ===")
    print(f"  Current volume: {status.volume}")
    await client.set_volume(30)
    status = await client.get_player_status()
    print(f"  Set to 30: {status.volume}")

    print("\nBasic tests passed!")
    print("\nTo test playback, run test_e2e.py with both tokens set.")


if __name__ == "__main__":
    asyncio.run(main())
