from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from importlib import resources
from pathlib import Path
from socket import socket
from ssl import SSLContext, SSLSession, SSLSocket
from types import TracebackType
from typing import Self, override

from aiomqtt import Client, Message, TLSParameters

from souzu.bambu import res


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
    async def messages(self) -> AsyncIterator[Message]:
        if self._client is None:
            raise RuntimeError("MQTT subscription not initialized")
        async for message in self._client.messages:
            yield message
