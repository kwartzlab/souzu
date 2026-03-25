# Slack Socket Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace polling-based Slack thread monitoring with Bolt/Socket Mode, introducing a unified `SlackClient` with interactive message support.

**Architecture:** A single `SlackClient` class wraps Bolt's `AsyncApp` (full mode) or a plain `AsyncWebClient` (degraded mode). Event handlers are registered via Bolt decorators in a separate `handlers.py`. Job tracking receives the client via dependency injection instead of importing module-level functions.

**Tech Stack:** Python 3.12+, slack-bolt, slack-sdk, asyncio, attrs, pytest, pytest-asyncio

**Design spec:** `docs/specs/2026-03-25-slack-socket-mode-design.md`

---

## File Structure

**Create:**
- `src/souzu/slack/client.py` — `SlackClient` class (outbound methods, Bolt app, lifecycle, `SlackApiError`)
- `src/souzu/slack/handlers.py` — `JobRegistry` type alias, `register_job_handlers()`
- `tests/souzu/slack/test_client.py` — tests for `SlackClient`
- `tests/souzu/slack/test_handlers.py` — tests for interactive handlers

**Modify:**
- `pyproject.toml` — add `slack-bolt` dependency
- `src/souzu/config.py` — add `app_token` field to `SlackConfig`
- `src/souzu/slack/__init__.py` — update re-exports
- `src/souzu/job_tracking.py` — accept `SlackClient` param, add `owner` field, use client methods
- `src/souzu/commands/monitor.py` — assemble `SlackClient`, register handlers, pass to consumers
- `tests/souzu/test_config.py` — add test for `app_token`
- `tests/souzu/test_job_tracking.py` — update to mock `SlackClient` instead of free functions
- `tests/souzu/commands/test_monitor.py` — rewrite for new `SlackClient`-based flow
- `tests/souzu/test_monitor.py` — update for new monitor command shape

**Delete:**
- `src/souzu/slack/thread.py`
- `src/souzu/slack/monitor.py`
- `tests/souzu/test_slack_thread.py`
- `tests/souzu/slack/test_monitor.py`

---

### Task 1: Add slack-bolt dependency and app_token config field

**Files:**
- Modify: `pyproject.toml:6-20` (dependencies)
- Modify: `src/souzu/config.py:33-36` (SlackConfig)
- Modify: `tests/souzu/test_config.py`

- [ ] **Step 1: Add `slack-bolt` to dependencies**

In `pyproject.toml`, add `slack-bolt` to the dependencies list:

```
"slack-bolt>=1.21.0,<2.0.0",
```

Keep `slack-sdk` as a direct dependency (used for `AsyncWebClient` in degraded mode).

- [ ] **Step 2: Add `app_token` to `SlackConfig`**

In `src/souzu/config.py`, add the field after `access_token`:

```python
@frozen
class SlackConfig:
    access_token: str | None = None
    app_token: str | None = None
    print_notification_channel: str | None = None
    error_notification_channel: str | None = None
```

- [ ] **Step 3: Write test for app_token config**

In `tests/souzu/test_config.py`, add a test that structures a config dict with `app_token` and verifies it round-trips:

```python
def test_slack_config_with_app_token() -> None:
    config_dict = {
        "slack": {
            "access_token": "xoxb-test",
            "app_token": "xapp-test",
            "print_notification_channel": "C123",
            "error_notification_channel": "C456",
        }
    }
    config = SERIALIZER.structure(config_dict, Config)
    assert config.slack.app_token == "xapp-test"
    assert config.slack.access_token == "xoxb-test"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/souzu/test_config.py -v`
Expected: All pass including the new test.

- [ ] **Step 5: Sync dependencies**

Run: `uv sync`
Expected: `slack-bolt` installed successfully.

- [ ] **Step 6: Run formatter and commit**

```bash
uv run ruff format src/souzu/config.py tests/souzu/test_config.py
uv run ruff check src/souzu/config.py tests/souzu/test_config.py
git add pyproject.toml uv.lock src/souzu/config.py tests/souzu/test_config.py
git commit -m "feat: add slack-bolt dependency and app_token config field"
```

---

### Task 2: Create SlackClient class with outbound methods

**Files:**
- Create: `src/souzu/slack/client.py`
- Create: `tests/souzu/slack/test_client.py`

This task implements the core `SlackClient` with all three operating modes and the outbound methods (`post_to_channel`, `post_to_thread`, `edit_message`). Socket mode connection (Bolt `AsyncApp` + `AsyncSocketModeHandler`) is included but tested with mocks.

- [ ] **Step 1: Write failing tests for no-token mode**

Create `tests/souzu/slack/test_client.py`:

```python
"""Tests for SlackClient."""

import pytest

from souzu.slack.client import SlackApiError, SlackClient


class TestNoTokenMode:
    """Tests for SlackClient with no tokens configured."""

    def test_app_is_none(self) -> None:
        client = SlackClient()
        assert client.app is None

    @pytest.mark.asyncio
    async def test_post_to_channel_returns_none(self) -> None:
        client = SlackClient()
        async with client:
            result = await client.post_to_channel("C123", "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_post_to_thread_returns_none(self) -> None:
        client = SlackClient()
        async with client:
            result = await client.post_to_thread("C123", "1234.5678", "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_edit_message_is_noop(self) -> None:
        client = SlackClient()
        async with client:
            await client.edit_message("C123", "1234.5678", "hello")

    @pytest.mark.asyncio
    async def test_bot_user_id_is_none(self) -> None:
        client = SlackClient()
        async with client:
            assert client.bot_user_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/slack/test_client.py -v`
Expected: ImportError (module doesn't exist yet).

- [ ] **Step 3: Write SlackClient skeleton with no-token mode**

Create `src/souzu/slack/client.py`:

```python
"""Unified Slack client wrapping Bolt's AsyncApp and the SDK's AsyncWebClient."""

import logging
from typing import Any


class SlackApiError(Exception):
    """Raised when a Slack API call fails."""


class SlackClient:
    """Unified Slack client supporting three operating modes.

    - No tokens: all methods are silent no-ops.
    - access_token only: outbound messages work, no event handling.
    - Both tokens: full socket mode with interactive features.
    """

    def __init__(
        self,
        access_token: str | None = None,
        app_token: str | None = None,
    ) -> None:
        self._access_token = access_token
        self._app_token = app_token
        self._web_client: Any | None = None
        self._app: Any | None = None
        self._socket_handler: Any | None = None
        self._bot_user_id: str | None = None

        if access_token and app_token:
            from slack_bolt.adapter.socket_mode.async_handler import (
                AsyncSocketModeHandler,
            )
            from slack_bolt.async_app import AsyncApp

            self._app = AsyncApp(token=access_token)
            self._socket_handler = AsyncSocketModeHandler(self._app, app_token)
            self._web_client = self._app.client
        elif access_token:
            from slack_sdk.web.async_client import AsyncWebClient

            self._web_client = AsyncWebClient(token=access_token)

    async def __aenter__(self) -> "SlackClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the client. Connects socket mode if available, caches bot_user_id."""
        if self._web_client is not None:
            try:
                response = await self._web_client.auth_test()
                self._bot_user_id = response.get("user_id")
            except Exception as e:
                raise SlackApiError(f"Failed to authenticate with Slack: {e}") from e

        if self._socket_handler is not None:
            await self._socket_handler.connect_async()
        elif self._web_client is not None:
            # Degraded mode: access_token only, no socket mode
            logging.warning(
                "Slack app_token not configured — running in degraded mode "
                "(outbound messages only, no interactive features)"
            )

    async def stop(self) -> None:
        """Stop the client. Disconnects socket mode if active."""
        if self._socket_handler is not None:
            await self._socket_handler.disconnect_async()

    @property
    def app(self) -> Any | None:
        """The Bolt AsyncApp, or None if socket mode is not available."""
        return self._app

    @property
    def bot_user_id(self) -> str | None:
        """The bot's Slack user ID, cached after start()."""
        return self._bot_user_id

    async def post_to_channel(
        self,
        channel: str | None,
        text: str,
        blocks: list[Any] | None = None,
    ) -> str | None:
        """Post a message to a channel. Returns the message timestamp."""
        if self._web_client is None:
            return None
        if channel is None:
            logging.debug(f"No channel to post message: {text}")
            return None
        try:
            kwargs: dict[str, Any] = {"channel": channel, "text": text}
            if blocks is not None:
                kwargs["blocks"] = blocks
            response = await self._web_client.chat_postMessage(**kwargs)
        except Exception as e:
            raise SlackApiError(f"Failed to post message to channel: {e}") from e
        if not response.get("ok"):
            raise SlackApiError(
                f"Failed to post message to channel: {response.get('error', 'Unknown error')}"
            )
        return response.get("ts")

    async def post_to_thread(
        self,
        channel: str | None,
        thread_ts: str,
        text: str,
        blocks: list[Any] | None = None,
    ) -> str | None:
        """Post a message to a thread. Returns the message timestamp."""
        if self._web_client is None:
            return None
        if channel is None:
            logging.debug(f"No channel to post message: {text}")
            return None
        try:
            kwargs: dict[str, Any] = {
                "channel": channel,
                "thread_ts": thread_ts,
                "text": text,
            }
            if blocks is not None:
                kwargs["blocks"] = blocks
            response = await self._web_client.chat_postMessage(**kwargs)
        except Exception as e:
            raise SlackApiError(f"Failed to post message to thread: {e}") from e
        if not response.get("ok"):
            raise SlackApiError(
                f"Failed to post message to thread: {response.get('error', 'Unknown error')}"
            )
        return response.get("ts")

    async def edit_message(
        self,
        channel: str | None,
        message_ts: str,
        text: str,
        blocks: list[Any] | None = None,
    ) -> None:
        """Edit an existing message."""
        if self._web_client is None:
            return
        if channel is None:
            logging.debug(f"No channel to edit message: {message_ts}")
            return
        try:
            kwargs: dict[str, Any] = {
                "channel": channel,
                "ts": message_ts,
                "text": text,
            }
            if blocks is not None:
                kwargs["blocks"] = blocks
            response = await self._web_client.chat_update(**kwargs)
        except Exception as e:
            raise SlackApiError(f"Failed to edit message: {e}") from e
        if not response.get("ok"):
            raise SlackApiError(
                f"Failed to edit message: {response.get('error', 'Unknown error')}"
            )
```

- [ ] **Step 4: Run no-token mode tests**

Run: `uv run pytest tests/souzu/slack/test_client.py::TestNoTokenMode -v`
Expected: All pass.

- [ ] **Step 5: Write tests for access-token-only mode**

Add to `tests/souzu/slack/test_client.py`:

```python
from unittest.mock import AsyncMock, patch

from pytest_mock import MockerFixture


class TestAccessTokenOnlyMode:
    """Tests for SlackClient with only access_token configured."""

    def test_app_is_none(self) -> None:
        client = SlackClient(access_token="xoxb-test")
        assert client.app is None

    @pytest.mark.asyncio
    async def test_start_caches_bot_user_id(self, mocker: MockerFixture) -> None:
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        async with client:
            assert client.bot_user_id == "U_BOT"

    @pytest.mark.asyncio
    async def test_start_raises_on_auth_failure(self, mocker: MockerFixture) -> None:
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.side_effect = Exception("invalid token")
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        with pytest.raises(SlackApiError, match="Failed to authenticate"):
            await client.start()

    @pytest.mark.asyncio
    async def test_post_to_channel_success(self, mocker: MockerFixture) -> None:
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mock_web_client.chat_postMessage.return_value = {"ok": True, "ts": "1234.5678"}
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        async with client:
            result = await client.post_to_channel("C123", "hello")
        assert result == "1234.5678"
        mock_web_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="hello"
        )

    @pytest.mark.asyncio
    async def test_post_to_channel_with_blocks(self, mocker: MockerFixture) -> None:
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mock_web_client.chat_postMessage.return_value = {"ok": True, "ts": "1234.5678"}
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]
        client = SlackClient(access_token="xoxb-test")
        async with client:
            await client.post_to_channel("C123", "hello", blocks=blocks)
        mock_web_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="hello", blocks=blocks
        )

    @pytest.mark.asyncio
    async def test_post_to_channel_none_channel(
        self, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(logging.DEBUG)
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        async with client:
            result = await client.post_to_channel(None, "hello")
        assert result is None
        assert "No channel to post message" in caplog.text

    @pytest.mark.asyncio
    async def test_post_to_channel_api_error(self, mocker: MockerFixture) -> None:
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mock_web_client.chat_postMessage.return_value = {
            "ok": False, "error": "channel_not_found"
        }
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        async with client:
            with pytest.raises(SlackApiError, match="channel_not_found"):
                await client.post_to_channel("C123", "hello")

    @pytest.mark.asyncio
    async def test_post_to_thread_success(self, mocker: MockerFixture) -> None:
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mock_web_client.chat_postMessage.return_value = {"ok": True, "ts": "1234.9999"}
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        async with client:
            result = await client.post_to_thread("C123", "1234.5678", "reply")
        assert result == "1234.9999"
        mock_web_client.chat_postMessage.assert_called_once_with(
            channel="C123", thread_ts="1234.5678", text="reply"
        )

    @pytest.mark.asyncio
    async def test_edit_message_success(self, mocker: MockerFixture) -> None:
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mock_web_client.chat_update.return_value = {"ok": True}
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        async with client:
            await client.edit_message("C123", "1234.5678", "updated")
        mock_web_client.chat_update.assert_called_once_with(
            channel="C123", ts="1234.5678", text="updated"
        )
```

- [ ] **Step 6: Run all client tests**

Run: `uv run pytest tests/souzu/slack/test_client.py -v`
Expected: All pass.

- [ ] **Step 7: Write test for degraded-mode warning**

Add to `tests/souzu/slack/test_client.py` in `TestAccessTokenOnlyMode`:

```python
    @pytest.mark.asyncio
    async def test_start_logs_degraded_mode_warning(
        self, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(logging.WARNING)
        mock_web_client = AsyncMock()
        mock_web_client.auth_test.return_value = {"user_id": "U_BOT"}
        mocker.patch(
            "souzu.slack.client.AsyncWebClient", return_value=mock_web_client
        )

        client = SlackClient(access_token="xoxb-test")
        async with client:
            pass
        assert "degraded mode" in caplog.text
```

- [ ] **Step 8: Run tests to verify degraded-mode warning test passes**

Run: `uv run pytest tests/souzu/slack/test_client.py::TestAccessTokenOnlyMode -v`
Expected: All pass including the new warning test.

- [ ] **Step 9: Write tests for full mode (socket mode lifecycle)**

Add to `tests/souzu/slack/test_client.py`:

```python
class TestFullMode:
    """Tests for SlackClient with both tokens configured."""

    @pytest.mark.asyncio
    async def test_app_is_not_none(self, mocker: MockerFixture) -> None:
        mocker.patch("souzu.slack.client.AsyncApp")
        mocker.patch("souzu.slack.client.AsyncSocketModeHandler")

        client = SlackClient(access_token="xoxb-test", app_token="xapp-test")
        assert client.app is not None

    @pytest.mark.asyncio
    async def test_start_connects_socket_mode(self, mocker: MockerFixture) -> None:
        mock_app = mocker.patch("souzu.slack.client.AsyncApp").return_value
        mock_app.client.auth_test = AsyncMock(return_value={"user_id": "U_BOT"})
        mock_handler = mocker.patch(
            "souzu.slack.client.AsyncSocketModeHandler"
        ).return_value
        mock_handler.connect_async = AsyncMock()
        mock_handler.disconnect_async = AsyncMock()

        client = SlackClient(access_token="xoxb-test", app_token="xapp-test")
        async with client:
            mock_handler.connect_async.assert_called_once()
            assert client.bot_user_id == "U_BOT"
        mock_handler.disconnect_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_disconnects_on_exception(self, mocker: MockerFixture) -> None:
        mock_app = mocker.patch("souzu.slack.client.AsyncApp").return_value
        mock_app.client.auth_test = AsyncMock(return_value={"user_id": "U_BOT"})
        mock_handler = mocker.patch(
            "souzu.slack.client.AsyncSocketModeHandler"
        ).return_value
        mock_handler.connect_async = AsyncMock()
        mock_handler.disconnect_async = AsyncMock()

        with pytest.raises(RuntimeError, match="test error"):
            async with SlackClient(
                access_token="xoxb-test", app_token="xapp-test"
            ) as client:
                raise RuntimeError("test error")
        mock_handler.disconnect_async.assert_called_once()
```

- [ ] **Step 10: Run all client tests**

Run: `uv run pytest tests/souzu/slack/test_client.py -v`
Expected: All pass.

- [ ] **Step 11: Run formatter and commit**

```bash
uv run ruff format src/souzu/slack/client.py tests/souzu/slack/test_client.py
uv run ruff check src/souzu/slack/client.py tests/souzu/slack/test_client.py
git add src/souzu/slack/client.py tests/souzu/slack/test_client.py
git commit -m "feat: add SlackClient class with three operating modes"
```

---

### Task 3: Update slack package exports

**Files:**
- Modify: `src/souzu/slack/__init__.py`

Update the exports to include the new `SlackClient`. The old modules are not deleted
yet — that happens in Task 7 after all consumers have been migrated, so the full test
suite stays green throughout.

- [ ] **Step 1: Update `__init__.py` to re-export from `client.py`**

```python
from souzu.slack.client import SlackApiError, SlackClient

__all__ = ["SlackApiError", "SlackClient"]
```

- [ ] **Step 2: Run the full test suite to make sure nothing broke**

Run: `uv run pytest -v`
Expected: All pass (old modules still exist, old tests still pass).

- [ ] **Step 3: Run formatter and commit**

```bash
uv run ruff format src/souzu/slack/__init__.py
uv run ruff check src/souzu/slack/__init__.py
git add src/souzu/slack/__init__.py
git commit -m "refactor: update slack package exports to include SlackClient"
```

---

### Task 4: Update job_tracking.py to use SlackClient

**Files:**
- Modify: `src/souzu/job_tracking.py:1-27` (imports), `150-188` (helpers), `209-231` (_job_started), `322-374` (monitor_printer_status)
- Modify: `tests/souzu/test_job_tracking.py`

This task replaces all module-level Slack function imports with a `SlackClient`
parameter threaded through the call chain. Also adds `owner: str | None` to `PrintJob`.

- [ ] **Step 1: Update imports and add owner field**

In `src/souzu/job_tracking.py`:

Remove the imports of free functions:
```python
# REMOVE these lines:
from souzu.slack.thread import (
    SlackApiError,
    edit_message,
    post_to_channel,
    post_to_thread,
)
```

Replace with:
```python
from souzu.slack.client import SlackApiError, SlackClient
```

Add `owner` field to `PrintJob`:
```python
@define
class PrintJob:
    duration: timedelta
    eta: datetime | None = None
    state: JobState = JobState.RUNNING
    slack_channel: str | None = None
    slack_thread_ts: str | None = None
    start_message: str | None = None
    owner: str | None = None
```

- [ ] **Step 2: Update helper functions to accept SlackClient**

Update `_update_thread` signature and body:
```python
async def _update_thread(
    slack: SlackClient,
    job: PrintJob,
    device: BambuDevice,
    edited_message: str,
    update_message: str,
) -> None:
```

Replace all calls to `post_to_channel(...)` with `await slack.post_to_channel(...)`,
`post_to_thread(...)` with `await slack.post_to_thread(...)`, and
`edit_message(...)` with `await slack.edit_message(...)`.

Update `_update_job`:
```python
async def _update_job(
    slack: SlackClient,
    job: PrintJob,
    device: BambuDevice,
    emoji: str,
    short_message: str,
    long_message: str | None = None,
) -> None:
```

Pass `slack` through to `_update_thread`.

Update all `_job_*` functions (`_job_started`, `_job_paused`, `_job_resumed`,
`_job_failed`, `_job_completed`, `_job_tracking_lost`) to accept `slack: SlackClient`
as the first parameter and pass it to `_update_job` / `_update_thread`.

In `_job_started`, update to send Block Kit blocks with a "Claim" button and register
the job in the `JobRegistry`:

```python
async def _job_started(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
    job_registry: JobRegistry,
) -> None:
    assert report.mc_remaining_time is not None
    duration = timedelta(minutes=report.mc_remaining_time)
    eta = datetime.now(tz=CONFIG.timezone) + duration
    start_message = (
        f"{device.device_name}: Print started, {_format_duration(duration)}, "
        f"done around {_format_eta(eta)}"
    )
    job = PrintJob(
        duration=duration,
        eta=eta,
        state=JobState.RUNNING,
        start_message=start_message,
    )

    claim_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":progress_bar: {start_message}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Claim"},
                    "action_id": "claim_print",
                    "style": "primary",
                },
            ],
        },
    ]

    try:
        thread_ts = await slack.post_to_channel(
            CONFIG.slack.print_notification_channel,
            f":progress_bar: {job.start_message}",
            blocks=claim_blocks,
        )
        job.slack_channel = CONFIG.slack.print_notification_channel
        job.slack_thread_ts = thread_ts
    except SlackApiError as e:
        logging.error(f"Failed to notify channel: {e}")
    state.current_job = job

    # Register in the job registry so interactive handlers can find this job
    if job.slack_thread_ts is not None:
        job_registry[job.slack_thread_ts] = state
```

Other `_job_*` functions (`_job_paused`, `_job_resumed`, `_job_failed`, `_job_completed`,
`_job_tracking_lost`) need `slack` as the first parameter but do NOT need `job_registry`
— they modify existing state, not register new entries. When a job ends
(`_job_completed`, `_job_failed`, `_job_tracking_lost`), the registry entry can be left
in place — the handler checks `state.current_job is None` and returns early. The
registry is bounded by the number of threads the bot has posted to in its current
process lifetime, which is small.

- [ ] **Step 3: Update monitor_printer_status signature**

Add `job_registry` parameter:

```python
from souzu.slack.handlers import JobRegistry

async def monitor_printer_status(
    device: BambuDevice,
    connection: BambuMqttConnection,
    slack: SlackClient,
    job_registry: JobRegistry,
) -> None:
```

Pass `slack` as the first argument to all `_job_*` function calls inside the match
statement, and pass `job_registry` to `_job_started`:
```python
case (None, 'RUNNING', _):
    await _job_started(slack, report, state, device, job_registry)
case (PrintJob(state=JobState.RUNNING), 'PAUSE', _):
    await _job_paused(slack, report, state, device)
```

- [ ] **Step 4: Update tests**

In `tests/souzu/test_job_tracking.py`:

Replace imports:
```python
# REMOVE:
from souzu.job_tracking import SlackApiError
# The SlackApiError import was used for side_effect in mocks — replace with:
from souzu.slack.client import SlackApiError
```

For `test_update_thread_*` tests, replace patching of `souzu.job_tracking.post_to_channel`
etc. with creating a mock `SlackClient` and passing it as the first argument:

```python
@pytest.mark.asyncio
async def test_update_thread_no_thread() -> None:
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts=None,
    )
    device = MagicMock()
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)
    await _update_thread(mock_slack, job, device, "Edited message", "Update message")

    mock_slack.post_to_channel.assert_called_once_with(
        "test-channel",
        "Update message",
    )
```

Apply the same pattern to all `test_update_thread_*` tests — replace patches with a
mock `SlackClient` passed as the first parameter.

For `test_monitor_printer_status` and related tests, pass a mock `SlackClient` as
the third argument and an empty `JobRegistry` as the fourth:
`monitor_printer_status(device, mock_connection, mock_slack, {})`.
Also update the patches of `_job_started` etc. since they now take `slack` as the first
parameter (and `_job_started` also takes `job_registry` as the last parameter).

For `test_print_job`, add an assertion for the new `owner` field:
```python
assert job.owner is None
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v`
Expected: All pass.

- [ ] **Step 6: Run formatter and full linter check**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
uv run ruff check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
uv run mypy src/souzu/job_tracking.py
```

- [ ] **Step 7: Commit**

```bash
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "refactor: update job_tracking to use SlackClient dependency injection"
```

---

### Task 5: Create handlers.py with JobRegistry and claim handler

**Files:**
- Create: `src/souzu/slack/handlers.py`
- Create: `tests/souzu/slack/test_handlers.py`

- [ ] **Step 1: Write failing tests for handler registration**

Create `tests/souzu/slack/test_handlers.py`:

```python
"""Tests for Slack interactive handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from souzu.job_tracking import JobState, PrintJob, PrinterState


def test_register_job_handlers_registers_claim_action() -> None:
    """Test that register_job_handlers registers the claim_print action."""
    from souzu.slack.handlers import register_job_handlers

    mock_app = MagicMock()
    mock_slack = MagicMock()
    mock_slack.app = mock_app

    register_job_handlers(mock_slack, {})

    mock_app.action.assert_any_call("claim_print")


def test_register_job_handlers_skips_when_no_app() -> None:
    """Test that register_job_handlers does nothing when app is None."""
    from souzu.slack.handlers import register_job_handlers

    mock_slack = MagicMock()
    mock_slack.app = None

    # Should not raise
    register_job_handlers(mock_slack, {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/slack/test_handlers.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement handlers.py**

Create `src/souzu/slack/handlers.py`:

```python
"""Slack interactive event handlers bridging Slack events to domain logic."""

import logging
from typing import TYPE_CHECKING, Any

from souzu.job_tracking import PrinterState

if TYPE_CHECKING:
    from souzu.slack.client import SlackClient

JobRegistry = dict[str, PrinterState]


def register_job_handlers(slack: "SlackClient", job_registry: JobRegistry) -> None:
    """Register interactive handlers on the Bolt app for job-related actions.

    Does nothing if socket mode is not available (slack.app is None).
    """
    if slack.app is None:
        return

    @slack.app.action("claim_print")
    async def handle_claim(ack: Any, body: Any, client: Any) -> None:
        await ack()

        user_id = body["user"]["id"]
        user_name = body["user"].get("name", user_id)
        message = body.get("message", {})
        thread_ts = message.get("ts")

        if thread_ts is None or thread_ts not in job_registry:
            logging.warning(f"Claim action for unknown job: {thread_ts}")
            return

        state = job_registry[thread_ts]
        if state.current_job is None:
            return

        job = state.current_job
        if job.owner is not None:
            await client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text=f"This print was already claimed by <@{job.owner}>.",
            )
            return

        job.owner = user_id
        logging.info(f"Print claimed by {user_name} ({user_id})")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/souzu/slack/test_handlers.py -v`
Expected: All pass.

- [ ] **Step 5: Write tests for the claim handler logic**

Add to `tests/souzu/slack/test_handlers.py`:

```python
from datetime import timedelta

from souzu.slack.handlers import JobRegistry


@pytest.fixture
def job_registry_with_job() -> tuple[JobRegistry, str]:
    """Create a job registry with one active job."""
    thread_ts = "1234.5678"
    job = PrintJob(duration=timedelta(hours=2))
    state = PrinterState(current_job=job)
    registry: JobRegistry = {thread_ts: state}
    return registry, thread_ts


@pytest.mark.asyncio
async def test_claim_handler_first_claimant_wins(
    job_registry_with_job: tuple[JobRegistry, str],
) -> None:
    """Test that the first user to click Claim gets ownership."""
    from souzu.slack.handlers import register_job_handlers

    registry, thread_ts = job_registry_with_job

    mock_app = MagicMock()
    handlers: dict[str, Any] = {}

    def capture_action(action_id: str) -> Any:
        def decorator(func: Any) -> Any:
            handlers[action_id] = func
            return func
        return decorator

    mock_app.action = capture_action

    mock_slack = MagicMock()
    mock_slack.app = mock_app

    register_job_handlers(mock_slack, registry)

    # Simulate a button click
    mock_ack = AsyncMock()
    mock_client = AsyncMock()
    body = {
        "user": {"id": "U_ALICE", "name": "alice"},
        "message": {"ts": thread_ts},
        "channel": {"id": "C123"},
    }

    await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)

    mock_ack.assert_called_once()
    assert registry[thread_ts].current_job is not None
    assert registry[thread_ts].current_job.owner == "U_ALICE"


@pytest.mark.asyncio
async def test_claim_handler_rejects_second_claimant(
    job_registry_with_job: tuple[JobRegistry, str],
) -> None:
    """Test that a second claimant gets an ephemeral rejection."""
    from souzu.slack.handlers import register_job_handlers

    registry, thread_ts = job_registry_with_job
    assert registry[thread_ts].current_job is not None
    registry[thread_ts].current_job.owner = "U_ALICE"  # Already claimed

    mock_app = MagicMock()
    handlers: dict[str, Any] = {}

    def capture_action(action_id: str) -> Any:
        def decorator(func: Any) -> Any:
            handlers[action_id] = func
            return func
        return decorator

    mock_app.action = capture_action

    mock_slack = MagicMock()
    mock_slack.app = mock_app

    register_job_handlers(mock_slack, registry)

    mock_ack = AsyncMock()
    mock_client = AsyncMock()
    body = {
        "user": {"id": "U_BOB", "name": "bob"},
        "message": {"ts": thread_ts},
        "channel": {"id": "C123"},
    }

    await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)

    mock_ack.assert_called_once()
    mock_client.chat_postEphemeral.assert_called_once()
    assert "already claimed" in mock_client.chat_postEphemeral.call_args.kwargs["text"]
    assert registry[thread_ts].current_job.owner == "U_ALICE"  # Unchanged
```

- [ ] **Step 6: Write edge case tests for claim handler**

Add to `tests/souzu/slack/test_handlers.py`:

```python
@pytest.mark.asyncio
async def test_claim_handler_unknown_thread_ts() -> None:
    """Test that claim handler handles unknown thread_ts gracefully."""
    from souzu.slack.handlers import register_job_handlers

    mock_app = MagicMock()
    handlers: dict[str, Any] = {}

    def capture_action(action_id: str) -> Any:
        def decorator(func: Any) -> Any:
            handlers[action_id] = func
            return func
        return decorator

    mock_app.action = capture_action
    mock_slack = MagicMock()
    mock_slack.app = mock_app

    register_job_handlers(mock_slack, {})  # Empty registry

    mock_ack = AsyncMock()
    mock_client = AsyncMock()
    body = {
        "user": {"id": "U_ALICE", "name": "alice"},
        "message": {"ts": "9999.9999"},  # Not in registry
        "channel": {"id": "C123"},
    }

    # Should not raise
    await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)
    mock_ack.assert_called_once()
    mock_client.chat_postEphemeral.assert_not_called()


@pytest.mark.asyncio
async def test_claim_handler_no_current_job() -> None:
    """Test that claim handler handles None current_job gracefully."""
    from souzu.slack.handlers import register_job_handlers

    thread_ts = "1234.5678"
    state = PrinterState(current_job=None)
    registry: JobRegistry = {thread_ts: state}

    mock_app = MagicMock()
    handlers: dict[str, Any] = {}

    def capture_action(action_id: str) -> Any:
        def decorator(func: Any) -> Any:
            handlers[action_id] = func
            return func
        return decorator

    mock_app.action = capture_action
    mock_slack = MagicMock()
    mock_slack.app = mock_app

    register_job_handlers(mock_slack, registry)

    mock_ack = AsyncMock()
    mock_client = AsyncMock()
    body = {
        "user": {"id": "U_ALICE", "name": "alice"},
        "message": {"ts": thread_ts},
        "channel": {"id": "C123"},
    }

    await handlers["claim_print"](ack=mock_ack, body=body, client=mock_client)
    mock_ack.assert_called_once()
    mock_client.chat_postEphemeral.assert_not_called()
```

- [ ] **Step 7: Run all handler tests**

Run: `uv run pytest tests/souzu/slack/test_handlers.py -v`
Expected: All pass.

- [ ] **Step 8: Run formatter and commit**

```bash
uv run ruff format src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
uv run ruff check src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
git add src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
git commit -m "feat: add Slack interactive handlers with print claim support"
```

---

### Task 6: Update monitor command to use SlackClient

**Files:**
- Modify: `src/souzu/commands/monitor.py`
- Modify: `tests/souzu/commands/test_monitor.py`
- Modify: `tests/souzu/test_monitor.py`

- [ ] **Step 1: Rewrite monitor command**

Replace the contents of `src/souzu/commands/monitor.py`:

```python
import logging
import signal
from asyncio import (
    ALL_COMPLETED,
    FIRST_COMPLETED,
    CancelledError,
    Event,
    Queue,
    Task,
    TaskGroup,
    create_task,
    get_running_loop,
    wait,
)
from contextlib import AsyncExitStack
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from types import FrameType

from souzu.bambu.discovery import BambuDevice, discover_bambu_devices
from souzu.bambu.mqtt import BambuMqttConnection
from souzu.config import CONFIG
from souzu.job_tracking import monitor_printer_status
from souzu.logs import log_reports
from souzu.slack.client import SlackClient
from souzu.slack.handlers import JobRegistry, register_job_handlers


async def notify_startup(slack: SlackClient) -> str | None:
    """Post a startup notification to Slack. Returns message ts, or None on failure."""
    try:
        souzu_version = version("souzu")
    except PackageNotFoundError:
        souzu_version = "unknown"

    try:
        return await slack.post_to_channel(
            CONFIG.slack.error_notification_channel,
            f"Souzu {souzu_version} started",
        )
    except Exception:
        logging.exception("Failed to post startup notification")
        return None


async def inner_loop(slack: SlackClient, job_registry: JobRegistry) -> None:
    queue = Queue[BambuDevice]()
    async with TaskGroup() as tg, AsyncExitStack() as stack:
        tg.create_task(discover_bambu_devices(queue, max_time=timedelta(minutes=1)))
        while True:
            device = await queue.get()
            logging.info(f"Found device {device.device_name} at {device.ip_address}")
            try:
                connection = await stack.enter_async_context(
                    BambuMqttConnection(tg, device)
                )
                tg.create_task(log_reports(device, connection))
                tg.create_task(
                    monitor_printer_status(device, connection, slack, job_registry)
                )
            except Exception:
                logging.exception(
                    f"Failed to set up subscription for {device.device_name}"
                )
            queue.task_done()


async def monitor() -> None:
    job_registry: JobRegistry = {}

    async with SlackClient(
        access_token=CONFIG.slack.access_token,
        app_token=CONFIG.slack.app_token,
    ) as slack:
        if slack.app:
            register_job_handlers(slack, job_registry)

        startup_ts = await notify_startup(slack)

        loop = get_running_loop()
        exit_event = Event()

        def exit_handler(sig: int, frame: FrameType | None) -> None:
            exit_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, exit_handler, sig, None)

        tasks: list[Task[object]] = [
            create_task(inner_loop(slack, job_registry)),
            create_task(exit_event.wait()),
        ]

        try:
            done, pending = await wait(tasks, return_when=FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await wait(pending, return_when=ALL_COMPLETED)
        except CancelledError:
            pass
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
```

- [ ] **Step 2: Rewrite monitor command tests**

Rewrite `tests/souzu/commands/test_monitor.py` to test the new shape. Key changes:
- `notify_startup` now takes a `SlackClient` parameter
- No more `watch_thread` task
- `monitor()` creates its own `SlackClient` context manager

```python
"""Tests for the monitor command."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_mock import MockerFixture


@pytest.mark.asyncio
async def test_notify_startup_returns_timestamp(mocker: MockerFixture) -> None:
    from souzu.commands.monitor import notify_startup
    from souzu.slack.client import SlackClient

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "1234.5678"

    result = await notify_startup(mock_slack)

    assert result == "1234.5678"


@pytest.mark.asyncio
async def test_notify_startup_returns_none_on_failure(mocker: MockerFixture) -> None:
    from souzu.commands.monitor import notify_startup
    from souzu.slack.client import SlackClient

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.side_effect = Exception("API error")

    result = await notify_startup(mock_slack)

    assert result is None
```

- [ ] **Step 3: Update test_monitor.py for new monitor shape**

Update `tests/souzu/test_monitor.py`. The tests for `notify_startup` need to pass a
mock `SlackClient`. The tests for `monitor()` need to mock the `SlackClient` context
manager. Remove tests that reference `watch_thread`.

Key changes:
- `test_notify_startup_posts_version`: patch `SlackClient` and pass it to
  `notify_startup(mock_slack)`
- `test_monitor_starts_thread_watcher_when_startup_succeeds`: DELETE (no more thread watcher)
- `test_monitor_skips_thread_watcher_when_startup_fails`: DELETE
- `test_monitor_signal_handler_triggers_exit`: update to mock `SlackClient` context manager
- `test_monitor_workflow_integration`: update to mock `SlackClient` context manager
- `test_monitor_calls_notify_startup`: update to mock `SlackClient` context manager

When mocking `monitor()`, the `SlackClient` is created inside the function. Mock it via:
```python
mocker.patch("souzu.commands.monitor.SlackClient", return_value=mock_slack)
```
Where `mock_slack` is an `AsyncMock` that supports `async with` (set up
`__aenter__` and `__aexit__`).

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/souzu/commands/test_monitor.py tests/souzu/test_monitor.py -v`
Expected: All pass.

- [ ] **Step 5: Run formatter and full test suite**

```bash
uv run ruff format src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py tests/souzu/test_monitor.py
uv run ruff check src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py tests/souzu/test_monitor.py
uv run pytest -v
```

Expected: Full test suite passes.

- [ ] **Step 6: Commit**

```bash
git add src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py tests/souzu/test_monitor.py
git commit -m "refactor: update monitor command to use SlackClient and remove polling"
```

---

### Task 7: Delete old modules and final verification

**Files:**
- Delete: `src/souzu/slack/thread.py`
- Delete: `src/souzu/slack/monitor.py`
- Delete: `tests/souzu/test_slack_thread.py`
- Delete: `tests/souzu/slack/test_monitor.py`
- Possibly modify: any files with lint/type errors

- [ ] **Step 1: Delete old modules and their tests**

Now that all consumers have been migrated, remove the old code:

```bash
git rm src/souzu/slack/thread.py
git rm src/souzu/slack/monitor.py
git rm tests/souzu/test_slack_thread.py
git rm tests/souzu/slack/test_monitor.py
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass.

- [ ] **Step 3: Run full linter and type checker suite**

```bash
uv run ruff format --check src/ tests/
uv run ruff check src/ tests/
uv run mypy
uv run pyright
```

Fix any issues found.

- [ ] **Step 4: Run prek**

Run: `uv run prek run --all-files`
Expected: All checks pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete old slack thread/monitor modules and their tests"
```

If additional lint/type fixes were needed, include them in the same commit or a
follow-up:
```bash
git commit -m "chore: fix lint and type errors from socket mode rework"
```
