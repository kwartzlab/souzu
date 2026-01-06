import json
from asyncio import Queue
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anyio import Path as AsyncPath

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import (
    SERIALIZER,
    BambuMqttConnection,
    BambuStatusReport,
    _BambuWrapper,
    _Cache,
    _custom_list_merge,
    _round_int,
)


def test_round_int() -> None:
    """Test the _round_int function."""
    assert _round_int(10.2) == 10
    assert _round_int(10.7) == 11
    assert _round_int(None) is None


def test_bambu_wrapper() -> None:
    """Test creating and accessing BambuWrapper instances."""
    report = BambuStatusReport(bed_temper=60)
    wrapper = _BambuWrapper(print=report)
    assert wrapper.print is report
    assert wrapper.print.bed_temper == 60


def test_parse_payload() -> None:
    """Test _parse_payload method of BambuMqttConnection using actual serialization."""
    # Create a connection with initial cache
    connection = MagicMock(spec=BambuMqttConnection)
    initial_report = BambuStatusReport(
        bed_temper=55,  # This will be updated by the new payload
        nozzle_temper=190,  # This will be updated by the new payload
        mc_remaining_time=120,  # This will be preserved
    )
    connection._cache = _Cache(print=initial_report)

    # Create a test payload with some updated values
    payload_data = {"print": {"bed_temper": 65, "nozzle_temper": 200}}
    payload = json.dumps(payload_data).encode()

    # Call the actual parse_payload method
    result = BambuMqttConnection._parse_payload(connection, payload)

    # Verify expected results
    assert result is not None
    assert result.print.bed_temper == 65  # Updated from payload
    assert result.print.nozzle_temper == 200  # Updated from payload
    assert result.print.mc_remaining_time == 120  # Preserved from initial report


@pytest.mark.asyncio
async def test_custom_list_merge() -> None:
    """Test the custom list merge function for lights_report."""
    from deepmerge.merger import Merger

    merger = Merger([(list, _custom_list_merge)], ["override"], ["override"])
    result = merger.merge([{"a": 1}, {"b": 2}], [{"c": 3}])
    assert result == [{"c": 3}]


@pytest.fixture
def mock_device() -> BambuDevice:
    """Create a mock BambuDevice."""
    return BambuDevice(
        device_id="XXXXYYYY",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="xxxxyyyy",
    )


@pytest.fixture
def mock_report() -> BambuStatusReport:
    """Create a mock status report."""
    return BambuStatusReport(
        bed_temper=60,
        nozzle_temper=210,
        chamber_temper=None,
        cooling_fan_speed=None,
        big_fan1_speed=None,
        big_fan2_speed=None,
        heatbreak_fan_speed=None,
        bed_target_temper=60,
        nozzle_target_temper=210,
        gcode_state="IDLE",
        mc_print_stage=1,  # 1: not printing
        mc_percent=0,
        mc_remaining_time=0,
        layer_num=None,
        total_layer_num=None,
    )


@pytest.fixture
def mock_config() -> Generator[MagicMock, None, None]:
    """Create a mock config with test access code."""
    mock_config = MagicMock()
    mock_config.printers = {"XXXXYYYY": MagicMock(access_code="test-access-code")}

    with patch("souzu.bambu.mqtt.CONFIG", mock_config):
        yield mock_config


@pytest.mark.asyncio
async def test_init(
    mock_device: BambuDevice, mock_config: Generator[MagicMock, None, None]
) -> None:
    """Test initialization of BambuMqttConnection."""
    task_group = MagicMock()
    connection = BambuMqttConnection(task_group, mock_device)

    assert connection.device is mock_device
    assert connection._queues == []
    assert connection.task_group is task_group


@pytest.mark.asyncio
async def test_subscribe(
    mock_device: BambuDevice, mock_config: Generator[MagicMock, None, None]
) -> None:
    """Test subscribe method."""
    task_group = MagicMock()
    connection = BambuMqttConnection(task_group, mock_device)

    # Set _consume_task to a dummy task to bypass initialization check
    connection._consume_task = MagicMock()  # type: ignore

    assert len(connection._queues) == 0

    async with connection.subscribe() as subscription:
        assert len(connection._queues) == 1
        assert hasattr(subscription, "__aiter__")
        assert hasattr(subscription, "__anext__")

    assert len(connection._queues) == 0  # Queue should be removed after context exit


@pytest.mark.asyncio
async def test_consume_messages(
    mock_device: BambuDevice, mock_config: Generator[MagicMock, None, None]
) -> None:
    """Test _consume_messages method."""

    # Create a proper AsyncIterator class for messages
    class MockAsyncMessageIterator:
        def __init__(self, messages: list[MagicMock]) -> None:
            self.messages = messages
            self.index = 0

        def __aiter__(self) -> "MockAsyncMessageIterator":
            return self

        async def __anext__(self) -> MagicMock:
            if self.index < len(self.messages):
                message = self.messages[self.index]
                self.index += 1
                return message
            raise StopAsyncIteration()

    task_group = MagicMock()
    connection = BambuMqttConnection(task_group, mock_device)

    mock_message = MagicMock()
    mock_message.payload = json.dumps(
        {
            "print": {
                "bed_temper": 60.5,
                "nozzle_temper": 200.7,
            }
        }
    ).encode()

    cert_path = Path("/mock/path/to/cert.pem")
    mock_path_cm = MagicMock()
    mock_path_cm.__enter__.return_value = cert_path
    mock_resource_path = MagicMock(return_value=mock_path_cm)

    mock_client = AsyncMock()
    mock_client.messages = MockAsyncMessageIterator([mock_message])

    # Returns normal client first, then one that raises exception to terminate test loop
    class ClientFactory:
        def __init__(self) -> None:
            self.call_count = 0

        def __call__(self, *args: object, **kwargs: object) -> AsyncMock:
            nonlocal mock_client
            result = mock_client
            if self.call_count > 0:
                error_client = AsyncMock()
                error_client.__aenter__.side_effect = Exception("Stop the test")
                result = error_client
            self.call_count += 1
            return result

    client_factory_instance = ClientFactory()
    mock_client_factory = MagicMock(side_effect=client_factory_instance)

    mock_queue = MagicMock()
    connection._queues = [mock_queue]  # type: ignore

    mock_wrapper = MagicMock()
    mock_wrapper.print = BambuStatusReport(bed_temper=60, nozzle_temper=200)
    connection._parse_payload = MagicMock(return_value=mock_wrapper)

    # Create a mock stack to work with _with_cache
    connection._stack = AsyncMock()
    connection._stack.__aenter__ = AsyncMock()
    connection._stack.enter_context = MagicMock(return_value=mock_path_cm)
    connection._cache = _Cache()

    mock_ssl_context = MagicMock()

    with (
        patch("souzu.bambu.mqtt.resources.path", mock_resource_path),
        patch("souzu.bambu.mqtt.Client", mock_client_factory),
        patch("souzu.bambu.mqtt._SniSslContext", MagicMock()),
        patch("souzu.bambu.mqtt.ssl.SSLContext", return_value=mock_ssl_context),
        patch("souzu.bambu.mqtt.datetime", MagicMock()),
    ):
        with pytest.raises(Exception, match="Stop the test"):
            await connection._consume_messages()

        assert mock_client_factory.call_count > 0
        mock_client.subscribe.assert_called_with(
            f"device/{mock_device.device_id}/report"
        )
        connection._parse_payload.assert_called_with(mock_message.payload)
        mock_queue.put_nowait.assert_called_with(mock_wrapper.print)


@pytest.mark.asyncio
async def test_consume_messages_mqtt_error(
    mock_device: BambuDevice, mock_config: Generator[MagicMock, None, None]
) -> None:
    """Test _consume_messages handling MqttError."""
    from aiomqtt import MqttError

    from souzu.bambu.mqtt import MQTT_ERROR_RECONNECT_DELAY

    task_group = MagicMock()
    connection = BambuMqttConnection(task_group, mock_device)

    cert_path = Path("/mock/path/to/cert.pem")
    mock_path_cm = MagicMock()
    mock_path_cm.__enter__.return_value = cert_path
    mock_resource_path = MagicMock(return_value=mock_path_cm)

    # First client raises MqttError to test reconnection logic
    error_client = AsyncMock()
    error_client.__aenter__.side_effect = MqttError("MQTT connection error")

    # Second client (after retry) raises exception to exit the test loop
    retry_client = AsyncMock()
    retry_client.__aenter__.side_effect = Exception("Stop the test")

    mock_client_factory = MagicMock(side_effect=[error_client, retry_client])
    mock_sleep = AsyncMock()  # Mock sleep to avoid waiting in tests

    # Create a mock stack to work with _with_cache
    connection._stack = AsyncMock()
    connection._stack.__aenter__ = AsyncMock()
    connection._stack.enter_context = MagicMock(return_value=mock_path_cm)

    mock_ssl_context = MagicMock()

    with (
        patch("souzu.bambu.mqtt.resources.path", mock_resource_path),
        patch("souzu.bambu.mqtt.Client", mock_client_factory),
        patch("souzu.bambu.mqtt._SniSslContext", MagicMock()),
        patch("souzu.bambu.mqtt.ssl.SSLContext", return_value=mock_ssl_context),
        patch("souzu.bambu.mqtt.sleep", mock_sleep),
    ):
        with pytest.raises(Exception, match="Stop the test"):
            await connection._consume_messages()

        assert mock_client_factory.call_count == 2
        mock_sleep.assert_called_once_with(MQTT_ERROR_RECONNECT_DELAY)


@pytest.mark.asyncio
async def test_with_cache_serialization() -> None:
    """Test the serialization of _Cache objects."""
    # Create a cache object with various data
    report = BambuStatusReport(
        bed_temper=60,
        nozzle_temper=210,
        mc_remaining_time=120,
        gcode_state="RUNNING",
        mc_percent=50,
        layer_num=10,
        total_layer_num=20,
    )

    original_cache = _Cache(
        print=report,
        last_update=datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_full_update=datetime(2023, 1, 1, 11, 0, 0, tzinfo=UTC),
    )

    # Step 1: Unstructure to dictionary using the serializer
    cache_dict = SERIALIZER.unstructure(original_cache)

    # Step 2: Convert to JSON
    json_str = json.dumps(cache_dict)

    # Step 3: Convert back from JSON
    json_loaded = json.loads(json_str)

    # Step 4: Structure back to object using the serializer
    restored_cache = SERIALIZER.structure(json_loaded, _Cache)

    # Verify the round trip worked correctly
    assert restored_cache.print is not None
    assert restored_cache.last_update == original_cache.last_update
    assert restored_cache.last_full_update == original_cache.last_full_update

    # Check report fields
    assert restored_cache.print.bed_temper == report.bed_temper
    assert restored_cache.print.nozzle_temper == report.nozzle_temper
    assert restored_cache.print.mc_remaining_time == report.mc_remaining_time
    assert restored_cache.print.gcode_state == report.gcode_state
    assert restored_cache.print.mc_percent == report.mc_percent
    assert restored_cache.print.layer_num == report.layer_num
    assert restored_cache.print.total_layer_num == report.total_layer_num


@pytest.mark.asyncio
async def test_with_cache(
    mock_device: BambuDevice,
    mock_config: Generator[MagicMock, None, None],
    tmp_path: Path,
) -> None:
    """Test _with_cache method with no existing cache file, using a real temporary directory."""
    # Create a temporary directory for the cache
    with TemporaryDirectory() as temp_dir:
        # Create a real AsyncPath for the temp directory
        temp_cache_dir = AsyncPath(temp_dir)
        task_group = MagicMock()
        connection = BambuMqttConnection(task_group, mock_device)

        # Set up test cache data
        test_report = BambuStatusReport(bed_temper=60, nozzle_temper=200)

        with patch("souzu.bambu.mqtt._CACHE_DIR", temp_cache_dir):
            # Use the actual cache mechanism
            async with connection._with_cache():
                assert connection._cache is not None
                # Modify cache to test it gets written on exit
                connection._cache = _Cache(
                    print=test_report,
                    last_update=datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC),
                )

            # Verify file was created and use AsyncPath for async file operations
            async_cache_file = (
                temp_cache_dir / f'mqtt.{mock_device.filename_prefix}.json'
            )
            assert await async_cache_file.exists()

            # Read the file content to verify it contains our data
            async with await async_cache_file.open('r') as f:
                content = await f.read()
                # Verify it's valid JSON
                cache_data = json.loads(content)
                # Verify our data is in there
                assert "print" in cache_data
                assert "bed_temper" in cache_data["print"]
                assert cache_data["print"]["bed_temper"] == 60
                assert "nozzle_temper" in cache_data["print"]
                assert cache_data["print"]["nozzle_temper"] == 200
                assert "last_update" in cache_data


@pytest.mark.asyncio
async def test_with_cache_load_existing(
    mock_device: BambuDevice, mock_config: Generator[MagicMock, None, None]
) -> None:
    """Test _with_cache method with existing cache file, using real file operations."""
    # Create a temporary directory for the cache
    with TemporaryDirectory() as temp_dir:
        # Create a real AsyncPath for the temp directory
        temp_cache_dir = AsyncPath(temp_dir)
        task_group = MagicMock()
        connection = BambuMqttConnection(task_group, mock_device)

        # Create test cache data
        original_report = BambuStatusReport(bed_temper=65, nozzle_temper=210)
        original_cache = _Cache(
            print=original_report,
            last_update=datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC),
        )

        # Manually create a cache file with our test data using AsyncPath for async operations
        async_cache_file = temp_cache_dir / f'mqtt.{mock_device.filename_prefix}.json'
        serialized = json.dumps(SERIALIZER.unstructure(original_cache))
        async with await async_cache_file.open('w') as f:
            await f.write(serialized)

        with patch("souzu.bambu.mqtt._CACHE_DIR", temp_cache_dir):
            # Use the actual cache mechanism to load the existing file
            async with connection._with_cache():
                # Verify the cache was properly loaded
                assert connection._cache is not None
                assert connection._cache.print is not None
                assert connection._cache.print.bed_temper == 65
                assert connection._cache.print.nozzle_temper == 210
                assert connection._cache.last_update is not None

                # Save a modified value to test that it gets written back correctly
                connection._cache = _Cache(
                    print=BambuStatusReport(bed_temper=70, nozzle_temper=220),
                    last_update=datetime(2023, 1, 1, 13, 0, 0, tzinfo=UTC),
                )

        # Verify the file was updated with our new values using AsyncPath
        async with await async_cache_file.open('r') as f:
            content = await f.read()
            cache_data = json.loads(content)
            assert "print" in cache_data
            assert "bed_temper" in cache_data["print"]
            assert cache_data["print"]["bed_temper"] == 70
            assert "nozzle_temper" in cache_data["print"]
            assert cache_data["print"]["nozzle_temper"] == 220


@pytest.mark.asyncio
async def test_consume_queue() -> None:
    """Test using a queue consumer function with a simple test queue."""

    # Create a simple async iterator that will return items from a queue
    async def consume_test_queue(
        test_queue: Queue[BambuStatusReport | Exception],
    ) -> AsyncGenerator[BambuStatusReport, None]:
        while True:
            item = await test_queue.get()
            if isinstance(item, Exception):
                raise item
            yield item
            test_queue.task_done()

    queue: Queue[BambuStatusReport | Exception] = Queue()

    await queue.put(BambuStatusReport(bed_temper=60))
    await queue.put(BambuStatusReport(bed_temper=70))
    # Use exception to terminate the iteration
    await queue.put(Exception("Stop test"))

    # Mock the task_done method to track calls
    queue.task_done = MagicMock()  # type: ignore

    iterator = consume_test_queue(queue)

    items = []
    with pytest.raises(Exception, match="Stop test"):
        while True:
            item = await iterator.__anext__()
            items.append(item)

    assert len(items) == 2
    assert items[0].bed_temper == 60
    assert items[1].bed_temper == 70
    assert queue.task_done.call_count == 2


@pytest.mark.asyncio
async def test_aenter_already_initialized(
    mock_device: BambuDevice, mock_config: Generator[MagicMock, None, None]
) -> None:
    """Test __aenter__ when already initialized."""
    # Patch __aenter__ to simulate an already initialized connection
    with patch.object(
        BambuMqttConnection,
        '__aenter__',
        side_effect=RuntimeError("MQTT subscription already initialized"),
    ):
        task_group = MagicMock()
        connection = BambuMqttConnection(task_group, mock_device)

        with pytest.raises(RuntimeError, match="MQTT subscription already initialized"):
            await connection.__aenter__()
