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
    device_name: str
    ip_address: str


async def discover_bambu_devices(discovered_device_queue: Queue[BambuDevice]) -> None:
    found_ids: set[str] = set()

    class BambuDiscovery(SimpleServiceDiscoveryProtocol):
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

        def handle_headers(self, header_list: list[tuple[str, str]]) -> None:
            headers = CaseInsensitiveDict[str](header_list)
            if headers.get("NT") == "urn:bambulab-com:device:3dprinter:1":
                ip_address = headers.get("Location")
                serial_number = headers.get("USN")
                device_name = headers.get("DevName.bambu.com")
                if serial_number in found_ids:
                    return
                if ip_address and serial_number:
                    device = BambuDevice(
                        device_id=serial_number,
                        device_name=device_name or serial_number,
                        ip_address=ip_address,
                    )
                    discovered_device_queue.put_nowait(device)
                    found_ids.add(serial_number)

    loop = get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: BambuDiscovery(),
        local_addr=("0.0.0.0", BAMBU_DISCOVERY_PORT),  # noqa: S104
        family=socket.AF_INET,
    )
    logging.info("Discovery started")
