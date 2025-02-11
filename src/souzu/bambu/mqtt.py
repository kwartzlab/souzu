from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from importlib import resources
from pathlib import Path
from socket import socket
from ssl import SSLContext, SSLSession, SSLSocket
from types import TracebackType
from typing import Self, override

from aiomqtt import Client, TLSParameters
from aiomqtt.types import PayloadType
from attrs import Factory, frozen
from cattrs import structure, unstructure
from deepmerge import always_merger

from souzu.bambu import res

# see more fields at https://github.com/Doridian/OpenBambuAPI/blob/main/mqtt.md


@frozen
class BambuAmsSlot:
    id: int | None = None
    cols: list[str] = Factory(list)
    nozzle_temp_max: float | None = None
    nozzle_temp_min: float | None = None
    tag_uid: str | None = None
    tray_color: str | None = None
    tray_info_idx: str | None = None
    tray_type: str | None = None


@frozen
class BambuAmsDetails:
    humidity: int | None = None
    id: int | None = None
    temp: float | None = None
    tray: list[BambuAmsSlot] = Factory(list)


@frozen
class BambuAmsSummary:
    ams: list[BambuAmsDetails] = Factory(list)
    tray_now: int | None = None
    tray_pre: int | None = None
    version: int | None = None


@frozen
class BambuLightReport:
    node: str | None = None
    mode: str | None = None  # "on", "flashing", probably "off"


@frozen
class BambuUploadReport:
    file_size: int | None = None
    finish_size: int | None = None
    message: str | None = None
    oss_url: str | None = None
    progress: int | None = None
    speed: int | None = None
    status: str | None = None
    time_remaining: int | None = None
    trouble_id: str | None = None


@frozen
class BambuStatusReport:
    ams: BambuAmsSummary | None = None
    aux_part_fan: bool | None = None
    bed_target_temper: float | None = None
    bed_temper: float | None = None
    big_fan1_speed: int | None = None  # aux fan
    big_fan2_speed: int | None = None  # chamber fan
    chamber_temper: float | None = None
    cooling_fan_speed: int | None = None
    fail_reason: int | None = None
    fan_gear: int | None = None
    gcode_file: str | None = None
    gcode_file_prepare_percent: int | None = None
    gcode_start_time: int | None = None
    gcode_state: str | None = None
    heatbreak_fan_speed: int | None = None
    layer_num: int | None = None
    lights_report: list[BambuLightReport] = Factory(list)
    mc_percent: int | None = None
    mc_print_error_code: str | None = None
    mc_print_stage: int | None = None
    mc_print_sub_stage: int | None = None
    mc_remaining_time: int | None = None
    nozzle_target_temper: float | None = None
    nozzle_temper: float | None = None
    print_error: int | None = None
    print_gcode_action: int | None = None
    print_real_action: int | None = None
    print_type: str | None = None
    queue_number: int | None = None
    sdcard: bool | None = None
    total_layer_num: int | None = None
    upload: BambuUploadReport | None = None
    wifi_signal: str | None = None


@frozen
class _BambuWrapper:
    print: BambuStatusReport


class _SniSslContext(SSLContext):
    def __new__(
        cls,
        hostname: str,
        wrapped: SSLContext,
    ) -> _SniSslContext:
        return SSLContext.__new__(cls, wrapped.protocol)

    def __init__(self, hostname: str, wrapped: SSLContext) -> None:
        super().__init__()
        self.hostname = hostname
        self.wrapped = wrapped

    @override
    def wrap_socket(
        self,
        sock: socket,
        server_side: bool = False,
        do_handshake_on_connect: bool = True,
        suppress_ragged_eofs: bool = True,
        server_hostname: str | bytes | None = None,
        session: SSLSession | None = None,
    ) -> SSLSocket:
        return self.wrapped.wrap_socket(
            sock,
            server_side=server_side,
            do_handshake_on_connect=do_handshake_on_connect,
            suppress_ragged_eofs=suppress_ragged_eofs,
            server_hostname=self.hostname,
        )


class BambuMqttSubscription(AbstractAsyncContextManager):
    def __init__(self, ip: str, device_id: str, access_code: str) -> None:
        self.ip = ip
        self.device_id = device_id
        self.access_code = access_code
        self._entered = False
        self._client: Client | None = None
        self._ca_path_ctx: AbstractContextManager[Path] | None = None
        self._ca_path: Path | None = None

        self._status: _BambuWrapper = _BambuWrapper(BambuStatusReport())

    @override
    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("MQTT subscription already initialized")

        self._ca_path_ctx = resources.path(res, "bambu_lan_ca_cert.pem")
        self._ca_path = self._ca_path_ctx.__enter__()

        tls_params = TLSParameters(ca_certs=str(self._ca_path))

        self._client = Client(
            hostname=self.ip,
            port=8883,
            username="bblp",
            password=self.access_code,
            tls_params=tls_params,
        )

        # patch to send device id in SNI, and fix cert validation
        assert self._client._client._ssl_context is not None
        self._client._client._ssl_context = _SniSslContext(
            self.device_id, self._client._client._ssl_context
        )

        await self._client.__aenter__()
        await self._client.subscribe(f"device/{self.device_id}/report")

        self._entered = True
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is None or self._ca_path_ctx is None:
            raise RuntimeError("MQTT subscription not initialized or already closed")
        try:
            await self._client.__aexit__(exc_type, exc, tb)
        finally:
            try:
                self._ca_path_ctx.__exit__(exc_type, exc, tb)
            finally:
                self._client = None

    @property
    async def messages(
        self,
    ) -> AsyncIterator[tuple[BambuStatusReport, BambuStatusReport]]:
        if self._client is None:
            raise RuntimeError("MQTT subscription not initialized")
        async for message in self._client.messages:
            wrapper = self._parse_payload(message.payload)
            if wrapper is not None:
                old = self._status
                self._status = wrapper
                yield old.print, wrapper.print

    def _parse_payload(self, payload: PayloadType) -> _BambuWrapper | None:
        try:
            if isinstance(payload, bytes):
                payload_str = payload.decode()
            elif isinstance(payload, str):
                payload_str = payload
            else:
                raise ValueError(f"Invalid message data type: {type(payload)}")
            new_dict = json.loads(payload_str)
            old_dict = unstructure(self._status)
            merged = always_merger.merge(old_dict, new_dict)
            return structure(merged, _BambuWrapper)
        except Exception as e:
            logging.exception(f"Error parsing message: {e}", extra={"payload": payload})
            return None
