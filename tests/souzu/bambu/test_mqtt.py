from __future__ import annotations

import json
from asyncio import TaskGroup
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import BambuMqttConnection


@pytest.fixture
def mock_connection() -> BambuMqttConnection:
    mock_tg = MagicMock(spec=TaskGroup)
    mock_device = MagicMock(spec=BambuDevice)
    mock_device.device_id = "SERIAL123"
    mock_device.device_name = "Test Printer"
    mock_device.ip_address = "192.168.1.100"
    with patch("souzu.bambu.mqtt.CONFIG") as mock_config:
        mock_config.printers = {"SERIAL123": MagicMock(access_code="test_code")}
        conn = BambuMqttConnection(mock_tg, mock_device)
    return conn


@pytest.fixture
def connected_connection(
    mock_connection: BambuMqttConnection,
) -> tuple[BambuMqttConnection, AsyncMock]:
    mock_client = AsyncMock()
    mock_connection._client = mock_client
    return mock_connection, mock_client


class TestSendCommand:
    @pytest.mark.asyncio
    async def test_raises_when_not_connected(
        self, mock_connection: BambuMqttConnection
    ) -> None:
        assert mock_connection._client is None
        with pytest.raises(RuntimeError, match="Not connected to printer"):
            await mock_connection.send_command("print", {"command": "pause"})

    @pytest.mark.asyncio
    async def test_publishes_to_correct_topic(
        self, connected_connection: tuple[BambuMqttConnection, AsyncMock]
    ) -> None:
        conn, mock_client = connected_connection
        await conn.send_command("print", {"command": "pause"})

        mock_client.publish.assert_awaited_once()
        assert mock_client.publish.call_args[0][0] == "device/SERIAL123/request"

    @pytest.mark.asyncio
    async def test_publishes_correct_json_payload(
        self, connected_connection: tuple[BambuMqttConnection, AsyncMock]
    ) -> None:
        conn, mock_client = connected_connection
        await conn.send_command("print", {"command": "pause"})

        payload = json.loads(mock_client.publish.call_args[0][1])
        assert payload == {"print": {"sequence_id": "0", "command": "pause"}}

    @pytest.mark.asyncio
    async def test_increments_sequence_id(
        self, connected_connection: tuple[BambuMqttConnection, AsyncMock]
    ) -> None:
        conn, mock_client = connected_connection
        await conn.send_command("print", {"command": "pause"})
        await conn.send_command("print", {"command": "resume"})

        first_payload = json.loads(mock_client.publish.call_args_list[0][0][1])
        second_payload = json.loads(mock_client.publish.call_args_list[1][0][1])
        assert first_payload["print"]["sequence_id"] == "0"
        assert second_payload["print"]["sequence_id"] == "1"


class TestConvenienceMethods:
    @pytest.mark.asyncio
    async def test_pause(
        self, connected_connection: tuple[BambuMqttConnection, AsyncMock]
    ) -> None:
        conn, mock_client = connected_connection
        await conn.pause()

        payload = json.loads(mock_client.publish.call_args[0][1])
        assert payload["print"]["command"] == "pause"

    @pytest.mark.asyncio
    async def test_resume(
        self, connected_connection: tuple[BambuMqttConnection, AsyncMock]
    ) -> None:
        conn, mock_client = connected_connection
        await conn.resume()

        payload = json.loads(mock_client.publish.call_args[0][1])
        assert payload["print"]["command"] == "resume"

    @pytest.mark.asyncio
    async def test_stop(
        self, connected_connection: tuple[BambuMqttConnection, AsyncMock]
    ) -> None:
        conn, mock_client = connected_connection
        await conn.stop()

        payload = json.loads(mock_client.publish.call_args[0][1])
        assert payload["print"]["command"] == "stop"
