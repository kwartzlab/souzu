# Camera Snapshot Feature Design

## Overview

Add the ability to capture a single JPEG snapshot from a Bambu Lab printer's built-in camera on demand, triggered via the existing "Photo" button in Slack print notifications.

## Background

Bambu printers expose their camera via two different protocols depending on the model series:

- **P1/A1 series** (P1P, P1S, A1, A1M): Proprietary TLS socket protocol on port 6000. The printer streams raw JPEG frames, each preceded by a 16-byte binary header. No RTSP support.
- **X1/H2 series** (X1C, X1E, H2S, H2D, P2S): Standard RTSPS on port 322 (`rtsps://bblp:<access_code>@<ip>:322/streaming/live/1`).

This design implements P1/A1 support only (the deployed fleet), with an abstraction layer to support adding RTSP later.

## Architecture

### Camera Client Abstraction

A `CameraClient` Protocol in `src/souzu/bambu/camera.py`:

```python
class CameraClient(Protocol):
    async def capture_frame(self) -> bytes:
        """Capture a single JPEG frame from the printer's camera."""
        ...
```

Returns raw JPEG bytes on success. Raises on failure. No retry logic — callers decide how to handle errors.

### P1CameraClient

Concrete implementation for P1/A1 printers. Constructor takes `ip_address: str` and `access_code: str`.

#### Wire protocol

1. Open a TLS connection to `<ip>:6000` with certificate verification disabled (printer uses a self-signed cert).
2. Send a 64-byte authentication packet:
   - Bytes 0-3: `0x00000040` (little-endian) — packet size
   - Bytes 4-7: `0x00003000` (little-endian) — packet type
   - Bytes 8-15: zero padding
   - Bytes 16-47: username `bblp` null-padded to 32 bytes
   - Bytes 48-63: access code null-padded to 32 bytes
3. Read frames in a loop:
   - 16-byte header: first 4 bytes are payload size (little-endian)
   - Payload: a complete JPEG image (starts with `FF D8`, ends with `FF D9`)
4. Return the first complete JPEG frame's bytes and close the connection.

The entire operation is wrapped in a 10-second `asyncio.timeout`.

### Integration with PrinterState

`PrinterState` gains a method:

```python
def camera_client(self) -> CameraClient | None:
```

Constructs a `P1CameraClient` on demand using the printer's IP address (from `BambuDevice`, which gets it via SSDP discovery) and access code (from `CONFIG.printers[device_id]`). Returns `None` if the connection is missing or credentials are unavailable. No persistent state — the client is created fresh per snapshot request.

### Slack Handler

The existing Photo stub in `slack/handlers.py` (lines 138-141) is replaced:

1. Get `state.camera_client()`. If `None`, send ephemeral "Printer is offline."
2. Call `await camera_client.capture_frame()`.
3. On success: upload the JPEG to the Slack thread via `client.files_upload_v2(channel=..., thread_ts=..., file=jpeg_bytes, filename="snapshot.jpg")`.
4. On failure: log the exception, send ephemeral "Couldn't capture a photo — the camera may be unavailable."

The Photo action remains subject to the `can_control_job` authorization check, same as pause/resume/cancel.

## Error Handling

`P1CameraClient.capture_frame()` may raise:

- `ConnectionError` — printer offline, wrong IP, connection refused
- `TimeoutError` — 10-second deadline expired (LAN liveview disabled, no frames arriving, connection dropped mid-frame)
- `ssl.SSLError` — TLS negotiation failure

The Slack handler catches all exceptions broadly, logs the traceback, and sends a generic ephemeral error message. No error-type-specific messaging for the user at this stage.

### Edge cases

- **No IP address in config or device**: `camera_client()` returns `None`, treated as offline.
- **Partial JPEG read** (connection drops mid-frame): the 10-second timeout covers this; incomplete data is discarded.
- **Multiple simultaneous Photo requests**: each opens its own short-lived connection. No shared state, no contention.

## Future Extension: RTSP Support

When X1/H2 support is needed, add an `RtspCameraClient` implementing the same `CameraClient` protocol. It would use ffmpeg (via subprocess or PyAV) to grab a single frame from the RTSPS URL. `PrinterState.camera_client()` would select the appropriate implementation based on printer model (detectable from MQTT data).

## Testing

### Unit tests

- **`P1CameraClient`**: Mock the TLS socket to verify:
  - Correct 64-byte auth packet contents (header bytes, username padding, access code padding)
  - JPEG frame extraction from 16-byte header + payload
  - Timeout behavior (mock that never sends data)
  - Connection error propagation

- **`PrinterState.camera_client()`**: Verify returns `P1CameraClient` when connection and config are present, `None` when missing.

- **Slack handler**: Mock `camera_client()` and `files_upload_v2` to verify:
  - Successful snapshot uploads to the correct channel/thread
  - Error cases produce ephemeral messages
  - Authorization check applies to Photo action

No integration tests against a real printer. Real-device testing is manual.
