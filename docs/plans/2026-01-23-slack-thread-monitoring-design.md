# Slack Thread Monitoring Design

## Overview

Add support for monitoring Slack threads for new replies via polling. This enables the application to respond to user messages in threads without requiring webhooks.

## Requirements

- Poll-based approach (no webhooks available in deployment environment)
- Monitor multiple threads concurrently
- 5-minute polling interval (configurable)
- For proof of concept: monitor replies to startup notification and echo them back

## Architecture

### New Module: `src/souzu/slack/monitor.py`

#### Data Types

```python
from attrs import frozen
from collections.abc import Awaitable, Callable
from datetime import timedelta

@frozen
class SlackMessage:
    """A message from a Slack thread."""
    ts: str          # Message timestamp (serves as unique ID)
    text: str        # Message content
    user: str        # User ID who posted

OnReplyCallback = Callable[[SlackMessage], Awaitable[None]]
```

#### Main Coroutine

```python
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

#### Fetch Helper

```python
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
        # Skip the parent message (has same ts as thread_ts)
        if msg["ts"] == thread_ts:
            continue
        # Skip messages we've already seen
        if after_ts is not None and msg["ts"] <= after_ts:
            continue
        messages.append(SlackMessage(
            ts=msg["ts"],
            text=msg.get("text", ""),
            user=msg.get("user", ""),
        ))

    return messages
```

### Modified: `src/souzu/commands/monitor.py`

#### Startup Notification Returns Timestamp

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

#### Monitor Function with Thread Watcher

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

    tasks = [
        create_task(inner_loop()),
        create_task(exit_event.wait()),
    ]
    if startup_ts and CONFIG.slack.error_notification_channel:
        tasks.append(create_task(watch_thread(
            CONFIG.slack.error_notification_channel,
            startup_ts,
            on_startup_reply,
        )))

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

## Design Decisions

### Per-Thread State Tracking

Each `watch_thread` call maintains its own `seen_ts` in memory. Callers can:
- Pass `last_seen_ts` on startup (from persistence) to resume
- Extract `msg.ts` from callback to persist if desired

This keeps the monitor stateless and delegates persistence decisions to callers.

### Error Handling

- Slack API errors: logged, polling continues
- Callback errors: logged, `seen_ts` still advances, remaining messages processed
- Cancellation: exits cleanly via loop termination

### Cancellation

Tasks are explicitly cancelled when `monitor()` exits (e.g., on SIGINT/SIGTERM). The `wait(pending, return_when=ALL_COMPLETED)` ensures tasks finish cancelling before the function returns.

## Future Considerations

- Extract `_CLIENT` to `src/souzu/slack/client.py` for sharing between `thread.py` and `monitor.py`
- Add `user_name` resolution if needed for richer reply formatting
- Consider exponential backoff if Slack rate limits become an issue
