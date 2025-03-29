import asyncio
import socket
from asyncio import Queue
from collections.abc import Generator
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_mock import MockerFixture
from ssdp.messages import SSDPRequest, SSDPResponse

from souzu.bambu.discovery import (
    BAMBU_DISCOVERY_PORT,
    BambuDevice,
    BambuDiscovery,
    discover_bambu_devices,
)


@pytest.fixture
def mock_transport() -> MagicMock:
    """Create a mock transport for datagram endpoint."""
    transport = MagicMock()
    transport.close = MagicMock()
    return transport


@pytest.fixture
def mock_protocol() -> MagicMock:
    """Create a mock protocol for datagram endpoint."""
    protocol = MagicMock()
    return protocol


@pytest.fixture
def discovery_queue() -> Queue[BambuDevice]:
    """Create a queue for discovered devices."""
    return Queue()


def test_bambu_device() -> None:
    """Test BambuDevice creation and attributes."""
    device_id = "DEVICE123456"
    device_name = "My Printer"
    ip_address = "192.168.1.100"
    filename_prefix = "printer1"

    device = BambuDevice(
        device_id=device_id,
        device_name=device_name,
        ip_address=ip_address,
        filename_prefix=filename_prefix,
    )

    assert device.device_id == device_id
    assert device.device_name == device_name
    assert device.ip_address == ip_address
    assert device.filename_prefix == filename_prefix


@pytest.mark.asyncio
@patch("souzu.bambu.discovery.get_running_loop")
async def test_discover_bambu_devices_timeout(
    mock_get_running_loop: MagicMock,
    mock_transport: MagicMock,
    mock_protocol: MagicMock,
    discovery_queue: Queue[BambuDevice],
    mocker: MockerFixture,
) -> None:
    """Test discover_bambu_devices with a timeout."""
    # Mock the asyncio.sleep to return immediately
    mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)

    # Set up the mock loop to return our mock transport and protocol
    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = AsyncMock(
        return_value=(mock_transport, mock_protocol)
    )
    mock_get_running_loop.return_value = mock_loop

    # Mock logging to verify it was called
    mock_logging = mocker.patch("souzu.bambu.discovery.logging")

    # Run discover_bambu_devices with a timeout
    max_time = timedelta(seconds=1)
    await discover_bambu_devices(discovery_queue, max_time)

    # Verify the datagram endpoint was created with the right parameters
    mock_loop.create_datagram_endpoint.assert_called_once()
    args, kwargs = mock_loop.create_datagram_endpoint.call_args

    # Verify the factory function creates a BambuDiscovery instance
    factory = args[0]
    protocol_instance = factory()
    assert isinstance(protocol_instance, BambuDiscovery)
    assert protocol_instance.discovered_device_queue is discovery_queue

    # Verify other parameters
    assert kwargs["local_addr"] == ("0.0.0.0", BAMBU_DISCOVERY_PORT)  # noqa: S104
    assert kwargs["family"] == socket.AF_INET

    # Check reuse_port behavior based on socket capabilities
    if hasattr(socket, 'SO_REUSEPORT'):
        assert kwargs["reuse_port"] is True
    else:
        assert kwargs["reuse_port"] is None

    # Verify sleep was called with the right timeout
    mock_sleep.assert_called_once_with(max_time.total_seconds())

    # Verify transport was closed
    mock_transport.close.assert_called_once()

    # Verify logging was called
    mock_logging.info.assert_any_call("Discovery started")


@pytest.mark.asyncio
async def test_discover_bambu_devices_without_timeout(
    mocker: MockerFixture,
    discovery_queue: Queue[BambuDevice],
) -> None:
    """Test discover_bambu_devices with no timeout."""
    # Setup mocks
    mock_transport = MagicMock()
    mock_protocol = MagicMock()

    # Mock out the entire createProtocol to bypass the socket issues
    mock_create_endpoint = AsyncMock(return_value=(mock_transport, mock_protocol))

    # Patch the asyncio loop
    with patch("souzu.bambu.discovery.get_running_loop") as mock_get_loop:
        mock_loop = MagicMock()
        mock_loop.create_datagram_endpoint = mock_create_endpoint
        mock_get_loop.return_value = mock_loop

        # Patch logging
        with patch("souzu.bambu.discovery.logging"):
            # Run as a task so we can cancel it
            task = asyncio.create_task(discover_bambu_devices(discovery_queue))

            # Give it a moment to start
            await asyncio.sleep(0.1)

            # Add a mock device to the protocol to simulate discovery
            protocol_instance = mock_create_endpoint.call_args[0][0]()

            # Simulate a discovery event by directly calling handle_headers
            # This verifies that devices discovered in the protocol arrive in the queue
            test_headers = [
                ("NT", "urn:bambulab-com:device:3dprinter:1"),
                ("Location", "192.168.1.100"),
                ("USN", "SIMULATED123"),
                ("DevName.bambu.com", "Simulated Printer"),
            ]
            protocol_instance.handle_headers(test_headers)

            # Cancel the task since it won't exit on its own
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

            # Verify endpoint was created
            mock_create_endpoint.assert_called_once()

            # Check that our simulated device was added to the queue
            assert not discovery_queue.empty()
            device = discovery_queue.get_nowait()
            assert device.device_id == "SIMULATED123"
            assert device.device_name == "Simulated Printer"
            assert device.ip_address == "192.168.1.100"


@pytest.mark.asyncio
async def test_discover_bambu_devices_socket_error(
    mocker: MockerFixture,
    discovery_queue: Queue[BambuDevice],
) -> None:
    """Test error handling in discover_bambu_devices when socket creation fails."""
    # Mock the asyncio loop
    mock_loop = MagicMock()

    # Simulate socket creation error
    socket_error = OSError("Failed to create socket")
    mock_loop.create_datagram_endpoint = AsyncMock(side_effect=socket_error)

    with patch("souzu.bambu.discovery.get_running_loop", return_value=mock_loop):
        # The error occurs before logging happens
        mocker.patch("souzu.bambu.discovery.logging")

        # Run discover_bambu_devices - should handle the socket error
        with pytest.raises(socket.error):
            await discover_bambu_devices(discovery_queue)

        # The error is thrown before logging can happen


class TestBambuDiscovery:
    """Test the BambuDiscovery inner class."""

    @pytest.fixture
    def mock_config(self) -> Generator[MagicMock, None, None]:
        """Create a mock CONFIG object."""
        with patch("souzu.bambu.discovery.CONFIG") as mock_config:
            mock_config.printers = {}
            yield mock_config

    @pytest.fixture
    def bambu_discovery(self, discovery_queue: Queue[BambuDevice]) -> BambuDiscovery:
        """Create a BambuDiscovery instance for testing."""
        return BambuDiscovery(discovery_queue)

    @pytest.mark.asyncio
    async def test_response_received(
        self, bambu_discovery: BambuDiscovery, mocker: MockerFixture
    ) -> None:
        """Test the response_received method."""
        # Create a mock handle_headers
        bambu_discovery.handle_headers = mocker.MagicMock()

        # Create a mock SSDP response
        response = SSDPResponse(200, [("NT", "urn:bambulab-com:device:3dprinter:1")])
        addr = ("192.168.1.100", 1234)

        # Call response_received
        bambu_discovery.response_received(response, addr)

        # Verify handle_headers was called with the right headers
        bambu_discovery.handle_headers.assert_called_once_with(response.headers)

    @pytest.mark.asyncio
    async def test_request_received(
        self, bambu_discovery: BambuDiscovery, mocker: MockerFixture
    ) -> None:
        """Test the request_received method."""
        # Create a mock handle_headers
        bambu_discovery.handle_headers = mocker.MagicMock()

        # Create a mock SSDP request
        # The API expects headers as a list but we can mock it for testing purposes
        request = MagicMock(spec=SSDPRequest)
        request.headers = [("NT", "urn:bambulab-com:device:3dprinter:1")]
        addr = ("192.168.1.100", 1234)

        # Call request_received
        bambu_discovery.request_received(request, addr)

        # Verify handle_headers was called with the right headers
        bambu_discovery.handle_headers.assert_called_once_with(request.headers)

    @pytest.mark.asyncio
    async def test_connection_lost(
        self, bambu_discovery: BambuDiscovery, mocker: MockerFixture
    ) -> None:
        """Test the connection_lost method."""
        # Mock logging
        mock_logging = mocker.patch("logging.info")

        # Test with exception
        exception = Exception("Test exception")
        bambu_discovery.connection_lost(exception)
        mock_logging.assert_called_once_with("Discovery stopped", exc_info=exception)

        # Reset mock and test without exception
        mock_logging.reset_mock()
        bambu_discovery.connection_lost(None)
        mock_logging.assert_called_once_with("Discovery stopped", exc_info=None)

    @pytest.mark.asyncio
    async def test_handle_headers_bambu_device(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
    ) -> None:
        """Test handling headers for a Bambu device."""
        # Make sure CONFIG.printers is empty for this test
        mock_config.printers = {}

        # Create headers for a Bambu device
        headers = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            ("Location", "192.168.1.100"),
            ("USN", "DEVICE123456"),
            ("DevName.bambu.com", "My Printer"),
        ]

        # Call handle_headers
        bambu_discovery.handle_headers(headers)

        # Get the device from the queue
        device = discovery_queue.get_nowait()

        # Verify the device has the right properties
        assert device.device_id == "DEVICE123456"
        assert device.device_name == "My Printer"
        assert device.ip_address == "192.168.1.100"
        assert device.filename_prefix == "DEVICE123456"

        # Queue should be empty now
        assert discovery_queue.empty()

    @pytest.mark.asyncio
    async def test_handle_headers_with_config(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
        mocker: MockerFixture,
    ) -> None:
        """Test handling headers with device config."""
        device_id = "DEVICE123456"

        # Create a mock device config
        device_config = MagicMock()
        device_config.ip_address = "192.168.1.200"  # Different from discovered IP
        device_config.filename_prefix = "custom_prefix"

        # Update the mocked CONFIG.printers for this test
        mock_config.printers = {device_id: device_config}

        # Create headers for a Bambu device
        headers = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            ("Location", "192.168.1.100"),  # Discovered IP differs from config
            ("USN", device_id),
            ("DevName.bambu.com", "My Printer"),
        ]

        # Mock logging.warning
        with patch("logging.warning") as mock_logging:
            # Call handle_headers
            bambu_discovery.handle_headers(headers)

            # Verify warning was logged about IP mismatch
            mock_logging.assert_called_once()

        # Get the device from the queue
        device = discovery_queue.get_nowait()

        # Verify the device has the right properties
        assert device.device_id == device_id
        assert device.device_name == "My Printer"
        assert device.ip_address == "192.168.1.100"  # Uses discovered IP despite config
        assert (
            device.filename_prefix == "custom_prefix"
        )  # Uses custom prefix from config

    @pytest.mark.asyncio
    async def test_handle_headers_with_config_no_ip_address(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
    ) -> None:
        """Test handling headers with device config that has no IP address."""
        device_id = "DEVICE123456"

        # Create a mock device config with only filename_prefix but no IP address
        device_config = MagicMock()
        device_config.ip_address = None  # No IP in config
        device_config.filename_prefix = "custom_prefix"

        # Update the mocked CONFIG.printers for this test
        mock_config.printers = {device_id: device_config}

        # Create headers for a Bambu device
        headers = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            ("Location", "192.168.1.100"),
            ("USN", device_id),
            ("DevName.bambu.com", "My Printer"),
        ]

        # Call handle_headers - no IP mismatch warning should be logged
        bambu_discovery.handle_headers(headers)

        # Get the device from the queue
        device = discovery_queue.get_nowait()

        # Verify the device has the right properties
        assert device.device_id == device_id
        assert device.device_name == "My Printer"
        assert device.ip_address == "192.168.1.100"
        assert device.filename_prefix == "custom_prefix"

    @pytest.mark.asyncio
    async def test_handle_headers_with_config_no_filename_prefix(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
    ) -> None:
        """Test handling headers with device config that has no filename_prefix."""
        device_id = "DEVICE123456"

        # Create a mock device config with IP address but no filename_prefix
        device_config = MagicMock()
        device_config.ip_address = "192.168.1.200"
        device_config.filename_prefix = None  # No filename_prefix

        # Update the mocked CONFIG.printers for this test
        mock_config.printers = {device_id: device_config}

        # Create headers for a Bambu device
        headers = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            ("Location", "192.168.1.100"),
            ("USN", device_id),
            ("DevName.bambu.com", "My Printer"),
        ]

        # Call handle_headers
        with patch("logging.warning"):
            bambu_discovery.handle_headers(headers)

        # Get the device from the queue
        device = discovery_queue.get_nowait()

        # Verify the device uses default device_id as filename_prefix
        assert device.device_id == device_id
        assert device.device_name == "My Printer"
        assert device.ip_address == "192.168.1.100"
        assert (
            device.filename_prefix == device_id
        )  # Uses device_id since config has none

    @pytest.mark.asyncio
    async def test_handle_headers_non_bambu_device(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
    ) -> None:
        """Test handling headers for a non-Bambu device."""
        # Create headers for a non-Bambu device
        headers = [
            ("NT", "urn:schemas-upnp-org:device:MediaServer:1"),  # Not a Bambu device
            ("Location", "192.168.1.100"),
            ("USN", "DEVICE123456"),
        ]

        # Call handle_headers
        bambu_discovery.handle_headers(headers)

        # Queue should be empty (no device added)
        assert discovery_queue.empty()

    @pytest.mark.asyncio
    async def test_handle_headers_duplicate_device(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
    ) -> None:
        """Test handling headers for a duplicate device."""
        # Add a device to the found_ids set
        bambu_discovery.found_ids.add("DEVICE123456")

        # Create headers for a Bambu device with the same ID
        headers = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            ("Location", "192.168.1.100"),
            ("USN", "DEVICE123456"),  # Already in found_ids
            ("DevName.bambu.com", "My Printer"),
        ]

        # Call handle_headers
        bambu_discovery.handle_headers(headers)

        # Queue should be empty (no duplicate device added)
        assert discovery_queue.empty()

    @pytest.mark.asyncio
    async def test_handle_headers_missing_fields(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
    ) -> None:
        """Test handling headers with missing fields."""
        # Create headers missing IP address
        headers1 = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            # Missing Location
            ("USN", "DEVICE123456"),
            ("DevName.bambu.com", "My Printer"),
        ]

        # Call handle_headers
        bambu_discovery.handle_headers(headers1)

        # Queue should be empty (missing required field)
        assert discovery_queue.empty()

        # Create headers missing serial number
        headers2 = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            ("Location", "192.168.1.100"),
            # Missing USN
            ("DevName.bambu.com", "My Printer"),
        ]

        # Call handle_headers
        bambu_discovery.handle_headers(headers2)

        # Queue should be empty (missing required field)
        assert discovery_queue.empty()

    @pytest.mark.asyncio
    async def test_handle_headers_missing_device_name(
        self,
        mock_config: MagicMock,
        bambu_discovery: BambuDiscovery,
        discovery_queue: Queue[BambuDevice],
    ) -> None:
        """Test handling headers with missing device name."""
        # Create headers missing device name
        headers = [
            ("NT", "urn:bambulab-com:device:3dprinter:1"),
            ("Location", "192.168.1.100"),
            ("USN", "DEVICE123456"),
            # Missing DevName.bambu.com
        ]

        # Call handle_headers
        bambu_discovery.handle_headers(headers)

        # Get the device from the queue
        device = discovery_queue.get_nowait()

        # Verify device_name defaults to device_id when missing
        assert device.device_name == "DEVICE123456"

    @pytest.mark.asyncio
    async def test_integrated_discovery_flow(
        self,
        mock_config: MagicMock,
        mocker: MockerFixture,
    ) -> None:
        """Test the integrated discovery flow with response and request handling."""
        # Create a clean queue and discovery protocol for this test
        clean_queue = Queue[BambuDevice]()
        test_discovery = BambuDiscovery(clean_queue)

        # Directly test the handle_headers method instead of going through response/request
        # Since that's the method that actually does the processing
        test_discovery.handle_headers(
            [
                ("NT", "urn:bambulab-com:device:3dprinter:1"),
                ("Location", "192.168.1.101"),
                ("USN", "DEVICE101"),
                ("DevName.bambu.com", "Printer One"),
            ]
        )

        test_discovery.handle_headers(
            [
                ("NT", "urn:bambulab-com:device:3dprinter:1"),
                ("Location", "192.168.1.102"),
                ("USN", "DEVICE102"),
                ("DevName.bambu.com", "Printer Two"),
            ]
        )

        # Verify devices were added to the queue
        assert clean_queue.qsize() == 2

        # Get devices from queue
        devices = []
        while not clean_queue.empty():
            devices.append(clean_queue.get_nowait())

        # Get the device IDs from our collected devices
        device_ids = [device.device_id for device in devices]

        # Verify they match what we expect
        assert sorted(device_ids) == ["DEVICE101", "DEVICE102"]

        # Verify we don't process duplicates by sending the same device again
        test_discovery.handle_headers(
            [
                ("NT", "urn:bambulab-com:device:3dprinter:1"),
                ("Location", "192.168.1.101"),
                ("USN", "DEVICE101"),  # Same as first device (should be in found_ids)
                ("DevName.bambu.com", "Printer One"),
            ]
        )

        # Queue should still be empty (no new devices)
        assert clean_queue.empty()
