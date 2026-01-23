# Slack Thread Monitoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add poll-based Slack thread monitoring that responds to replies in threads.

**Architecture:** New `monitor.py` module with `watch_thread` coroutine, integrated into startup flow.

**Tech Stack:** Python 3.12+, attrs, slack_sdk AsyncWebClient, pytest-asyncio

---

## Task 1: Create SlackMessage Dataclass

**Files:**
- Create: `src/souzu/slack/monitor.py`
- Create: `tests/souzu/slack/test_monitor.py`

**Step 1: Write the failing test**

Create the test file with basic dataclass tests:

```python
"""Tests for the Slack thread monitoring module."""

import pytest

from souzu.slack.monitor import SlackMessage


def test_slack_message_creation() -> None:
    """Test creating a SlackMessage instance."""
    msg = SlackMessage(ts="1234.5678", text="Hello world", user="U12345")
    assert msg.ts == "1234.5678"
    assert msg.text == "Hello world"
    assert msg.user == "U12345"


def test_slack_message_is_frozen() -> None:
    """Test that SlackMessage is immutable."""
    msg = SlackMessage(ts="1234.5678", text="Hello world", user="U12345")
    with pytest.raises(AttributeError):
        msg.text = "Changed"  # type: ignore[misc]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/souzu/slack/test_monitor.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'souzu.slack.monitor'"

**Step 3: Write minimal implementation**

Create the monitor module:

```python
"""Slack thread monitoring via polling."""

from attrs import frozen


@frozen
class SlackMessage:
    """A message from a Slack thread."""

    ts: str
    text: str
    user: str
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/souzu/slack/test_monitor.py -v`
Expected: PASS

**Step 5: Run formatters and linters**

Run: `uv run ruff format src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py && uv run ruff check --fix src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py`

**Step 6: Commit**

```bash
git add src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py
git commit -m "feat(slack): add SlackMessage dataclass for thread monitoring"
```

---

## Task 2: Create _fetch_replies Helper

**Files:**
- Modify: `src/souzu/slack/monitor.py`
- Modify: `tests/souzu/slack/test_monitor.py`

**Step 1: Write the failing tests**

Add tests to the test file:

```python
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_slack_client() -> AsyncMock:
    """Create a mock Slack client for testing."""
    mock_client = AsyncMock()
    mock_client.conversations_replies = AsyncMock()
    return mock_client


@pytest.mark.asyncio
async def test_fetch_replies_returns_empty_when_no_client() -> None:
    """Test that _fetch_replies returns empty list when client is None."""
    from souzu.slack.monitor import _fetch_replies

    with patch("souzu.slack.monitor._CLIENT", None):
        result = await _fetch_replies("C123", "1234.5678")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_replies_filters_parent_message(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that _fetch_replies filters out the parent message."""
    from souzu.slack.monitor import _fetch_replies

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent message", "user": "U111"},
            {"ts": "1234.5679", "text": "Reply 1", "user": "U222"},
        ]
    }

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        result = await _fetch_replies("C123", "1234.5678")

    assert len(result) == 1
    assert result[0].ts == "1234.5679"
    assert result[0].text == "Reply 1"
    assert result[0].user == "U222"


@pytest.mark.asyncio
async def test_fetch_replies_filters_already_seen(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that _fetch_replies filters messages at or before after_ts."""
    from souzu.slack.monitor import _fetch_replies

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Old reply", "user": "U222"},
            {"ts": "1234.5680", "text": "New reply", "user": "U333"},
        ]
    }

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        result = await _fetch_replies("C123", "1234.5678", after_ts="1234.5679")

    assert len(result) == 1
    assert result[0].ts == "1234.5680"
    assert result[0].text == "New reply"


@pytest.mark.asyncio
async def test_fetch_replies_handles_missing_fields(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that _fetch_replies handles messages with missing optional fields."""
    from souzu.slack.monitor import _fetch_replies

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678"},  # Parent, missing text and user
            {"ts": "1234.5679"},  # Reply, missing text and user
        ]
    }

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        result = await _fetch_replies("C123", "1234.5678")

    assert len(result) == 1
    assert result[0].text == ""
    assert result[0].user == ""
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/souzu/slack/test_monitor.py::test_fetch_replies_returns_empty_when_no_client -v`
Expected: FAIL with "cannot import name '_fetch_replies'"

**Step 3: Write minimal implementation**

Add to `src/souzu/slack/monitor.py`:

```python
"""Slack thread monitoring via polling."""

from attrs import frozen
from slack_sdk.web.async_client import AsyncWebClient

from souzu.config import CONFIG

_CLIENT = (
    AsyncWebClient(token=CONFIG.slack.access_token)
    if CONFIG.slack.access_token
    else None
)


@frozen
class SlackMessage:
    """A message from a Slack thread."""

    ts: str
    text: str
    user: str


async def _fetch_replies(
    channel: str,
    thread_ts: str,
    after_ts: str | None = None,
) -> list[SlackMessage]:
    """
    Fetch replies from a Slack thread, optionally filtering to messages after a timestamp.

    Returns messages in chronological order (oldest first).
    """
    if _CLIENT is None:
        return []

    response = await _CLIENT.conversations_replies(
        channel=channel,
        ts=thread_ts,
    )

    messages: list[SlackMessage] = []
    for msg in response.get("messages", []):
        if msg["ts"] == thread_ts:
            continue
        if after_ts is not None and msg["ts"] <= after_ts:
            continue
        messages.append(
            SlackMessage(
                ts=msg["ts"],
                text=msg.get("text", ""),
                user=msg.get("user", ""),
            )
        )

    return messages
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/slack/test_monitor.py -v`
Expected: PASS

**Step 5: Run formatters and linters**

Run: `uv run ruff format src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py && uv run ruff check --fix src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py`

**Step 6: Commit**

```bash
git add src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py
git commit -m "feat(slack): add _fetch_replies helper for thread monitoring"
```

---

## Task 3: Create watch_thread Coroutine

**Files:**
- Modify: `src/souzu/slack/monitor.py`
- Modify: `tests/souzu/slack/test_monitor.py`

**Step 1: Write the failing tests**

Add to test file:

```python
import asyncio
from datetime import timedelta


@pytest.mark.asyncio
async def test_watch_thread_calls_callback_for_new_replies(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that watch_thread calls on_reply for each new message."""
    from souzu.slack.monitor import SlackMessage, watch_thread

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Reply 1", "user": "U222"},
            {"ts": "1234.5680", "text": "Reply 2", "user": "U333"},
        ]
    }

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(received_messages) >= 2
    assert received_messages[0].text == "Reply 1"
    assert received_messages[1].text == "Reply 2"


@pytest.mark.asyncio
async def test_watch_thread_skips_already_seen_messages(
    mock_slack_client: AsyncMock,
) -> None:
    """Test that watch_thread respects last_seen_ts."""
    from souzu.slack.monitor import SlackMessage, watch_thread

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Old reply", "user": "U222"},
            {"ts": "1234.5680", "text": "New reply", "user": "U333"},
        ]
    }

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                last_seen_ts="1234.5679",
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(received_messages) >= 1
    assert all(msg.text == "New reply" for msg in received_messages)


@pytest.mark.asyncio
async def test_watch_thread_continues_on_api_error(
    mock_slack_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that watch_thread logs and continues on API errors."""
    import logging

    from souzu.slack.monitor import SlackMessage, watch_thread

    caplog.set_level(logging.ERROR)
    call_count = 0

    async def mock_replies(*args: object, **kwargs: object) -> dict[str, list[object]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("API error")
        return {"messages": [{"ts": "1234.5678", "text": "Parent", "user": "U111"}]}

    mock_slack_client.conversations_replies.side_effect = mock_replies

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "Error polling thread" in caplog.text
    assert call_count >= 2


@pytest.mark.asyncio
async def test_watch_thread_continues_on_callback_error(
    mock_slack_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that watch_thread logs and continues when callback raises."""
    import logging

    from souzu.slack.monitor import SlackMessage, watch_thread

    caplog.set_level(logging.ERROR)

    mock_slack_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1234.5678", "text": "Parent", "user": "U111"},
            {"ts": "1234.5679", "text": "Reply 1", "user": "U222"},
            {"ts": "1234.5680", "text": "Reply 2", "user": "U333"},
        ]
    }

    received_messages: list[SlackMessage] = []

    async def on_reply(msg: SlackMessage) -> None:
        if msg.ts == "1234.5679":
            raise Exception("Callback error")
        received_messages.append(msg)

    with patch("souzu.slack.monitor._CLIENT", mock_slack_client):
        task = asyncio.create_task(
            watch_thread(
                "C123",
                "1234.5678",
                on_reply,
                poll_interval=timedelta(seconds=0.01),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "Error in on_reply callback" in caplog.text
    assert any(msg.text == "Reply 2" for msg in received_messages)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/souzu/slack/test_monitor.py::test_watch_thread_calls_callback_for_new_replies -v`
Expected: FAIL with "cannot import name 'watch_thread'"

**Step 3: Write minimal implementation**

Add to `src/souzu/slack/monitor.py`:

```python
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

# ... existing code ...

OnReplyCallback = Callable[[SlackMessage], Awaitable[None]]


async def watch_thread(
    channel: str,
    thread_ts: str,
    on_reply: OnReplyCallback,
    last_seen_ts: str | None = None,
    poll_interval: timedelta = timedelta(minutes=5),
) -> None:
    """
    Poll a Slack thread for new replies and invoke callback for each.

    Runs until cancelled. Handles API errors by logging and continuing.

    Args:
        channel: Slack channel ID
        thread_ts: Parent message timestamp (thread ID)
        on_reply: Called for each new reply, in chronological order
        last_seen_ts: Skip replies at or before this timestamp
        poll_interval: Time between polls (default 5 minutes)
    """
    seen_ts = last_seen_ts

    while True:
        try:
            replies = await _fetch_replies(channel, thread_ts, after_ts=seen_ts)
        except Exception:
            logging.exception(f"Error polling thread {thread_ts}")
            replies = []

        for msg in replies:
            try:
                await on_reply(msg)
            except Exception:
                logging.exception(f"Error in on_reply callback for message {msg.ts}")
            seen_ts = msg.ts

        await asyncio.sleep(poll_interval.total_seconds())
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/slack/test_monitor.py -v`
Expected: PASS

**Step 5: Run formatters and linters**

Run: `uv run ruff format src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py && uv run ruff check --fix src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py`

**Step 6: Commit**

```bash
git add src/souzu/slack/monitor.py tests/souzu/slack/test_monitor.py
git commit -m "feat(slack): add watch_thread coroutine for polling thread replies"
```

---

## Task 4: Modify notify_startup to Return Timestamp

**Files:**
- Modify: `src/souzu/commands/monitor.py:26-39`

**Step 1: Write the failing test**

Create or modify test file `tests/souzu/commands/test_monitor.py`:

```python
"""Tests for the monitor command."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_notify_startup_returns_timestamp() -> None:
    """Test that notify_startup returns the message timestamp."""
    from souzu.commands.monitor import notify_startup

    with (
        patch("souzu.commands.monitor.post_to_channel", new_callable=AsyncMock) as mock_post,
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_post.return_value = "1234.5678"
        mock_config.slack.error_notification_channel = "C123"

        result = await notify_startup()

    assert result == "1234.5678"


@pytest.mark.asyncio
async def test_notify_startup_returns_none_on_failure() -> None:
    """Test that notify_startup returns None when posting fails."""
    from souzu.commands.monitor import notify_startup

    with (
        patch("souzu.commands.monitor.post_to_channel", new_callable=AsyncMock) as mock_post,
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_post.side_effect = Exception("API error")
        mock_config.slack.error_notification_channel = "C123"

        result = await notify_startup()

    assert result is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/souzu/commands/test_monitor.py::test_notify_startup_returns_timestamp -v`
Expected: FAIL (returns None instead of timestamp, or test file doesn't exist)

**Step 3: Write minimal implementation**

Modify `src/souzu/commands/monitor.py` lines 26-39:

```python
async def notify_startup() -> str | None:
    """Post a startup notification to Slack. Returns message ts, or None on failure."""
    try:
        souzu_version = version("souzu")
    except PackageNotFoundError:
        souzu_version = "unknown"

    try:
        return await post_to_channel(
            CONFIG.slack.error_notification_channel,
            f"Souzu {souzu_version} started",
        )
    except Exception:
        logging.exception("Failed to post startup notification")
        return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/commands/test_monitor.py -v`
Expected: PASS

**Step 5: Run formatters and linters**

Run: `uv run ruff format src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py && uv run ruff check --fix src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py`

**Step 6: Commit**

```bash
git add src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py
git commit -m "feat(monitor): return timestamp from notify_startup"
```

---

## Task 5: Integrate Thread Watcher into Monitor

**Files:**
- Modify: `src/souzu/commands/monitor.py:62-87`
- Modify: `tests/souzu/commands/test_monitor.py`

**Step 1: Write the failing test**

Add to test file:

```python
import asyncio


@pytest.mark.asyncio
async def test_monitor_starts_thread_watcher_when_startup_succeeds() -> None:
    """Test that monitor starts watching the startup thread."""
    from souzu.commands.monitor import monitor

    watch_thread_called = False
    watch_thread_args: tuple[str, str] | None = None

    async def mock_watch_thread(
        channel: str,
        thread_ts: str,
        on_reply: object,
        **kwargs: object,
    ) -> None:
        nonlocal watch_thread_called, watch_thread_args
        watch_thread_called = True
        watch_thread_args = (channel, thread_ts)
        await asyncio.sleep(10)  # Run until cancelled

    with (
        patch("souzu.commands.monitor.notify_startup", new_callable=AsyncMock) as mock_startup,
        patch("souzu.commands.monitor.inner_loop", new_callable=AsyncMock) as mock_inner,
        patch("souzu.commands.monitor.watch_thread", side_effect=mock_watch_thread),
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_startup.return_value = "1234.5678"
        mock_config.slack.error_notification_channel = "C123"
        mock_inner.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(monitor(), timeout=0.1)

    assert watch_thread_called
    assert watch_thread_args == ("C123", "1234.5678")


@pytest.mark.asyncio
async def test_monitor_skips_thread_watcher_when_startup_fails() -> None:
    """Test that monitor doesn't start watcher when startup returns None."""
    from souzu.commands.monitor import monitor

    watch_thread_called = False

    async def mock_watch_thread(*args: object, **kwargs: object) -> None:
        nonlocal watch_thread_called
        watch_thread_called = True
        await asyncio.sleep(10)

    with (
        patch("souzu.commands.monitor.notify_startup", new_callable=AsyncMock) as mock_startup,
        patch("souzu.commands.monitor.inner_loop", new_callable=AsyncMock) as mock_inner,
        patch("souzu.commands.monitor.watch_thread", side_effect=mock_watch_thread),
        patch("souzu.commands.monitor.CONFIG") as mock_config,
    ):
        mock_startup.return_value = None
        mock_config.slack.error_notification_channel = "C123"
        mock_inner.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(monitor(), timeout=0.1)

    assert not watch_thread_called
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/souzu/commands/test_monitor.py::test_monitor_starts_thread_watcher_when_startup_succeeds -v`
Expected: FAIL (watch_thread not imported/called)

**Step 3: Write minimal implementation**

Update imports at top of `src/souzu/commands/monitor.py`:

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
from souzu.slack.monitor import SlackMessage, watch_thread
from souzu.slack.thread import post_to_channel, post_to_thread
```

Update the `monitor()` function:

```python
async def monitor() -> None:
    startup_ts = await notify_startup()

    loop = get_running_loop()
    exit_event = Event()

    def exit_handler(sig: int, frame: FrameType | None) -> None:
        exit_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, exit_handler, sig, None)

    async def on_startup_reply(msg: SlackMessage) -> None:
        await post_to_thread(
            CONFIG.slack.error_notification_channel,
            startup_ts,
            f"I saw this message: {msg.text}",
        )

    tasks: list[Task[object]] = [
        create_task(inner_loop()),
        create_task(exit_event.wait()),
    ]
    if startup_ts and CONFIG.slack.error_notification_channel:
        tasks.append(
            create_task(
                watch_thread(
                    CONFIG.slack.error_notification_channel,
                    startup_ts,
                    on_startup_reply,
                )
            )
        )

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

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/commands/test_monitor.py -v`
Expected: PASS

**Step 5: Run all tests to ensure nothing broke**

Run: `uv run pytest -v`
Expected: All tests PASS

**Step 6: Run formatters and linters**

Run: `uv run ruff format src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py && uv run ruff check --fix src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py`

**Step 7: Run full pre-commit checks**

Run: `uv run prek run --all-files`
Expected: All checks pass

**Step 8: Commit**

```bash
git add src/souzu/commands/monitor.py tests/souzu/commands/test_monitor.py
git commit -m "feat(monitor): integrate thread watcher for startup notification"
```

---

## Task 6: Manual Testing

**Step 1: Configure Slack credentials**

Ensure your config has:
- `slack.access_token` set
- `slack.error_notification_channel` set to a test channel

**Step 2: Run the application**

Run: `uv run souzu`

**Step 3: Verify startup notification appears**

Check the configured Slack channel for "Souzu {version} started" message.

**Step 4: Reply to the startup thread**

Post a reply in the thread (e.g., "Hello bot!")

**Step 5: Wait for response**

Within 5 minutes, the bot should reply: "I saw this message: Hello bot!"

**Step 6: Test shutdown**

Press Ctrl-C and verify clean shutdown (no errors, pending tasks cancelled).
