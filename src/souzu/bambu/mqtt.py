from __future__ import annotations

import json
import logging
from asyncio import Queue, QueueFull, Task, TaskGroup, sleep
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    asynccontextmanager,
)
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from socket import socket
from ssl import SSLContext, SSLSession, SSLSocket
from types import TracebackType
from typing import Any, Self, cast, override

from aiomqtt import Client, MqttError, TLSParameters
from aiomqtt.types import PayloadType
from anyio import Path as AsyncPath
from attrs import Factory, field, frozen
from cattrs import Converter, structure, unstructure
from deepmerge.merger import Merger
from deepmerge.strategy.core import STRATEGY_END
from xdg_base_dirs import xdg_cache_home

from souzu.bambu import res
from souzu.bambu.discovery import BambuDevice
from souzu.config import CONFIG

_CACHE_DIR = AsyncPath(xdg_cache_home() / "souzu")

MQTT_ERROR_RECONNECT_DELAY = 30


def _round_int(value: float | None) -> int | None:
    """Round noisy floats to ints."""
    if value is None:
        return None
    return int(round(value))


# see more fields at https://github.com/Doridian/OpenBambuAPI/blob/main/mqtt.md


@frozen
class BambuAmsSlot:
    id: int | None = None
    cols: list[str] = Factory(list)
    nozzle_temp_max: float | None = None
    nozzle_temp_min: float | None = None
    # tag_uid: str | None = None  # not captured, for privacy
    # we may consider capturing this in limited cases (e.g. by limiting it to lab spools)
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
    # version: int | None = None  # not captured, noisy and not useful for us


@frozen
class BambuLightReport:
    node: str | None = None
    mode: str | None = None  # "on", "flashing", "off"


@frozen
class BambuUploadReport:
    file_size: int | None = None
    finish_size: int | None = None
    message: str | None = None
    # oss_url: str | None = None  # not captured, for privacy
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
    bed_temper: int | None = field(converter=_round_int, default=None)
    big_fan1_speed: int | None = None  # aux fan
    big_fan2_speed: int | None = None  # chamber fan
    chamber_temper: float | None = None
    cooling_fan_speed: int | None = None
    fail_reason: int | None = None
    fan_gear: int | None = None
    # gcode_file: str | None = None  # not captured, for privacy
    gcode_file_prepare_percent: int | None = None
    gcode_start_time: int | None = None
    gcode_state: str | None = (
        None  # unreliable, this sometimes goes to "FAILED" before print start
    )
    heatbreak_fan_speed: int | None = None
    layer_num: int | None = None
    lights_report: list[BambuLightReport] = Factory(list)
    mc_percent: int | None = None
    mc_print_error_code: str | None = None
    mc_print_stage: int | None = None  # 1: not printing, 2: printing
    mc_print_sub_stage: int | None = None
    mc_remaining_time: int | None = None  # in minutes
    nozzle_target_temper: float | None = None
    nozzle_temper: int | None = field(converter=_round_int, default=None)
    print_error: int | None = None
    print_gcode_action: int | None = None
    print_real_action: int | None = None
    print_type: str | None = None
    queue_number: int | None = None
    sdcard: bool | None = None
    total_layer_num: int | None = None
    upload: BambuUploadReport | None = None
    # wifi_signal: str | None = None  # not captured, too noisy and not useful for us
    # if wifi isn't working, the printer can't tell us


@frozen
class _BambuWrapper:
    print: BambuStatusReport


@frozen
class _Cache:
    print: BambuStatusReport | None = None
    last_update: datetime | None = None
    last_full_update: datetime | None = None


def _custom_list_merge[_T](
    config: Merger, path: list[str], base: _T, nxt: _T
) -> _T | object:
    if path == ["print", "lights_report"]:
        base_list = cast(list[dict[str, Any]], base)
        items = {item["node"]: item for item in base_list}
        for item in cast(list[dict[str, Any]], nxt):
            items[item["node"]] = item
        return list(items.values())

    if isinstance(nxt, list):
        if nxt:
            return nxt
        else:
            return base
    return STRATEGY_END


_MERGER = Merger(
    [
        (list, _custom_list_merge),
        (dict, "merge"),
        (set, "union"),
    ],
    ["override"],
    ["override"],
)

SERIALIZER = Converter()
SERIALIZER.register_unstructure_hook(datetime, lambda dt: dt.isoformat())
SERIALIZER.register_structure_hook(datetime, lambda dt, _: datetime.fromisoformat(dt))


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


class BambuMqttConnection(AbstractAsyncContextManager):
    def __init__(self, task_group: TaskGroup, device: BambuDevice) -> None:
        self.task_group = task_group
        self.ip = device.ip_address
        self.device = device
        printer_config = CONFIG.printers.get(device.device_id)
        if not printer_config or not printer_config.access_code:
            raise ValueError(
                f"No access code for device {device.device_id} ({device.device_name})"
            )
        self.access_code = printer_config.access_code

        self._stack = AsyncExitStack()
        self._ca_path: Path | None = None
        self._client: Client | None = None
        self._consume_task: Task | None = None

        self._cache = _Cache()

        self._queues = list[Queue[BambuStatusReport]]()

    @override
    async def __aenter__(self) -> Self:
        if self._consume_task is not None:
            raise RuntimeError("MQTT subscription already initialized")
        await self._stack.__aenter__()
        await self._stack.enter_async_context(self._with_cache())
        self._consume_task = self.task_group.create_task(self._consume_messages())
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._consume_task is None:
            raise RuntimeError("MQTT subscription not initialized or already closed")
        self._consume_task.cancel()
        await self._stack.__aexit__(exc_type, exc, tb)

    @asynccontextmanager
    async def subscribe(
        self,
    ) -> AsyncGenerator[AsyncIterator[BambuStatusReport], None]:
        """
        Create an in-memory subscription to the MQTT topic.

        Note that this does not correspond to a subscription with the MQTT
        broker on the printer. Instead, this class maintains a single MQTT
        subscription, and passes events to queues for each individual
        subscription.
        """

        queue = Queue[BambuStatusReport]()
        self._queues.append(queue)
        try:
            yield _consume_queue(queue)
        finally:
            self._queues.remove(queue)

    async def _consume_messages(self) -> None:
        self._ca_path = self._stack.enter_context(
            resources.path(res, "bambu_lan_ca_cert.pem")
        )
        tls_params = TLSParameters(ca_certs=str(self._ca_path))
        while True:
            client = Client(
                hostname=self.ip,
                port=8883,
                username="bblp",
                password=self.access_code,
                tls_params=tls_params,
            )

            # patch to send device id in SNI, and fix cert validation
            assert client._client._ssl_context is not None
            client._client._ssl_context = _SniSslContext(
                self.device.device_id, client._client._ssl_context
            )

            try:
                async with client:
                    await client.subscribe(f"device/{self.device.device_id}/report")
                    async for message in client.messages:
                        wrapper = self._parse_payload(message.payload)
                        if wrapper is not None:
                            self._cache = _Cache(
                                print=wrapper.print,
                                last_update=datetime.now(UTC),
                                last_full_update=self._cache.last_full_update,
                            )
                            for queue in self._queues:
                                try:
                                    queue.put_nowait(wrapper.print)
                                except QueueFull:
                                    logging.warning(
                                        "Dropping message due to full queue"
                                    )
            except MqttError as e:
                logging.exception(f"MQTT error: {e}")
                await sleep(MQTT_ERROR_RECONNECT_DELAY)
                # TODO how to handle rediscovery at new IP?

    def _parse_payload(self, payload: PayloadType) -> _BambuWrapper | None:
        try:
            if isinstance(payload, bytes):
                payload_str = payload.decode()
            elif isinstance(payload, str):
                payload_str = payload
            else:
                raise ValueError(f"Invalid message data type: {type(payload)}")
            new_dict = json.loads(payload_str)
            old_dict = {"print": unstructure(self._cache.print) or {}}
            merged = _MERGER.merge(old_dict, new_dict)
            return structure(merged, _BambuWrapper)
        except Exception as e:
            logging.exception(f"Error parsing message: {e}", extra={"payload": payload})
            return None

    @asynccontextmanager
    async def _with_cache(self) -> AsyncGenerator[None, None]:
        """
        Context manager to load and save a cache file.

        When the context manager exits due to AsyncExitStack, the cache file
        will be saved.
        """
        await _CACHE_DIR.mkdir(exist_ok=True, parents=True)
        cache_file = _CACHE_DIR / f'mqtt.{self.device.filename_prefix}.json'
        if await cache_file.exists():
            async with await cache_file.open('r') as f:
                cache_str = json.loads(await f.read())
                logging.info(f"Loading cache file {cache_file}")
                self._cache = SERIALIZER.structure(cache_str, _Cache)

        try:
            yield
        finally:
            serialized = json.dumps(SERIALIZER.unstructure(self._cache))
            async with await cache_file.open('w') as f:
                await f.write(serialized)
                logging.info(f"Saved cache file {cache_file}")


async def _consume_queue[_T](
    queue: Queue[_T],
) -> AsyncIterator[_T]:
    while True:
        yield await queue.get()
        queue.task_done()
