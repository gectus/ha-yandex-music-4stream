"""Local HTTP proxy for streaming HTTPS content to 4STREAM devices."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass

import aiohttp
from aiohttp import web

_LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024  # 64KB
TOKEN_TTL = 300  # 5 minutes - proxy token lifetime


@dataclass
class StreamEntry:
    """A registered stream URL with expiration."""

    url: str
    created: float


class StreamProxy:
    """HTTP proxy server that streams HTTPS content over HTTP."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8479) -> None:
        self._host = host
        self._port = port
        self._streams: dict[str, StreamEntry] = {}
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    @property
    def port(self) -> int:
        return self._port

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def register_url(self, url: str) -> str:
        """Register an HTTPS URL and return a proxy token."""
        now = time.time()
        expired = [k for k, v in self._streams.items() if now - v.created > TOKEN_TTL]
        for k in expired:
            del self._streams[k]

        token = secrets.token_hex(16)
        self._streams[token] = StreamEntry(url=url, created=now)
        return token

    def get_proxy_url(self, token: str, proxy_host: str) -> str:
        """Get the HTTP URL that 4STREAM device should use."""
        return f"http://{proxy_host}:{self._port}/stream/{token}.mp3"

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        """Handle stream request from 4STREAM device."""
        token = request.match_info["token"]
        if token.endswith(".mp3"):
            token = token[:-4]

        entry = self._streams.get(token)
        if not entry:
            _LOGGER.warning("Stream token not found or expired: %s", token)
            return web.Response(status=404, text="Not found")

        _LOGGER.debug("Proxying stream for token %s", token)

        headers: dict[str, str] = {}
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]

        try:
            session = self._get_session()
            async with session.get(entry.url, headers=headers) as upstream:
                response = web.StreamResponse(
                    status=upstream.status,
                    headers={
                        "Content-Type": upstream.headers.get(
                            "Content-Type", "audio/mpeg"
                        ),
                    },
                )
                if "Content-Length" in upstream.headers:
                    response.headers["Content-Length"] = upstream.headers[
                        "Content-Length"
                    ]
                if "Content-Range" in upstream.headers:
                    response.headers["Content-Range"] = upstream.headers[
                        "Content-Range"
                    ]
                if "Accept-Ranges" in upstream.headers:
                    response.headers["Accept-Ranges"] = upstream.headers[
                        "Accept-Ranges"
                    ]

                await response.prepare(request)

                async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                    await response.write(chunk)

                await response.write_eof()
                return response
        except ConnectionResetError:
            _LOGGER.debug("Client disconnected (device stopped playback)")
            return web.Response(status=499, text="Client disconnected")
        except Exception:
            _LOGGER.exception("Error proxying stream")
            return web.Response(status=502, text="Upstream error")

    async def start(self) -> None:
        """Start the proxy server."""
        self._app = web.Application()
        self._app.router.add_get("/stream/{token}", self._handle_stream)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        _LOGGER.info("Stream proxy started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Stop the proxy server."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._app = None
            _LOGGER.info("Stream proxy stopped")
