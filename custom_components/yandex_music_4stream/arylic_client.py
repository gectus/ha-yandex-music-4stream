"""Arylic/4STREAM HTTP API client."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """4STREAM device information."""

    name: str
    model: str
    firmware: str
    uuid: str
    ip: str


@dataclass
class PlayerStatus:
    """Current playback status."""

    status: str  # "play", "pause", "stop", etc.
    title: str
    artist: str
    album: str
    volume: int  # 0-100
    muted: bool
    position_ms: int
    duration_ms: int


class ArylicClient:
    """Async HTTP client for Arylic/4STREAM devices."""

    def __init__(self, host: str, port: int = 80) -> None:
        self._host = host
        self._port = port
        self._base_url = f"http://{host}:{port}"
        self._session: aiohttp.ClientSession | None = None

    @property
    def host(self) -> str:
        return self._host

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _command(self, cmd: str) -> str:
        """Send command to device and return response text."""
        url = f"{self._base_url}/httpapi.asp?command={cmd}"
        _LOGGER.debug("Arylic command: %s", url)
        session = self._get_session()
        async with session.get(url) as resp:
            text = await resp.text()
            _LOGGER.debug("Arylic response: %s", text[:200])
            return text

    # --- Playback ---

    async def play_url(self, url: str) -> None:
        """Play audio from URL. Arylic devices expect raw unencoded URLs."""
        await self._command(f"setPlayerCmd:play:{url}")

    async def pause(self) -> None:
        await self._command("setPlayerCmd:pause")

    async def resume(self) -> None:
        await self._command("setPlayerCmd:resume")

    async def stop(self) -> None:
        await self._command("setPlayerCmd:stop")

    async def toggle(self) -> None:
        """Toggle play/pause."""
        await self._command("setPlayerCmd:onepause")

    async def next_track(self) -> None:
        await self._command("setPlayerCmd:next")

    async def prev_track(self) -> None:
        await self._command("setPlayerCmd:prev")

    async def seek(self, position_seconds: int) -> None:
        await self._command(f"setPlayerCmd:seek:{position_seconds}")

    # --- Volume ---

    async def set_volume(self, level: int) -> None:
        """Set volume (0-100)."""
        level = max(0, min(100, level))
        await self._command(f"setPlayerCmd:vol:{level}")

    async def mute(self, muted: bool) -> None:
        await self._command(f"setPlayerCmd:mute:{1 if muted else 0}")

    # --- Status ---

    async def get_player_status(self) -> PlayerStatus:
        """Get current playback status."""
        text = await self._command("getPlayerStatus")
        data = json.loads(text)

        return PlayerStatus(
            status=data.get("status", "stop"),
            title=data.get("Title1", ""),
            artist=data.get("Artist1", ""),
            album=data.get("Album1", ""),
            volume=int(data.get("vol", 0)),
            muted=data.get("mute", "0") == "1",
            position_ms=int(data.get("curpos", 0)),
            duration_ms=int(data.get("totlen", 0)),
        )

    async def get_device_info(self) -> DeviceInfo:
        """Get device information."""
        text = await self._command("getStatusEx")
        data = json.loads(text)

        return DeviceInfo(
            name=data.get("DeviceName", "Unknown"),
            model=data.get("hardware", "Unknown"),
            firmware=data.get("firmware", "Unknown"),
            uuid=data.get("uuid", ""),
            ip=self._host,
        )

    # --- Multiroom ---

    async def join_group(self, master_ip: str) -> None:
        """Join a multiroom group as guest."""
        await self._command(
            f"ConnectMasterAp:JoinGroupMaster:eth{master_ip}:wifi0.0.0.0"
        )

    async def get_slaves(self) -> list[dict]:
        """Get list of slave devices in multiroom group."""
        text = await self._command("multiroom:getSlaveList")
        data = json.loads(text)
        return data.get("slave_list", [])

    async def kick_slave(self, ip: str) -> None:
        await self._command(f"multiroom:SlaveKickout:{ip}")

    async def ungroup(self) -> None:
        """Disband the multiroom group."""
        await self._command("multiroom:Ungroup")
