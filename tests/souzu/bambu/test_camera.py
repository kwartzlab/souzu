"""Tests for souzu.bambu.camera."""

import struct

from souzu.bambu.camera import P1CameraClient


class TestP1AuthPacket:
    def test_auth_packet_is_64_bytes(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        assert len(packet) == 64

    def test_auth_packet_header(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        size, ptype = struct.unpack_from("<II", packet, 0)
        assert size == 0x40
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
        code = packet[48:64]
        assert code == b"12345678" + b"\x00" * 8
