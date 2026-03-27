# Remote Control Design

## Goal

Enable souzu to pause, resume, and cancel prints on Bambu Lab printers via Slack action buttons. This replaces the current "coming soon" stubs with real MQTT command publishing.

## MQTT Command Publishing

### Protocol

Per the [OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI/blob/main/mqtt.md), commands are published to `device/{serial}/request`:

```json
{
  "print": {
    "sequence_id": "0",
    "command": "pause"
  }
}
```

Commands: `pause`, `resume`, `stop` (the API uses "stop", not "cancel").

The `sequence_id` is a string counter that increments per command. Responses arrive on the existing `device/{serial}/report` subscription with a matching `sequence_id` and `"result": "success"`.

### No response tracking

We do not track command responses. The printer's status report stream (already subscribed) will reflect state changes through the normal monitoring path. If a command fails silently, buttons remain in their current state and the user can retry.

### Changes to `BambuMqttConnection`

Add instance state:

- `_sequence_id: int = 0` — counter for command sequencing
- Store `self._client` reference when connected inside `_consume_messages`, clear on disconnect

New methods:

```python
async def send_command(self, command_type: str, payload: dict[str, str]) -> None:
    """Publish a command to the printer's request topic.

    Raises RuntimeError if the printer is not connected.
    """

async def pause(self) -> None:
    await self.send_command("print", {"command": "pause"})

async def resume(self) -> None:
    await self.send_command("print", {"command": "resume"})

async def stop(self) -> None:
    await self.send_command("print", {"command": "stop"})
```

`send_command` raises `RuntimeError` if `self._client` is `None` (not connected). The high-level methods are thin wrappers.

### Client lifecycle in `_consume_messages`

The `Client` is currently created inside a `while True` reconnect loop and not stored. Change to:

1. Set `self._client = client` after `async with client:` enters
2. Clear `self._client = None` when the context exits (disconnect/error)

This makes `_client` available for publishing while connected, and `None` during reconnect gaps.

## PrinterState Changes

Add `connection: BambuMqttConnection | None = None` to `PrinterState`.

This field is a runtime reference, not persistent state. Exclude it from serialization by registering a cattrs hook that omits it on unstructure and defaults it to `None` on structure.

Set in `monitor.py:inner_loop` after creating the connection, before spawning `monitor_printer_status`.

## Handler Changes

### Command dispatch

Replace the stub ephemeral response in `_make_action_handler` with:

1. Get `state.connection` from the registry lookup.
2. If connection is `None`, send **ephemeral**: "Printer is offline."
3. Call the appropriate method (`connection.pause()`, `.resume()`, `.stop()`) in a try/except.
4. On success, post a **non-ephemeral thread reply**: "{Action} requested by <@user_id>" (e.g. "Pause requested by <@user_id>"). This creates an audit trail and correctly attributes actions regardless of who triggered them.
5. On `RuntimeError` (disconnected) or `MqttError`, send **ephemeral**: "Failed to send command to printer."

The `PHOTO` action remains a stub — it requires a different mechanism (camera stream) and is out of scope.

### Action-to-method mapping

```python
_ACTION_COMMANDS: dict[JobAction, str] = {
    JobAction.PAUSE: "pause",
    JobAction.RESUME: "resume",
    JobAction.CANCEL: "stop",
}

_ACTION_NAMES: dict[JobAction, str] = {
    JobAction.PAUSE: "Pause",
    JobAction.RESUME: "Resume",
    JobAction.CANCEL: "Cancel",
}
```

### Handler access to connection

The handler gets `state` from `job_registry[parent_ts]`, then `state.connection`. The handler also needs to post non-ephemeral thread replies on success, using the Bolt `client` (already available in the handler signature) with `job.slack_channel` and `job.slack_thread_ts`.

## Cancel Confirmation Dialog

Add a `confirm` object to the Cancel button in `build_actions_blocks`:

```json
{
    "type": "button",
    "text": {"type": "plain_text", "text": "Cancel"},
    "action_id": "print_cancel",
    "style": "danger",
    "confirm": {
        "title": {"type": "plain_text", "text": "Cancel print?"},
        "text": {"type": "plain_text", "text": "This will stop the print and cannot be undone."},
        "confirm": {"type": "plain_text", "text": "Cancel print"},
        "deny": {"type": "plain_text", "text": "Keep printing"}
    }
}
```

Slack handles the confirmation dialog client-side. The `print_cancel` handler only fires if the user confirms.

## Activating the Buttons

`build_actions_blocks` currently returns a "coming soon" context block. Replace with actual `actions` blocks containing buttons. This is the gate that enables the feature.

Button rendering per action:

| Action | Label | Style | Confirm |
|---|---|---|---|
| Pause | "Pause" | (none) | No |
| Resume | "Resume" | (none) | No |
| Cancel | "Cancel" | `danger` | Yes (see above) |
| Photo | "Photo" | (none) | No |

## Files Affected

### Modify

- `src/souzu/bambu/mqtt.py` — store `_client` reference, add `_sequence_id`, `send_command()`, `pause()`, `resume()`, `stop()`
- `src/souzu/job_tracking.py` — add `connection` to `PrinterState` (excluded from serialization), replace stub `build_actions_blocks` with real buttons
- `src/souzu/slack/handlers.py` — replace stub response with command dispatch, non-ephemeral success messages, ephemeral error messages
- `src/souzu/commands/monitor.py` — set `state.connection` when setting up printer monitoring
- `tests/souzu/bambu/test_mqtt.py` — tests for `send_command`, `pause`, `resume`, `stop`, disconnected error
- `tests/souzu/test_job_tracking.py` — tests for real button blocks, confirm dialog on cancel
- `tests/souzu/slack/test_handlers.py` — tests for command dispatch, success messages, error handling, photo stub

## Out of Scope

- Command response tracking / acknowledgment
- Photo action implementation (camera stream)
- Broadening authorization beyond claimed owner
- Config toggle for read-only vs. control mode
