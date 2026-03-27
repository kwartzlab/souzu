# Remote Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable souzu to pause, resume, and cancel prints on Bambu Lab printers via Slack action buttons, replacing the current "coming soon" stubs with real MQTT command publishing.

**Architecture:** `BambuMqttConnection` gains a `send_command` method that publishes JSON to the printer's MQTT request topic. `PrinterState` stores a reference to the connection. Slack action handlers use that reference to dispatch commands, posting non-ephemeral audit trail messages on success and ephemeral messages on failure.

**Tech Stack:** Python 3.12+, aiomqtt, slack-bolt, attrs, pytest, pytest-asyncio

**Design spec:** `docs/specs/2026-03-27-remote-control-design.md`

---

## File Structure

**Modify:**
- `src/souzu/bambu/mqtt.py` — add `_sequence_id`, `_client` storage, `send_command()`, `pause()`, `resume()`, `stop()`
- `src/souzu/job_tracking.py` — add `connection` to `PrinterState` (excluded from serialization), replace stub `build_actions_blocks` with real buttons including cancel confirmation
- `src/souzu/slack/handlers.py` — replace stub response with command dispatch, non-ephemeral success messages, ephemeral error messages
- `src/souzu/commands/monitor.py` — set `state.connection` on `PrinterState` when setting up printer monitoring
- `tests/souzu/bambu/test_mqtt.py` — new file, tests for `send_command`, `pause`, `resume`, `stop`, disconnected error
- `tests/souzu/test_job_tracking.py` — update tests for real button blocks, cancel confirmation dialog
- `tests/souzu/slack/test_handlers.py` — update tests for command dispatch, success messages, error handling

---

### Task 1: Add MQTT command publishing to BambuMqttConnection

**Files:**
- Create: `tests/souzu/bambu/test_mqtt.py`
- Modify: `src/souzu/bambu/mqtt.py:215-319` (BambuMqttConnection class)

- [ ] **Step 1: Write tests for send_command, pause, resume, stop**

Create `tests/souzu/bambu/test_mqtt.py`:

```python
"""Tests for souzu.bambu.mqtt command publishing."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from souzu.bambu.mqtt import BambuMqttConnection


@pytest.fixture
def mock_connection() -> BambuMqttConnection:
    """Create a BambuMqttConnection with mocked dependencies."""
    mock_tg = MagicMock()
    mock_device = MagicMock()
    mock_device.device_id = "SERIAL123"
    mock_device.device_name = "Test Printer"
    mock_device.ip_address = "192.168.1.100"

    with patch("souzu.bambu.mqtt.CONFIG") as mock_config:
        mock_config.printers = {
            "SERIAL123": MagicMock(access_code="test_code"),
        }
        conn = BambuMqttConnection(mock_tg, mock_device)
    return conn


class TestSendCommand:
    @pytest.mark.asyncio
    async def test_publishes_to_request_topic(
        self, mock_connection: BambuMqttConnection
    ) -> None:
        mock_client = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.send_command("print", {"command": "pause"})

        mock_client.publish.assert_awaited_once()
        topic = mock_client.publish.call_args.args[0]
        assert topic == "device/SERIAL123/request"
        payload = json.loads(mock_client.publish.call_args.args[1])
        assert payload == {
            "print": {"sequence_id": "0", "command": "pause"},
        }

    @pytest.mark.asyncio
    async def test_increments_sequence_id(
        self, mock_connection: BambuMqttConnection
    ) -> None:
        mock_client = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.send_command("print", {"command": "pause"})
        await mock_connection.send_command("print", {"command": "resume"})

        payloads = [
            json.loads(call.args[1])
            for call in mock_client.publish.call_args_list
        ]
        assert payloads[0]["print"]["sequence_id"] == "0"
        assert payloads[1]["print"]["sequence_id"] == "1"

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(
        self, mock_connection: BambuMqttConnection
    ) -> None:
        assert mock_connection._client is None
        with pytest.raises(RuntimeError, match="Not connected"):
            await mock_connection.send_command("print", {"command": "pause"})


class TestConvenienceMethods:
    @pytest.mark.asyncio
    async def test_pause(self, mock_connection: BambuMqttConnection) -> None:
        mock_client = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.pause()

        payload = json.loads(mock_client.publish.call_args.args[1])
        assert payload["print"]["command"] == "pause"

    @pytest.mark.asyncio
    async def test_resume(self, mock_connection: BambuMqttConnection) -> None:
        mock_client = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.resume()

        payload = json.loads(mock_client.publish.call_args.args[1])
        assert payload["print"]["command"] == "resume"

    @pytest.mark.asyncio
    async def test_stop(self, mock_connection: BambuMqttConnection) -> None:
        mock_client = AsyncMock()
        mock_connection._client = mock_client

        await mock_connection.stop()

        payload = json.loads(mock_client.publish.call_args.args[1])
        assert payload["print"]["command"] == "stop"
```

- [ ] **Step 2: Create `tests/souzu/bambu/__init__.py` if it doesn't exist**

Run: `ls tests/souzu/bambu/__init__.py 2>/dev/null || touch tests/souzu/bambu/__init__.py`

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/bambu/test_mqtt.py -v --no-cov`
Expected: Failures — `send_command`, `pause`, `resume`, `stop` don't exist yet.

- [ ] **Step 4: Implement send_command and convenience methods**

In `src/souzu/bambu/mqtt.py`, add to `BambuMqttConnection.__init__` (after `self._queues = list[...]()` on line 234):

```python
        self._sequence_id: int = 0
```

Store the client reference in `_consume_messages`. Replace the `async with client:` block (lines 299-315) with:

```python
            try:
                async with client:
                    self._client = client
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
            finally:
                self._client = None
```

Add the following methods to `BambuMqttConnection`, after the `subscribe` method:

```python
    async def send_command(
        self, command_type: str, payload: dict[str, str]
    ) -> None:
        """Publish a command to the printer's MQTT request topic.

        Raises RuntimeError if the printer is not connected.
        """
        if self._client is None:
            raise RuntimeError("Not connected to printer")
        message = {
            command_type: {
                "sequence_id": str(self._sequence_id),
                **payload,
            },
        }
        await self._client.publish(
            f"device/{self.device.device_id}/request",
            json.dumps(message),
        )
        self._sequence_id += 1

    async def pause(self) -> None:
        """Send a pause command to the printer."""
        await self.send_command("print", {"command": "pause"})

    async def resume(self) -> None:
        """Send a resume command to the printer."""
        await self.send_command("print", {"command": "resume"})

    async def stop(self) -> None:
        """Send a stop (cancel) command to the printer."""
        await self.send_command("print", {"command": "stop"})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/bambu/test_mqtt.py -v --no-cov`
Expected: All pass.

- [ ] **Step 6: Run formatter and full test suite**

Run: `uv run ruff format src/souzu/bambu/mqtt.py tests/souzu/bambu/test_mqtt.py && uv run pytest --no-cov`

- [ ] **Step 7: Commit**

```bash
git add tests/souzu/bambu/__init__.py tests/souzu/bambu/test_mqtt.py src/souzu/bambu/mqtt.py
git commit -m "feat: add MQTT command publishing to BambuMqttConnection

Add send_command(), pause(), resume(), and stop() methods for
publishing control commands to Bambu printers via MQTT."
```

---

### Task 2: Add connection to PrinterState and exclude from serialization

**Files:**
- Modify: `src/souzu/job_tracking.py:67-93` (PrintJob/PrinterState definitions and serializer)
- Modify: `tests/souzu/test_job_tracking.py` (serialization round-trip test)

- [ ] **Step 1: Write test for connection field and serialization exclusion**

Add to `tests/souzu/test_job_tracking.py`, after the existing `test_printer_state` test:

```python
def test_printer_state_connection_default() -> None:
    """Test that PrinterState.connection defaults to None."""
    state = PrinterState()
    assert state.connection is None


def test_printer_state_connection_excluded_from_serialization() -> None:
    """Test that connection is excluded from serialization round-trip."""
    import json

    from souzu.bambu.mqtt import BambuMqttConnection
    from souzu.job_tracking import _STATE_SERIALIZER

    mock_conn = MagicMock(spec=BambuMqttConnection)
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    state = PrinterState(current_job=job, connection=mock_conn)

    unstructured = _STATE_SERIALIZER.unstructure(state)
    assert "connection" not in unstructured

    json_str = json.dumps(unstructured)
    json_loaded = json.loads(json_str)
    restored = _STATE_SERIALIZER.structure(json_loaded, PrinterState)
    assert restored.connection is None
```

Add the `MagicMock` import if not already present (it is already imported in this test file).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_printer_state_connection_default tests/souzu/test_job_tracking.py::test_printer_state_connection_excluded_from_serialization -v --no-cov`
Expected: Failures — `connection` field doesn't exist yet.

- [ ] **Step 3: Add connection field to PrinterState and serialization hooks**

In `src/souzu/job_tracking.py`, modify the `PrinterState` class and add serialization hooks.

Add a `TYPE_CHECKING` import at the top of the file (after the existing imports):

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from souzu.bambu.mqtt import BambuMqttConnection
```

(Remove the existing `from typing import Any` if present, since we're consolidating.)

Update `PrinterState` to include the connection field:

```python
@define
class PrinterState:
    current_job: PrintJob | None = None
    connection: BambuMqttConnection | None = None
```

Add serialization hooks after the `PrinterState` class definition (before `_round_up`). These exclude `connection` from serialization and default it to `None` on structure. Use `cattrs.gen.make_dict_unstructure_fn` and `cattrs.gen.override` to omit the field cleanly:

```python
from cattrs.gen import make_dict_unstructure_fn, override as cattrs_override

_STATE_SERIALIZER.register_unstructure_hook(
    PrinterState,
    make_dict_unstructure_fn(
        PrinterState,
        _STATE_SERIALIZER,
        connection=cattrs_override(omit=True),
    ),
)
```

No custom structure hook is needed — cattrs will ignore the missing `connection` key and use the default `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v --no-cov`
Expected: All pass, including the existing serialization round-trip test.

- [ ] **Step 5: Run formatter and full test suite**

Run: `uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py && uv run pytest --no-cov`

- [ ] **Step 6: Commit**

```bash
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add connection field to PrinterState

Store a runtime reference to BambuMqttConnection on PrinterState,
excluded from serialization since it's not persistent state."
```

---

### Task 3: Replace stub action blocks with real buttons

**Files:**
- Modify: `src/souzu/job_tracking.py:170-212` (build_actions_blocks and related)
- Modify: `tests/souzu/test_job_tracking.py` (button block tests)

- [ ] **Step 1: Write tests for real action buttons**

Replace the existing `test_build_actions_blocks_running`, `test_build_actions_blocks_paused`, and `test_build_actions_blocks_empty` tests in `tests/souzu/test_job_tracking.py` with:

```python
def test_build_actions_blocks_running() -> None:
    """Running job gets Pause, Cancel (with confirm), and Photo buttons."""
    actions = [JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO]
    blocks = build_actions_blocks(actions)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "actions"
    elements = blocks[0]["elements"]
    assert len(elements) == 3
    assert elements[0]["action_id"] == "print_pause"
    assert elements[0]["text"]["text"] == "Pause"
    assert "style" not in elements[0]
    assert elements[1]["action_id"] == "print_cancel"
    assert elements[1]["style"] == "danger"
    assert "confirm" in elements[1]
    assert elements[2]["action_id"] == "print_photo"


def test_build_actions_blocks_paused() -> None:
    """Paused job gets Resume, Cancel (with confirm), and Photo buttons."""
    actions = [JobAction.RESUME, JobAction.CANCEL, JobAction.PHOTO]
    blocks = build_actions_blocks(actions)
    elements = blocks[0]["elements"]
    assert elements[0]["action_id"] == "print_resume"
    assert elements[1]["action_id"] == "print_cancel"
    assert "confirm" in elements[1]


def test_build_actions_blocks_empty() -> None:
    blocks = build_actions_blocks([])
    assert blocks == []


def test_build_actions_blocks_cancel_confirm_dialog() -> None:
    """Cancel button has a confirmation dialog with expected text."""
    actions = [JobAction.CANCEL]
    blocks = build_actions_blocks(actions)
    cancel_btn = blocks[0]["elements"][0]
    confirm = cancel_btn["confirm"]
    assert confirm["title"]["text"] == "Cancel print?"
    assert "cannot be undone" in confirm["text"]["text"]
    assert confirm["confirm"]["text"] == "Cancel print"
    assert confirm["deny"]["text"] == "Keep printing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_build_actions_blocks_running -v --no-cov`
Expected: Failure — blocks still return context type, not actions type.

- [ ] **Step 3: Replace build_actions_blocks implementation**

In `src/souzu/job_tracking.py`, replace the `build_actions_blocks` function:

```python
_CANCEL_CONFIRM = {
    "title": {"type": "plain_text", "text": "Cancel print?"},
    "text": {
        "type": "plain_text",
        "text": "This will stop the print and cannot be undone.",
    },
    "confirm": {"type": "plain_text", "text": "Cancel print"},
    "deny": {"type": "plain_text", "text": "Keep printing"},
}


def build_actions_blocks(actions: list[JobAction]) -> list[dict[str, Any]]:
    """Build Block Kit blocks for the actions message."""
    if not actions:
        return []
    elements: list[dict[str, Any]] = []
    for action in actions:
        button: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": _ACTION_LABELS[action]},
            "action_id": f"print_{action.value}",
        }
        if action in _ACTION_STYLES:
            button["style"] = _ACTION_STYLES[action]
        if action == JobAction.CANCEL:
            button["confirm"] = _CANCEL_CONFIRM
        elements.append(button)
    return [{"type": "actions", "elements": elements}]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v --no-cov`
Expected: All pass. Note: `test_job_started_posts_actions_message` may need updating since it asserts on the old context block format. If it fails, update its assertion from checking `"Pause" in ...["elements"][0]["text"]` to checking `blocks[0]["type"] == "actions"`.

- [ ] **Step 5: Run formatter and full test suite**

Run: `uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py && uv run pytest --no-cov`

- [ ] **Step 6: Commit**

```bash
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: replace stub action blocks with real Slack buttons

Activate pause, resume, cancel, and photo buttons in print job threads.
Cancel button includes a confirmation dialog."
```

---

### Task 4: Wire Slack handlers to MQTT commands

**Files:**
- Modify: `src/souzu/slack/handlers.py` (replace stub with command dispatch)
- Modify: `tests/souzu/slack/test_handlers.py` (update and add handler tests)

- [ ] **Step 1: Write tests for command dispatch**

In `tests/souzu/slack/test_handlers.py`, add `BambuMqttConnection` import and update the `job_registry_with_job` fixture and the `TestActionHandlers` class.

Add import at top:

```python
from souzu.bambu.mqtt import BambuMqttConnection
```

Update the `job_registry_with_job` fixture to include a mock connection:

```python
@pytest.fixture
def job_registry_with_job() -> tuple[JobRegistry, str, AsyncMock]:
    thread_ts = "1234567890.123456"
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    mock_conn = AsyncMock(spec=BambuMqttConnection)
    state = PrinterState(current_job=job, connection=mock_conn)
    registry: JobRegistry = {thread_ts: state}
    return registry, thread_ts, mock_conn
```

Update **all existing tests** that use `job_registry_with_job` to unpack three values instead of two. For example, change:

```python
registry, thread_ts = job_registry_with_job
```

to:

```python
registry, thread_ts, _mock_conn = job_registry_with_job
```

Replace `TestActionHandlers` with:

```python
class TestActionHandlers:
    @pytest.mark.asyncio
    async def test_pause_sends_command_and_posts_audit(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Pause by owner sends MQTT pause and posts audit trail message."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"
        state.current_job.slack_channel = "C456"
        state.current_job.slack_thread_ts = thread_ts

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_conn.pause.assert_awaited_once()
        # Non-ephemeral audit trail message
        mock_client.chat_postMessage.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert post_kwargs["thread_ts"] == thread_ts
        assert "Pause" in post_kwargs["text"]
        assert "<@U_OWNER>" in post_kwargs["text"]
        # No ephemeral message on success
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_sends_command(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Resume by owner sends MQTT resume."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.state = JobState.PAUSED
        state.current_job.owner = "U_OWNER"
        state.current_job.slack_channel = "C456"
        state.current_job.slack_thread_ts = thread_ts

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_resume"](ack=mock_ack, body=body, client=mock_client)

        mock_conn.resume.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert "Resume" in post_kwargs["text"]

    @pytest.mark.asyncio
    async def test_cancel_sends_stop_command(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Cancel by owner sends MQTT stop."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"
        state.current_job.slack_channel = "C456"
        state.current_job.slack_thread_ts = thread_ts

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_cancel"](ack=mock_ack, body=body, client=mock_client)

        mock_conn.stop.assert_awaited_once()
        post_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert "Cancel" in post_kwargs["text"]

    @pytest.mark.asyncio
    async def test_printer_offline_sends_ephemeral(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """When connection is None, send ephemeral offline message."""
        registry, thread_ts, _mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"
        state.connection = None

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "offline" in mock_client.chat_postEphemeral.call_args.kwargs["text"].lower()
        mock_client.chat_postMessage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mqtt_error_sends_ephemeral(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """When MQTT command raises, send ephemeral error message."""
        registry, thread_ts, mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"
        mock_conn.pause.side_effect = RuntimeError("Not connected")

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "failed" in mock_client.chat_postEphemeral.call_args.kwargs["text"].lower()
        mock_client.chat_postMessage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_photo_stays_as_stub(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Photo action still returns stub response."""
        registry, thread_ts, _mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_photo"](ack=mock_ack, body=body, client=mock_client)

        mock_client.chat_postEphemeral.assert_awaited_once()
        assert "implemented yet" in mock_client.chat_postEphemeral.call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_unauthorized_rejection(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Non-owner gets 'not your print' rejection."""
        registry, thread_ts, _mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_STRANGER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_awaited_once()
        assert (
            "isn't your print"
            in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        )

    @pytest.mark.asyncio
    async def test_wrong_state(
        self,
        job_registry_with_job: tuple[JobRegistry, str, AsyncMock],
    ) -> None:
        """Pause on an already-paused job sends 'not available' ephemeral."""
        registry, thread_ts, _mock_conn = job_registry_with_job
        state = registry[thread_ts]
        assert state.current_job is not None
        state.current_job.state = JobState.PAUSED
        state.current_job.owner = "U_OWNER"

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts, user_id="U_OWNER")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_awaited_once()
        assert (
            "isn't available right now"
            in mock_client.chat_postEphemeral.call_args.kwargs["text"]
        )

    @pytest.mark.asyncio
    async def test_unknown_job(self) -> None:
        """Action on unknown thread returns silently."""
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, {})

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body("unknown.ts")

        await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_current_job(self) -> None:
        """Action when current_job is None returns silently."""
        thread_ts = "1234567890.123456"
        registry: JobRegistry = {thread_ts: PrinterState(current_job=None)}

        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, registry)

        mock_ack = AsyncMock()
        mock_client = AsyncMock()
        body = _make_action_body(thread_ts)

        await handlers["print_cancel"](ack=mock_ack, body=body, client=mock_client)

        mock_ack.assert_awaited_once()
        mock_client.chat_postEphemeral.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_four_action_handlers_registered(self) -> None:
        """Verify all four action handlers are registered."""
        _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
        register_job_handlers(mock_slack, {})

        assert "print_pause" in handlers
        assert "print_resume" in handlers
        assert "print_cancel" in handlers
        assert "print_photo" in handlers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/slack/test_handlers.py::TestActionHandlers::test_pause_sends_command_and_posts_audit -v --no-cov`
Expected: Failure — handler still sends stub ephemeral, not MQTT command.

- [ ] **Step 3: Implement command dispatch in handlers**

Replace `src/souzu/slack/handlers.py` with:

```python
"""Slack interactive event handlers bridging Slack events to domain logic."""

import logging
from typing import TYPE_CHECKING, Any

from aiomqtt import MqttError

from souzu.job_tracking import (
    JobAction,
    JobRegistry,
    PrinterState,
    PrintJob,
    available_actions,
)

if TYPE_CHECKING:
    from souzu.slack.client import SlackClient

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


def can_control_job(user_id: str, job: PrintJob) -> bool:
    """Whether this user is allowed to control this job."""
    return job.owner is not None and job.owner == user_id


def register_job_handlers(slack: "SlackClient", job_registry: JobRegistry) -> None:
    """Register interactive handlers on the Bolt app for job-related actions.

    Does nothing if socket mode is not available (slack.app is None).
    """
    if slack.app is None:
        return

    @slack.app.action("claim_print")
    async def handle_claim(ack: Any, body: Any, client: Any) -> None:  # noqa: ANN401
        await ack()

        user_id: str = body["user"]["id"]
        user_name: str = body["user"].get("name", user_id)
        message: dict[str, Any] = body.get("message", {})
        thread_ts: str | None = message.get("ts")

        if thread_ts is None or thread_ts not in job_registry:
            logging.warning(f"Claim action for unknown job: {thread_ts}")
            return

        state: PrinterState = job_registry[thread_ts]
        if state.current_job is None:
            return

        job = state.current_job
        if job.owner is not None:
            await client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text=f"This print was already claimed by <@{job.owner}>.",
                thread_ts=thread_ts,
            )
            return

        job.owner = user_id
        logging.info(f"Print claimed by {user_name} ({user_id})")

        channel_id = body["channel"]["id"]
        message_text = message.get("text", "Print job")

        from souzu.job_tracking import _build_status_blocks

        claimed_blocks = _build_status_blocks(message_text, user_id)
        await client.chat_update(
            channel=channel_id,
            ts=thread_ts,
            text=message_text,
            blocks=claimed_blocks,
        )

        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"<@{user_id}> claimed this print.",
        )

    def _make_action_handler(
        bound_action_id: str,
    ) -> Any:  # noqa: ANN401
        async def handle_action(
            ack: Any,  # noqa: ANN401
            body: Any,  # noqa: ANN401
            client: Any,  # noqa: ANN401
        ) -> None:
            await ack()

            user_id: str = body["user"]["id"]
            message: dict[str, Any] = body.get("message", {})
            parent_ts: str | None = message.get("thread_ts")

            if parent_ts is None or parent_ts not in job_registry:
                return

            state = job_registry[parent_ts]
            if state.current_job is None:
                return

            job = state.current_job

            action_value = bound_action_id.removeprefix("print_")
            try:
                action = JobAction(action_value)
            except ValueError:
                return

            async def _ephemeral(text: str) -> None:
                try:
                    await client.chat_postEphemeral(
                        channel=body["channel"]["id"],
                        user=user_id,
                        text=text,
                        thread_ts=parent_ts,
                    )
                except Exception:
                    logging.exception("Failed to post ephemeral message")

            if action not in available_actions(job):
                await _ephemeral("This action isn't available right now.")
                return

            if not can_control_job(user_id, job):
                await _ephemeral("Sorry, this isn't your print.")
                return

            # Photo remains a stub
            if action == JobAction.PHOTO:
                await _ephemeral(
                    "Sorry, this isn't implemented yet, but stay tuned!"
                )
                return

            # Dispatch MQTT command
            if state.connection is None:
                await _ephemeral("Printer is offline.")
                return

            command_method = {
                JobAction.PAUSE: state.connection.pause,
                JobAction.RESUME: state.connection.resume,
                JobAction.CANCEL: state.connection.stop,
            }.get(action)

            if command_method is None:
                return

            try:
                await command_method()
            except (RuntimeError, MqttError):
                logging.exception(
                    f"Failed to send {action.value} command to printer"
                )
                await _ephemeral("Failed to send command to printer.")
                return

            # Post non-ephemeral audit trail message
            action_name = _ACTION_NAMES.get(action, action.value.capitalize())
            channel = job.slack_channel or body["channel"]["id"]
            thread = job.slack_thread_ts or parent_ts
            try:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread,
                    text=f"{action_name} requested by <@{user_id}>",
                )
            except Exception:
                logging.exception("Failed to post audit trail message")

        return handle_action

    for action_id in [f"print_{action.value}" for action in JobAction]:
        slack.app.action(action_id)(_make_action_handler(action_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/slack/test_handlers.py -v --no-cov`
Expected: All pass.

- [ ] **Step 5: Run formatter and full test suite**

Run: `uv run ruff format src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py && uv run pytest --no-cov`

- [ ] **Step 6: Commit**

```bash
git add src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
git commit -m "feat: wire Slack action handlers to MQTT commands

Replace stub responses with real command dispatch for pause, resume,
and cancel. Posts non-ephemeral audit trail on success, ephemeral
messages on failure. Photo action remains a stub."
```

---

### Task 5: Set connection on PrinterState in monitor.py

**Files:**
- Modify: `src/souzu/commands/monitor.py:46-65` (inner_loop)

- [ ] **Step 1: Add connection assignment in inner_loop**

In `src/souzu/commands/monitor.py`, in the `inner_loop` function, after the connection is created and before `monitor_printer_status` is spawned, we need to make the connection available to the monitoring task. Currently `monitor_printer_status` receives `connection` as a parameter but `PrinterState` doesn't get it set.

The `PrinterState` is created inside `monitor_printer_status`, so the cleanest approach is to pass the connection through to the state there. Modify `monitor_printer_status` in `src/souzu/job_tracking.py` to set `state.connection = connection` after the state is created or loaded.

In `src/souzu/job_tracking.py`, in `monitor_printer_status`, add after `state = PrinterState()` (line 558) and after the state loading branch:

After the `else` clause that creates a new `PrinterState()`, and after the `if` clause that loads existing state, add:

```python
        state.connection = connection
```

This goes right before the `try:` on line 560, so both the loaded and fresh state get the connection.

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest --no-cov`
Expected: All pass. The existing `test_monitor_printer_status` test uses `MagicMock` for connection, which will be set on state automatically.

- [ ] **Step 3: Run formatter**

Run: `uv run ruff format src/souzu/job_tracking.py`

- [ ] **Step 4: Commit**

```bash
git add src/souzu/job_tracking.py
git commit -m "feat: set connection on PrinterState during monitoring

Ensure PrinterState.connection is set so Slack handlers can access
the MQTT connection for sending commands."
```

---

### Task 6: Update README and final verification

**Files:**
- Modify: `README.md` (update philosophy statement)

- [ ] **Step 1: Update README**

Find the statement about souzu never sending commands and update it to reflect the new capability. The exact text will depend on the current README content — read it first and make a targeted edit to the relevant paragraph.

- [ ] **Step 2: Run full pre-commit checks**

Run: `uv run prek run --all-files`
Expected: All pass.

- [ ] **Step 3: Run full test suite with coverage**

Run: `uv run pytest`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: update README to reflect remote control capability"
```
