import logging
import socket
from asyncio import Queue, get_running_loop
from typing import Any, override

from attrs import frozen
from requests.structures import CaseInsensitiveDict
from ssdp.aio import SimpleServiceDiscoveryProtocol
from ssdp.messages import SSDPRequest, SSDPResponse

BAMBU_DISCOVERY_PORT = 2021


@frozen
class BambuDevice:
    device_id: str
    ip_address: str


async def discover_bambu_devices(discovered_device_queue: Queue[BambuDevice]) -> None:
    class BambuDiscovery(SimpleServiceDiscoveryProtocol):
        @override
        def response_received(  # type: ignore[misc]
            self, response: SSDPResponse, addr: tuple[str | Any, int]
        ) -> None:
            headers = CaseInsensitiveDict[str](response.headers)
            if headers.get("NT") == "urn:bambulab-com:device:3dprinter:1":
                ip_address = headers.get("Location")
                serial_number = headers.get("USN")
                if ip_address and serial_number:
                    device = BambuDevice(
                        device_id=serial_number,
                        ip_address=ip_address,
                    )
                    discovered_device_queue.put_nowait(device)

        @override
        def request_received(  # type: ignore[misc]
            self, request: SSDPRequest, addr: tuple[str | Any, int]
        ) -> None:
            headers = CaseInsensitiveDict[str](request.headers)
            if headers.get("NT") == "urn:bambulab-com:device:3dprinter:1":
                ip_address = headers.get("Location")
                serial_number = headers.get("USN")
                if ip_address and serial_number:
                    device = BambuDevice(
                        device_id=serial_number,
                        ip_address=ip_address,
                    )
                    discovered_device_queue.put_nowait(device)

    loop = get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: BambuDiscovery(),
        local_addr=("0.0.0.0", BAMBU_DISCOVERY_PORT),  # noqa: S104
        family=socket.AF_INET,
    )
    logging.info("Discovery started")
