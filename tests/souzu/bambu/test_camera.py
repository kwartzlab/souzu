"""Tests for souzu.bambu.camera."""

import asyncio
import ssl
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from souzu.bambu.camera import P1CameraClient

# Minimal valid JPEG: SOI marker + APP0 + EOI marker
_JPEG_SOI = b"\xff\xd8\xff\xe0"
_JPEG_EOI = b"\xff\xd9"
_FAKE_JPEG = _JPEG_SOI + b"\x00" * 100 + _JPEG_EOI


def _make_frame_payload(jpeg_data: bytes) -> bytes:
    """Build a frame as the printer sends it: 16-byte header + JPEG payload."""
    header = struct.pack("<I", len(jpeg_data)) + b"\x00" * 12
    return header + jpeg_data


class TestP1AuthPacket:
    def test_auth_packet_is_80_bytes(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        assert len(packet) == 80

    def test_auth_packet_header(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        magic, ptype = struct.unpack_from("<II", packet, 0)
        assert magic == 0x40
        assert ptype == 0x3000

    def test_auth_packet_zero_padding(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        assert packet[8:16] == b"\x00" * 8

    def test_auth_packet_username(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        username = packet[16:48]
        assert username == b"bblp" + b"\x00" * 28

    def test_auth_packet_access_code(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        code = packet[48:80]
        assert code == b"12345678" + b"\x00" * 24


class TestP1CaptureFrame:
    @pytest.mark.asyncio
    async def test_captures_single_jpeg_frame(self, mocker: MockerFixture) -> None:
        frame_bytes = _make_frame_payload(_FAKE_JPEG)
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_reader.readexactly.side_effect = [
            frame_bytes[:16],
            frame_bytes[16:],
        ]
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            return_value=(mock_reader, mock_writer),
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        result = await client.capture_frame()

        assert result == _FAKE_JPEG
        mock_writer.write.assert_called_once()
        mock_writer.drain.assert_awaited_once()
        mock_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_raises(self, mocker: MockerFixture) -> None:
        async def hang(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(999)

        mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            side_effect=hang,
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        client.TIMEOUT_SECONDS = 0.01
        with pytest.raises(TimeoutError):
            await client.capture_frame()

    @pytest.mark.asyncio
    async def test_connection_refused_raises(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            side_effect=ConnectionRefusedError("Connection refused"),
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        with pytest.raises(ConnectionRefusedError):
            await client.capture_frame()

    @pytest.mark.asyncio
    async def test_ssl_context_disables_verification(
        self, mocker: MockerFixture
    ) -> None:
        frame_bytes = _make_frame_payload(_FAKE_JPEG)
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_reader.readexactly.side_effect = [
            frame_bytes[:16],
            frame_bytes[16:],
        ]
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mock_open = mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            return_value=(mock_reader, mock_writer),
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        await client.capture_frame()

        call_kwargs = mock_open.call_args.kwargs
        ssl_ctx = call_kwargs["ssl"]
        assert isinstance(ssl_ctx, ssl.SSLContext)
        assert ssl_ctx.check_hostname is False
        assert ssl_ctx.verify_mode == ssl.CERT_NONE
