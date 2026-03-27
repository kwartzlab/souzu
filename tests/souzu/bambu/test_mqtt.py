from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from souzu.bambu.mqtt import BambuMqttConnection


@pytest.fixture
def mock_connection() -> BambuMqttConnection:
    mock_tg = MagicMock()
    mock_device = MagicMock()
    mock_device.device_id = "SERIAL123"
    mock_device.device_name = "Test Printer"
    mock_device.ip_address = "192.168.1.100"
    with patch("souzu.bambu.mqtt.CONFIG") as mock_config:
        mock_config.printers = {"SERIAL123": MagicMock(access_code="test_code")}
        conn = BambuMqttConnection(mock_tg, mock_device)
    return conn


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
        self, mock_connection: BambuMqttConnection
    ) -> None:
        mock_client = MagicMock()
        mock_client.publish = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.send_command("print", {"command": "pause"})

        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "device/SERIAL123/request"

    @pytest.mark.asyncio
    async def test_publishes_correct_json_payload(
        self, mock_connection: BambuMqttConnection
    ) -> None:
        mock_client = MagicMock()
        mock_client.publish = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.send_command("print", {"command": "pause"})

        call_args = mock_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload == {"print": {"sequence_id": "0", "command": "pause"}}

    @pytest.mark.asyncio
    async def test_increments_sequence_id(
        self, mock_connection: BambuMqttConnection
    ) -> None:
        mock_client = MagicMock()
        mock_client.publish = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.send_command("print", {"command": "pause"})
        await mock_connection.send_command("print", {"command": "resume"})

        first_payload = json.loads(mock_client.publish.call_args_list[0][0][1])
        second_payload = json.loads(mock_client.publish.call_args_list[1][0][1])
        assert first_payload["print"]["sequence_id"] == "0"
        assert second_payload["print"]["sequence_id"] == "1"


class TestConvenienceMethods:
    @pytest.mark.asyncio
    async def test_pause(self, mock_connection: BambuMqttConnection) -> None:
        mock_client = MagicMock()
        mock_client.publish = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.pause()

        call_args = mock_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["print"]["command"] == "pause"

    @pytest.mark.asyncio
    async def test_resume(self, mock_connection: BambuMqttConnection) -> None:
        mock_client = MagicMock()
        mock_client.publish = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.resume()

        call_args = mock_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["print"]["command"] == "resume"

    @pytest.mark.asyncio
    async def test_stop(self, mock_connection: BambuMqttConnection) -> None:
        mock_client = MagicMock()
        mock_client.publish = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.stop()

        call_args = mock_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload["print"]["command"] == "stop"
