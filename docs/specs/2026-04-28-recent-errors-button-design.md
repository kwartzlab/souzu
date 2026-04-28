# Recent Errors Button Design

## Overview

Replace the existing "Check admin" button on the bot's startup notification with a "Recent errors" button that, when clicked by an admin, replies ephemerally with the 10 most recent WARNING-or-higher log records from the running process.

## Background

The startup notification currently includes a "Check admin" button that probes whether the clicking user is in the configured admin user group and replies in-thread. The commit that introduced it described the feature as "groundwork for permissioning" — a stand-in until a real admin-gated action existed.

Bot runtime logs flow through Python's `logging` module, configured via `logging.basicConfig` in `cli/main.py`. Output goes to stderr; under the systemd unit, it's captured by journald. There is currently no in-process retention of recent log records.

The "Recent errors" button is a real admin-gated action that doubles as the diagnostic the "Check admin" probe was emulating: a non-admin who clicks it learns they are not an admin via the denial message.

## Architecture

### Components

**New `src/souzu/log_buffer.py`:**

- `class RingBufferHandler(logging.Handler)` — a `logging.Handler` subclass that retains formatted records in a `collections.deque(maxlen=capacity)`. `emit(record)` calls `self.format(record)` and appends. `snapshot() -> list[str]` returns a copy of the deque oldest→newest.
- Module-level singleton: `RECENT_LOGS = RingBufferHandler(capacity=10)`, configured at import time with `setLevel(logging.WARNING)` and a `logging.Formatter` that includes timestamp, level, logger name, and message. `Formatter.format()` already appends tracebacks for records emitted via `logging.exception`, so traceback handling requires no extra code.

**Modified `src/souzu/cli/main.py`:**

After `logging.basicConfig(...)` in `main()`, attach the singleton to the root logger:

```python
from souzu.log_buffer import RECENT_LOGS
logging.getLogger().addHandler(RECENT_LOGS)
```

Done unconditionally, so the buffer also populates during `update`/`compact`/`install` runs. The buffer is a small bounded deque; this is cheap and harmless.

**Modified `src/souzu/commands/monitor.py`:**

- In `_build_startup_blocks`, replace the `"Check admin"` button with `{"text": "Recent errors", "action_id": "recent_errors"}`.
- Replace the `register_admin_check_handler(slack)` call in `monitor()` with `register_recent_errors_handler(slack)`.

**Modified `src/souzu/slack/handlers.py`:**

- Remove `register_admin_check_handler`.
- Add `register_recent_errors_handler(slack)` that registers a Bolt action handler for `recent_errors`.

### Data flow

**Capture path** (always-on, regardless of subcommand):

1. `main()` calls `logging.basicConfig(...)` → root logger gets a stderr handler.
2. `main()` calls `logging.getLogger().addHandler(RECENT_LOGS)` → root logger also fans out to the ring buffer.
3. Every WARNING+ record passes through `RECENT_LOGS.emit()`, which formats the record (timestamp, level, logger name, message, plus traceback if `exc_info` is set) and appends to the deque. When the deque hits capacity, the oldest record drops.

**Read path** (admin clicks "Recent errors"):

1. Bolt invokes the registered handler. `await ack()` first.
2. Pull `user_id`, `channel_id`, `message_ts` from `body` (same as today's `check_admin` handler).
3. Gate: `is_admin = await slack.is_user_in_group(user_id, CONFIG.slack.admin_user_group)`.
   - If not admin → one `chat_postEphemeral` call with "Sorry, this is admin-only." Done.
4. `records = RECENT_LOGS.snapshot()`.
   - If empty → one ephemeral with "No recent warnings or errors."
   - Otherwise: split into one or more chunks of ≤3500 chars, respecting record boundaries (no mid-record / mid-traceback splits). Each chunk is rendered as a single fenced codeblock and sent via `chat_postEphemeral`, oldest first, so the most recent record arrives in the last message.
5. All `chat_postEphemeral` calls use `thread_ts=message_ts` so replies are pinned to the startup message thread (consistent with the existing admin probe).

### Splitting algorithm

Iterate records in order. Maintain a current-chunk string buffer. For each record:

- If the buffer is empty, append the record (even if it exceeds 3500 chars on its own — it gets a dedicated message).
- Else, if `len(buffer) + len(separator) + len(record) ≤ 3500`, append.
- Else, flush the buffer as one ephemeral, start a new buffer with this record.

Flush any trailing non-empty buffer at the end. Wrap each flushed chunk in a fenced codeblock when posting.

## Error handling

- *Empty buffer:* ephemeral "No recent warnings or errors."
- *Slack API failure on an ephemeral post:* wrap each `chat_postEphemeral` in try/except and `logging.exception("Failed to post recent-errors response")`. Don't retry — partial delivery is acceptable; the admin can click again.
- *`is_user_in_group` failure:* match the existing `register_admin_check_handler` pattern — let exceptions surface to the SDK. Admin retries on failure.
- *Single record exceeds 3500 chars:* gets its own ephemeral; no internal splitting. Slack's hard message limit is well above 3500, so a single record cannot realistically trip the API.
- *Buffer state across `monitor` restarts:* the buffer is process-local and re-initialized at process start. Records from before this `monitor()` boot are gone — acceptable, since the startup message itself is per-boot.

## Concurrency

- `RingBufferHandler.emit()` is invoked from whichever thread emits the log record. `collections.deque` with `maxlen` is thread-safe for `append` and bounded eviction.
- `snapshot()` returns `list(self._records)`; the list copy is atomic enough for this use case (a record racing in during snapshot either makes it or doesn't — both are correct).
- The Slack handler runs in the asyncio event loop; reading the buffer is sync and instant.
- No locks needed.

## Permission gating

Reuses `slack.is_user_in_group(user_id, CONFIG.slack.admin_user_group)`, which already exists. No new config fields.

Non-admin denial uses the same ephemeral-message pattern as the per-print action handlers (e.g., "Sorry, this isn't your print" in `_make_action_handler`).

## Logging filter scope

`RECENT_LOGS.setLevel(logging.WARNING)` captures WARNING, ERROR, and CRITICAL. Records below WARNING never reach `emit()`.

The handler is attached to the root logger, so all loggers — including third-party noise — flow through. If third-party WARNINGs become noisy in practice, a per-logger filter can be added later.

## Testing

### `tests/souzu/test_log_buffer.py` (new)

- `test_captures_warning_and_above`: instantiate `RingBufferHandler(capacity=10)`, attach to a fresh logger, emit DEBUG/INFO/WARNING/ERROR/CRITICAL, assert `snapshot()` returns 3 entries (WARNING/ERROR/CRITICAL).
- `test_drops_oldest_at_capacity`: emit 12 WARNING records numbered 0–11, assert snapshot returns the last 10 in order (oldest → newest).
- `test_format_includes_timestamp_level_logger_message`: emit one record, assert the formatted string contains expected fields.
- `test_format_includes_traceback`: catch a real exception, call `logger.exception("boom")`, assert the formatted record contains `"boom"`, `"Traceback"`, and the exception class name.
- `test_snapshot_returns_copy`: mutate the returned list, call `snapshot()` again, assert the second snapshot is unaffected.

### `tests/souzu/slack/test_handlers.py` (extend)

Drop the existing `check_admin` tests, add `recent_errors` equivalents:

- `test_recent_errors_denies_non_admin`: `is_user_in_group` returns `False`. Assert exactly one `chat_postEphemeral` call with the denial text and `thread_ts=message_ts`.
- `test_recent_errors_empty_buffer`: admin path with empty `RECENT_LOGS`. Assert one ephemeral with "No recent warnings or errors."
- `test_recent_errors_single_chunk`: admin path, populate buffer with a few short records. Assert one ephemeral whose text contains all records inside a fenced codeblock.
- `test_recent_errors_splits_at_3500_chars`: populate buffer such that combined output exceeds 3500 chars. Assert ≥2 ephemerals; oldest records appear in earlier messages, newest in the last message; no record is split mid-content.
- `test_recent_errors_oversized_single_record`: one record > 3500 chars. Assert it is sent in its own ephemeral with no further splitting.

All admin-path tests assert `chat_postEphemeral` was called with `thread_ts=message_ts` from the body.

### `tests/souzu/test_monitor.py` (small touch)

Update any existing assertion referencing `"check_admin"` button/`action_id` to expect `"recent_errors"`.

### Conventions

- Use `mocker.patch` (not `@patch` decorators).
- Use `MagicMock(spec=...)` for constructed mocks.
- Use `pytest.mark.asyncio` for async handler tests.
