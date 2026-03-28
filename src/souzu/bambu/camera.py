"""Camera client for capturing snapshots from Bambu Lab printers."""

from __future__ import annotations

import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class CameraClient(Protocol):
    """Protocol for capturing a single JPEG frame from a printer's camera."""

    async def capture_frame(self) -> bytes: ...


class P1CameraClient:
    """Camera client for P1/A1-series printers using the port 6000 TLS protocol."""

    CAMERA_PORT = 6000
    TIMEOUT_SECONDS = 10

    def __init__(self, ip_address: str, access_code: str) -> None:
        self._ip_address = ip_address
        self._access_code = access_code

    def _build_auth_packet(self) -> bytes:
        """Build the 64-byte authentication packet for the camera stream."""
        header = struct.pack("<II", 0x40, 0x3000)
        padding = b"\x00" * 8
        username = b"bblp".ljust(32, b"\x00")
        access_code = self._access_code.encode().ljust(16, b"\x00")
        return header + padding + username + access_code

    async def capture_frame(self) -> bytes:
        """Capture a single JPEG frame from the printer's camera."""
        raise NotImplementedError("Will be implemented in the next task")
