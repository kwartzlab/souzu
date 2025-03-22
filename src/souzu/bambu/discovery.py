import asyncio
import logging
import socket
from asyncio import Queue, get_running_loop
from datetime import timedelta
from typing import Any, override

from attrs import frozen
from requests.structures import CaseInsensitiveDict
from ssdp.aio import SimpleServiceDiscoveryProtocol
from ssdp.messages import SSDPRequest, SSDPResponse

from souzu.config import CONFIG

BAMBU_DISCOVERY_PORT = 2021


@frozen
class BambuDevice:
    device_id: str
    device_name: str
    ip_address: str
    filename_prefix: str


class BambuDiscovery(SimpleServiceDiscoveryProtocol):
    def __init__(self, discovered_device_queue: Queue[BambuDevice]) -> None:
        super().__init__()
        self.discovered_device_queue = discovered_device_queue
        self.found_ids: set[str] = set()

    @override
    def response_received(  # type: ignore[misc]
        self, response: SSDPResponse, addr: tuple[str | Any, int]
    ) -> None:
        self.handle_headers(response.headers)

    @override
    def request_received(  # type: ignore[misc]
        self, request: SSDPRequest, addr: tuple[str | Any, int]
    ) -> None:
        self.handle_headers(request.headers)

    @override
    def connection_lost(self, exc: Exception | None) -> None:  # type: ignore[misc]
        logging.info("Discovery stopped", exc_info=exc)

    def handle_headers(self, header_list: list[tuple[str, str]]) -> None:
        headers = CaseInsensitiveDict[str](header_list)
        if headers.get("NT") == "urn:bambulab-com:device:3dprinter:1":
            ip_address = headers.get("Location")
            serial_number = headers.get("USN")
            device_name = headers.get("DevName.bambu.com")
            if serial_number in self.found_ids:
                return
            if ip_address and serial_number:
                filename_prefix = serial_number
                logging.info(
                    f"Found device {serial_number} at {ip_address} ({device_name})"
                )
                device_config = CONFIG.printers.get(serial_number)
                if device_config:
                    if (
                        device_config.ip_address
                        and device_config.ip_address != ip_address
                    ):
                        logging.warning(
                            f"Discovered device {serial_number} at {ip_address}, but config"
                            f" has {device_config.ip_address} (connecting to {ip_address})"
                        )
                    if device_config.filename_prefix:
                        filename_prefix = device_config.filename_prefix
                device = BambuDevice(
                    device_id=serial_number,
                    device_name=device_name or serial_number,
                    ip_address=ip_address,
                    filename_prefix=filename_prefix,
                )
                self.discovered_device_queue.put_nowait(device)
                self.found_ids.add(serial_number)


async def discover_bambu_devices(
    discovered_device_queue: Queue[BambuDevice], max_time: timedelta | None = None
) -> None:
    loop = get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: BambuDiscovery(discovered_device_queue),
        local_addr=("0.0.0.0", BAMBU_DISCOVERY_PORT),  # noqa: S104
        family=socket.AF_INET,
        reuse_port=hasattr(socket, 'SO_REUSEPORT') or None,  # share port, if supported
    )
    logging.info("Discovery started")
    if max_time is not None:
        try:
            await asyncio.sleep(max_time.total_seconds())
        finally:
            transport.close()
