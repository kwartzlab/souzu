"""Camera client for capturing snapshots from Bambu Lab printers."""

from __future__ import annotations

import asyncio
import ssl
import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class CameraClient(Protocol):
    """Protocol for capturing a single JPEG frame from a printer's camera."""

    async def capture_frame(self) -> bytes: ...


class P1CameraClient:
    """Camera client for P1/A1-series printers using the port 6000 TLS protocol."""

    CAMERA_PORT: int = 6000
    TIMEOUT_SECONDS: float = 15
    # P1S streams at ~0.5 FPS; 5s drain ensures we catch at least one
    # post-discard frame even if the firmware's stale buffer is two frames deep.
    DRAIN_SECONDS: float = 2.0

    def __init__(self, ip_address: str, access_code: str) -> None:
        self._ip_address = ip_address
        self._access_code = access_code

    def _build_auth_packet(self) -> bytes:
        """Build the 80-byte authentication packet for the camera stream.

        The leading 0x40 is a magic/type marker, not a length — the actual
        packet is 80 bytes with the access code field padded to 32 bytes, per
        the community-reverse-engineered protocol.
        """
        header = struct.pack("<II", 0x40, 0x3000)
        padding = b"\x00" * 8
        username = b"bblp".ljust(32, b"\x00")
        access_code = self._access_code.encode().ljust(32, b"\x00")
        return header + padding + username + access_code

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes:
        """Read one complete frame: 16-byte header + JPEG payload."""
        header = await reader.readexactly(16)
        payload_size = struct.unpack_from("<I", header, 0)[0]
        return await reader.readexactly(payload_size)

    async def capture_frame(self) -> bytes:
        """Capture the most recent live JPEG frame from the printer's camera.

        Opens a TLS connection, authenticates, discards the first frame, and
        returns the next complete frame (plus any additional frames received
        within ``DRAIN_SECONDS`` of the second frame, returning the latest).

        The first frame must be discarded because the camera firmware replays
        a stale buffered frame from the previous client session immediately
        after authentication, before live frames begin flowing. The P1S only
        produces frames every ~2 seconds, so a purely time-based drain isn't
        enough — the live frame must be reached by frame count, not deadline.

        Raises:
            TimeoutError: If the operation exceeds TIMEOUT_SECONDS without
                producing two complete frames.
            ConnectionError: If the printer is unreachable.
            ssl.SSLError: If TLS negotiation fails.
        """
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        async with asyncio.timeout(self.TIMEOUT_SECONDS):
            reader, writer = await asyncio.open_connection(
                host=self._ip_address,
                port=self.CAMERA_PORT,
                ssl=ssl_ctx,
            )
            try:
                writer.write(self._build_auth_packet())
                await writer.drain()

                await self._read_frame(reader)  # discard stale buffered frame
                latest_jpeg = await self._read_frame(reader)
                try:
                    async with asyncio.timeout(self.DRAIN_SECONDS):
                        while True:
                            latest_jpeg = await self._read_frame(reader)
                except TimeoutError:
                    pass
                return latest_jpeg
            finally:
                writer.close()
                await writer.wait_closed()
