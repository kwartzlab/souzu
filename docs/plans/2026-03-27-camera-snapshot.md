# Camera Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture a single JPEG snapshot from a P1-series Bambu printer's camera on demand via the existing Slack "Photo" button.

**Architecture:** A `CameraClient` Protocol defines `capture_frame() -> bytes`. `P1CameraClient` implements the port-6000 TLS socket protocol. `PrinterState.camera_client()` constructs a client on demand. The Slack handler calls it and uploads the JPEG to the thread.

**Tech Stack:** Python stdlib `ssl`/`asyncio`, `struct` for binary packing, attrs for the protocol class, Slack SDK `files_upload_v2` for image upload.

**Design spec:** `docs/specs/2026-03-27-camera-snapshot-design.md`

---

### Task 1: CameraClient Protocol and P1CameraClient — auth packet construction

**Files:**
- Create: `src/souzu/bambu/camera.py`
- Create: `tests/souzu/bambu/test_camera.py`

- [ ] **Step 1: Write the failing test for auth packet construction**

In `tests/souzu/bambu/test_camera.py`:

```python
"""Tests for souzu.bambu.camera."""

import struct

from souzu.bambu.camera import P1CameraClient


class TestP1AuthPacket:
    def test_auth_packet_is_64_bytes(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        assert len(packet) == 64

    def test_auth_packet_header(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        size, ptype = struct.unpack_from("<II", packet, 0)
        assert size == 0x40
        assert ptype == 0x3000

    def test_auth_packet_zero_padding(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        assert packet[8:16] == b"\x00" * 8

    def test_auth_packet_username(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        username = packet[16:48]
        assert username == b"bblp" + b"\x00" * 28

    def test_auth_packet_access_code(self) -> None:
        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        packet = client._build_auth_packet()
        code = packet[48:64]
        assert code == b"12345678" + b"\x00" * 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/bambu/test_camera.py -v`
Expected: FAIL with `ImportError` (module doesn't exist yet)

- [ ] **Step 3: Write the Protocol and P1CameraClient with auth packet**

Create `src/souzu/bambu/camera.py`:

```python
"""Camera client for capturing snapshots from Bambu Lab printers."""

from __future__ import annotations

import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class CameraClient(Protocol):
    """Protocol for capturing a single JPEG frame from a printer's camera."""

    async def capture_frame(self) -> bytes: ...


class P1CameraClient:
    """Camera client for P1/A1-series printers using the port 6000 TLS protocol."""

    CAMERA_PORT = 6000
    TIMEOUT_SECONDS = 10

    def __init__(self, ip_address: str, access_code: str) -> None:
        self._ip_address = ip_address
        self._access_code = access_code

    def _build_auth_packet(self) -> bytes:
        """Build the 64-byte authentication packet for the camera stream."""
        header = struct.pack("<II", 0x40, 0x3000)
        padding = b"\x00" * 8
        username = b"bblp".ljust(32, b"\x00")
        access_code = self._access_code.encode().ljust(16, b"\x00")
        return header + padding + username + access_code

    async def capture_frame(self) -> bytes:
        """Capture a single JPEG frame from the printer's camera."""
        raise NotImplementedError("Will be implemented in the next task")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/bambu/test_camera.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run formatter and type checker**

Run: `uv run ruff format src/souzu/bambu/camera.py tests/souzu/bambu/test_camera.py && uv run ruff check src/souzu/bambu/camera.py tests/souzu/bambu/test_camera.py`

- [ ] **Step 6: Commit**

```bash
git add src/souzu/bambu/camera.py tests/souzu/bambu/test_camera.py
git commit -m "feat: add CameraClient protocol and P1 auth packet construction"
```

---

### Task 2: P1CameraClient — frame capture via TLS socket

**Files:**
- Modify: `src/souzu/bambu/camera.py`
- Modify: `tests/souzu/bambu/test_camera.py`

- [ ] **Step 1: Write failing tests for frame capture**

Add to `tests/souzu/bambu/test_camera.py`:

```python
import asyncio
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_mock import MockerFixture

from souzu.bambu.camera import P1CameraClient

# Minimal valid JPEG: SOI marker + APP0 + EOI marker
_JPEG_SOI = b"\xff\xd8\xff\xe0"
_JPEG_EOI = b"\xff\xd9"
_FAKE_JPEG = _JPEG_SOI + b"\x00" * 100 + _JPEG_EOI


def _make_frame_payload(jpeg_data: bytes) -> bytes:
    """Build a frame as the printer sends it: 16-byte header + JPEG payload."""
    import struct

    header = struct.pack("<I", len(jpeg_data)) + b"\x00" * 12
    return header + jpeg_data


class TestP1CaptureFrame:
    @pytest.mark.asyncio
    async def test_captures_single_jpeg_frame(
        self, mocker: MockerFixture
    ) -> None:
        frame_bytes = _make_frame_payload(_FAKE_JPEG)
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        # First read: 16-byte header; second read: JPEG payload
        mock_reader.readexactly.side_effect = [
            frame_bytes[:16],
            frame_bytes[16:],
        ]
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            return_value=(mock_reader, mock_writer),
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        result = await client.capture_frame()

        assert result == _FAKE_JPEG
        mock_writer.write.assert_called_once()
        mock_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_raises(self, mocker: MockerFixture) -> None:
        async def hang(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(999)

        mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            side_effect=hang,
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        # Use a very short timeout for the test
        client.TIMEOUT_SECONDS = 0.01
        with pytest.raises(TimeoutError):
            await client.capture_frame()

    @pytest.mark.asyncio
    async def test_connection_refused_raises(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            side_effect=ConnectionRefusedError("Connection refused"),
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        with pytest.raises(ConnectionRefusedError):
            await client.capture_frame()

    @pytest.mark.asyncio
    async def test_ssl_context_disables_verification(
        self, mocker: MockerFixture
    ) -> None:
        frame_bytes = _make_frame_payload(_FAKE_JPEG)
        mock_reader = AsyncMock(spec=asyncio.StreamReader)
        mock_reader.readexactly.side_effect = [
            frame_bytes[:16],
            frame_bytes[16:],
        ]
        mock_writer = MagicMock(spec=asyncio.StreamWriter)
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        mock_open = mocker.patch(
            "souzu.bambu.camera.asyncio.open_connection",
            return_value=(mock_reader, mock_writer),
        )

        client = P1CameraClient(ip_address="192.168.1.100", access_code="12345678")
        await client.capture_frame()

        call_kwargs = mock_open.call_args.kwargs
        ssl_ctx = call_kwargs["ssl"]
        assert isinstance(ssl_ctx, ssl.SSLContext)
        assert ssl_ctx.check_hostname is False
        assert ssl_ctx.verify_mode == ssl.CERT_NONE
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `uv run pytest tests/souzu/bambu/test_camera.py::TestP1CaptureFrame -v`
Expected: FAIL (capture_frame raises NotImplementedError)

- [ ] **Step 3: Implement capture_frame**

Replace the `capture_frame` method and add imports in `src/souzu/bambu/camera.py`:

```python
"""Camera client for capturing snapshots from Bambu Lab printers."""

from __future__ import annotations

import asyncio
import ssl
import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class CameraClient(Protocol):
    """Protocol for capturing a single JPEG frame from a printer's camera."""

    async def capture_frame(self) -> bytes: ...


class P1CameraClient:
    """Camera client for P1/A1-series printers using the port 6000 TLS protocol."""

    CAMERA_PORT = 6000
    TIMEOUT_SECONDS = 10

    def __init__(self, ip_address: str, access_code: str) -> None:
        self._ip_address = ip_address
        self._access_code = access_code

    def _build_auth_packet(self) -> bytes:
        """Build the 64-byte authentication packet for the camera stream."""
        header = struct.pack("<II", 0x40, 0x3000)
        padding = b"\x00" * 8
        username = b"bblp".ljust(32, b"\x00")
        access_code = self._access_code.encode().ljust(16, b"\x00")
        return header + padding + username + access_code

    async def capture_frame(self) -> bytes:
        """Capture a single JPEG frame from the printer's camera.

        Opens a TLS connection to the printer's camera port, sends the
        authentication packet, reads one complete JPEG frame, and returns it.

        Raises:
            TimeoutError: If the operation exceeds TIMEOUT_SECONDS.
            ConnectionError: If the printer is unreachable.
            ssl.SSLError: If TLS negotiation fails.
        """
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        async with asyncio.timeout(self.TIMEOUT_SECONDS):
            reader, writer = await asyncio.open_connection(
                host=self._ip_address,
                port=self.CAMERA_PORT,
                ssl=ssl_ctx,
            )
            try:
                writer.write(self._build_auth_packet())
                await writer.drain()

                # Read one frame: 16-byte header followed by JPEG payload
                header = await reader.readexactly(16)
                payload_size = struct.unpack_from("<I", header, 0)[0]
                jpeg_data = await reader.readexactly(payload_size)
                return jpeg_data
            finally:
                writer.close()
                await writer.wait_closed()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/bambu/test_camera.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run formatter and type checker**

Run: `uv run ruff format src/souzu/bambu/camera.py tests/souzu/bambu/test_camera.py && uv run ruff check src/souzu/bambu/camera.py tests/souzu/bambu/test_camera.py`

- [ ] **Step 6: Commit**

```bash
git add src/souzu/bambu/camera.py tests/souzu/bambu/test_camera.py
git commit -m "feat: implement P1 camera frame capture via TLS socket"
```

---

### Task 3: PrinterState.camera_client() method

**Files:**
- Modify: `src/souzu/bambu/camera.py` (only if needed for imports)
- Modify: `src/souzu/job_tracking.py:92-95`
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/souzu/test_job_tracking.py`. First read it to find the right place to add tests — add a new test class at the end:

```python
from unittest.mock import MagicMock
from souzu.bambu.camera import P1CameraClient
from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import BambuMqttConnection
from souzu.config import PrinterConfig


class TestPrinterStateCameraClient:
    def _make_device(self) -> BambuDevice:
        return BambuDevice(
            device_id="SERIAL123",
            device_name="Test Printer",
            ip_address="192.168.1.100",
            filename_prefix="test",
        )

    def test_returns_p1_camera_client_when_connection_exists(
        self, mocker: MockerFixture
    ) -> None:
        device = self._make_device()
        mock_conn = MagicMock(spec=BambuMqttConnection)
        mock_conn.device = device
        mock_conn.access_code = "12345678"
        state = PrinterState(connection=mock_conn)

        client = state.camera_client()

        assert isinstance(client, P1CameraClient)

    def test_returns_none_when_no_connection(self) -> None:
        state = PrinterState(connection=None)
        assert state.camera_client() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::TestPrinterStateCameraClient -v`
Expected: FAIL with `AttributeError` (no `camera_client` method)

- [ ] **Step 3: Implement camera_client() on PrinterState**

In `src/souzu/job_tracking.py`, add the import near the top (after the existing bambu imports):

```python
from souzu.bambu.camera import CameraClient, P1CameraClient
```

Then add the method to the `PrinterState` class (after line 95):

```python
@define
class PrinterState:
    current_job: PrintJob | None = None
    connection: BambuMqttConnection | None = None

    def camera_client(self) -> CameraClient | None:
        """Construct a camera client for this printer, or None if unavailable."""
        if self.connection is None:
            return None
        return P1CameraClient(
            ip_address=self.connection.device.ip_address,
            access_code=self.connection.access_code,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::TestPrinterStateCameraClient -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 6: Run formatter and type checker**

Run: `uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py && uv run ruff check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py`

- [ ] **Step 7: Commit**

```bash
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add camera_client() method to PrinterState"
```

---

### Task 4: Wire Photo action in Slack handler

**Files:**
- Modify: `src/souzu/slack/handlers.py:138-141`
- Modify: `tests/souzu/slack/test_handlers.py`

- [ ] **Step 1: Write failing tests for Photo action**

In `tests/souzu/slack/test_handlers.py`, replace the existing `test_photo_stays_as_stub` test and add new Photo tests in `TestActionHandlers`:

```python
    @pytest.mark.asyncio
    async def test_photo_captures_and_uploads(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
        mocker: MockerFixture,
    ) -> None:
        """Photo action captures a frame and uploads it to the thread."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        mock_camera = AsyncMock()
        mock_camera.capture_frame.return_value = b"\xff\xd8fake_jpeg\xff\xd9"
        mocker.patch.object(
            PrinterState, "camera_client", return_value=mock_camera
        )

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_camera.capture_frame.assert_awaited_once()
        mock_client.files_upload_v2.assert_awaited_once()
        upload_kwargs = mock_client.files_upload_v2.call_args.kwargs
        assert upload_kwargs["channel"] == "C456"
        assert upload_kwargs["thread_ts"] == thread_ts
        assert upload_kwargs["filename"] == "snapshot.jpg"
        assert upload_kwargs["content"] == b"\xff\xd8fake_jpeg\xff\xd9"
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_photo_camera_unavailable(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
        mocker: MockerFixture,
    ) -> None:
        """Photo with no camera client sends ephemeral error."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        mocker.patch.object(
            PrinterState, "camera_client", return_value=None
        )

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "unavailable" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        mock_client.files_upload_v2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_photo_capture_error(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
        mocker: MockerFixture,
    ) -> None:
        """Photo capture failure sends ephemeral error."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        mock_camera = AsyncMock()
        mock_camera.capture_frame.side_effect = TimeoutError("timed out")
        mocker.patch.object(
            PrinterState, "camera_client", return_value=mock_camera
        )

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "unavailable" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        mock_client.files_upload_v2.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/slack/test_handlers.py::TestActionHandlers::test_photo_captures_and_uploads tests/souzu/slack/test_handlers.py::TestActionHandlers::test_photo_camera_unavailable tests/souzu/slack/test_handlers.py::TestActionHandlers::test_photo_capture_error -v`
Expected: FAIL (old stub behavior doesn't match new assertions)

- [ ] **Step 3: Implement Photo handler in slack/handlers.py**

Replace lines 138-141 in `src/souzu/slack/handlers.py`:

```python
            # Photo: capture and upload a camera snapshot
            if action == JobAction.PHOTO:
                camera = state.camera_client()
                if camera is None:
                    await _ephemeral(
                        "Couldn't capture a photo — the camera may be unavailable."
                    )
                    return

                try:
                    jpeg_data = await camera.capture_frame()
                except Exception:
                    logging.exception("Failed to capture camera frame")
                    await _ephemeral(
                        "Couldn't capture a photo — the camera may be unavailable."
                    )
                    return

                try:
                    channel = job.slack_channel or body["channel"]["id"]
                    thread = job.slack_thread_ts or parent_ts
                    await client.files_upload_v2(
                        channel=channel,
                        thread_ts=thread,
                        content=jpeg_data,
                        filename="snapshot.jpg",
                    )
                except Exception:
                    logging.exception("Failed to upload camera snapshot to Slack")
                    await _ephemeral("Captured a photo but failed to upload it.")
                return
```

- [ ] **Step 4: Remove the old `test_photo_stays_as_stub` test**

Delete the `test_photo_stays_as_stub` method from `tests/souzu/slack/test_handlers.py` (it tested the stub behavior which no longer exists).

- [ ] **Step 5: Add missing imports to test file**

Ensure `tests/souzu/slack/test_handlers.py` has:

```python
from pytest_mock import MockerFixture
from souzu.job_tracking import JobRegistry, JobState, PrinterState, PrintJob
```

(`PrinterState` should already be imported; add `MockerFixture` if missing.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/slack/test_handlers.py -v`
Expected: All tests PASS

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 8: Run formatter and type checker**

Run: `uv run ruff format src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py && uv run ruff check src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py`

- [ ] **Step 9: Commit**

```bash
git add src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
git commit -m "feat: wire Photo button to capture and upload camera snapshots"
```

---

### Task 5: Final validation

**Files:** None (read-only verification)

- [ ] **Step 1: Run full test suite with coverage**

Run: `uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Run all pre-commit checks**

Run: `uv run prek run --all-files`
Expected: All checks PASS

- [ ] **Step 3: Run type checkers**

Run: `uv run mypy && uv run pyright`
Expected: No errors
