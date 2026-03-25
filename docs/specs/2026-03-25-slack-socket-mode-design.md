# Slack Socket Mode Rework

## Goal

Replace the polling-based Slack thread monitoring with Slack's Socket Mode via the Bolt
framework, enabling real-time event handling and interactive message features (buttons,
slash commands, DMs).

## Motivation

The existing approach polls `conversations.replies` every 5 minutes to watch a single
thread — a tech demo with high latency and no path to interactivity. Socket Mode enables
push-based event delivery and interactive components, unlocking:

- **Print claiming:** A "Claim" button on job status messages. First claimant wins.
  Eventually, unclaimed prints will be paused and cancelled automatically.
- **Print controls:** Pause/resume, cancel, and camera snapshot buttons, restricted to
  the print owner or 3D printer team members. Photos sent via DM.
- **Auxiliary interactions:** DMs or slash commands for config updates, stats, log dumps.

## Architecture

### SlackClient (`src/souzu/slack/client.py`)

A single class replacing both `slack/thread.py` (outbound messages) and
`slack/monitor.py` (polling). Wraps Bolt's `AsyncApp` for event handling and the SDK's
`AsyncWebClient` for outbound API calls.

#### Three operating modes

| Tokens present | Behavior |
|---|---|
| Neither | All methods are silent no-ops. No warning. |
| `access_token` only | Outbound messages work. No event handling. Warning posted to error notification channel at startup. |
| Both `access_token` and `app_token` | Full socket mode with interactive features. |

#### Interface

```python
class SlackClient:
    def __init__(self, access_token: str | None = None, app_token: str | None = None) -> None: ...

    # Async context manager — start() on enter, stop() on exit
    async def __aenter__(self) -> SlackClient: ...
    async def __aexit__(self, *exc: object) -> None: ...

    async def start(self) -> None:
        """Connect socket mode (if available), cache bot_user_id,
        post degraded-mode warning if no app_token."""

    async def stop(self) -> None:
        """Disconnect socket mode cleanly."""

    # Outbound methods — work in all modes (no-op with no access_token)
    async def post_to_channel(self, channel: str | None, text: str, blocks: list | None = None) -> str | None: ...
    async def post_to_thread(self, channel: str | None, thread_ts: str, text: str, blocks: list | None = None) -> str | None: ...
    async def edit_message(self, channel: str | None, message_ts: str, text: str, blocks: list | None = None) -> None: ...

    # Bolt app for handler registration — None if no app_token
    @property
    def app(self) -> AsyncApp | None: ...

    @property
    def bot_user_id(self) -> str | None: ...
```

- `blocks` parameter on all outbound methods supports Block Kit messages with
  interactive buttons. `text` serves as the notification fallback.
- Channel-is-None guard: log debug message and return None (preserves current behavior).
- No-token mode: outbound methods silently return None (no exception). `SlackApiError`
  is only raised for actual API call failures in access-token and full modes.
- `SlackApiError` is defined in `client.py` (moved from the deleted `thread.py`),
  wrapping Bolt/SDK errors.

#### Bolt internals

When `app_token` is present, `__init__` creates:

```python
self._app = AsyncApp(token=access_token)
self._socket_handler = AsyncSocketModeHandler(self._app, app_token)
```

`start()` calls `await self._socket_handler.connect_async()` (not `start_async()`,
which blocks forever). The SDK manages its own reconnection. `stop()` calls
`await self._socket_handler.disconnect_async()`.

`start()` also calls `auth_test()` to cache `bot_user_id`. If this fails (e.g., invalid
token), `start()` raises immediately — this is a configuration error that should surface
at startup, not silently degrade at runtime.

When only `access_token` is present, a plain `AsyncWebClient` is used for outbound
calls, with no Bolt app or socket handler.

The aiohttp-based adapter is used (`slack_bolt.adapter.socket_mode.async_handler`),
since the project already depends on `aiohttp`.

### Event Handlers (`src/souzu/slack/handlers.py`)

Bridges Slack events to domain logic. Separate from `client.py` to keep the client
generic (knows about Slack, not about printers or jobs).

```python
def register_job_handlers(slack: SlackClient, job_registry: JobRegistry) -> None:
    """Register interactive handlers on the Bolt app for job-related actions."""
```

**Job registry:** Handlers need to look up `PrinterState` when a button is clicked.
A shared `JobRegistry` (e.g., `dict[str, PrinterState]` keyed by Slack message
timestamp) is populated by `job_tracking` when jobs start and read by handlers when
interactive events arrive. The registry is created by the monitor command and passed to
both `register_job_handlers` and `monitor_printer_status`.

Uses Bolt's decorator API on `slack.app`:

```python
@slack.app.action("claim_print")
async def handle_claim(ack, body, client):
    await ack()
    # Look up PrinterState via job_registry[message_ts]
    # First claimant wins; already-claimed -> ephemeral rejection
    # Update message to show owner
```

Dependencies flow one way: `commands/monitor.py` -> `slack/client.py` +
`slack/handlers.py` -> `job_tracking.py`.

### Configuration (`src/souzu/config.py`)

`SlackConfig` gains `app_token`:

```python
@frozen
class SlackConfig:
    access_token: str | None = None
    app_token: str | None = None
    print_notification_channel: str | None = None
    error_notification_channel: str | None = None
```

Example `souzu.json`:

```json
{
  "slack": {
    "access_token": "xoxb-...",
    "app_token": "xapp-...",
    "print_notification_channel": "C...",
    "error_notification_channel": "C..."
  }
}
```

### Job Tracking Changes (`src/souzu/job_tracking.py`)

- `monitor_printer_status` receives a `SlackClient` and `JobRegistry` instead of
  importing module-level free functions. It registers each active job's `PrinterState`
  in the registry (keyed by Slack message timestamp) so interactive handlers can find it.
- `PrintJob` gains an `owner: str | None` field to track who claimed a print.
- `_job_started` posts messages with Block Kit blocks containing a "Claim" button.
- Internal helpers (`_update_thread`, `_update_job`, etc.) receive the client as a
  parameter.

### Monitor Command (`src/souzu/commands/monitor.py`)

Becomes the assembly point:

```python
async def monitor() -> None:
    job_registry: JobRegistry = {}

    async with SlackClient(
        access_token=CONFIG.slack.access_token,
        app_token=CONFIG.slack.app_token,
    ) as slack:
        if slack.app:
            register_job_handlers(slack, job_registry)

        startup_ts = await notify_startup(slack)

        # Signal handling, inner_loop(slack, job_registry), exit_event.wait()
        # No more watch_thread task.
```

- `notify_startup` takes the `SlackClient` instead of using imported free functions.
- `inner_loop` receives `slack: SlackClient` and `job_registry: JobRegistry` as
  parameters, passing both through to `monitor_printer_status`. Other tasks it creates
  (`log_reports`, `discover_bambu_devices`) are unchanged.
- The `watch_thread` polling task and its callback are removed entirely.
- Socket mode runs within the `SlackClient` context manager lifecycle.

### Module Structure

```
src/souzu/slack/
    __init__.py       — re-exports SlackClient, SlackApiError
    client.py         — SlackClient class
    handlers.py       — register_job_handlers() and handler implementations
    thread.py         — DELETED
    monitor.py        — DELETED
```

## Deleted Code

- `src/souzu/slack/thread.py` — replaced by `SlackClient` outbound methods
- `src/souzu/slack/monitor.py` — replaced by socket mode event delivery
- Thread polling in `commands/monitor.py` — the `watch_thread` task and
  `on_startup_reply` callback

## Dependencies

**Added:** `slack-bolt` in `pyproject.toml`.

**Kept:** `slack-sdk` as a direct dependency (used for `AsyncWebClient` in degraded
mode and SDK error types). Version constraint may be relaxed to align with what
`slack-bolt` requires.

### Slack App Setup Requirements

- Enable Socket Mode in the Slack app settings
- Generate an app-level token with `connections:write` scope
- Subscribe to events: `message.channels`, `message.groups`
- Enable Interactivity (no request URL needed with socket mode)
- Bot token scopes: `chat:write`, `channels:history`, `groups:history` (existing),
  plus `commands` if slash commands are added later

## Testing Strategy

**SlackClient unit tests:**
- All three modes (no tokens, access-only, full) — verify outbound method behavior
- Mock Bolt `AsyncApp` and `AsyncSocketModeHandler` — testing our wiring, not Slack's SDK
- Context manager lifecycle (start/stop called correctly, cleanup on exception)

**Handler tests:**
- Claim handler: first claimant wins, second gets ephemeral rejection
- Handlers only registered when `slack.app` is not None

**Job tracking tests:**
- Updated to pass a mocked `SlackClient` instead of patching module-level functions
- Block Kit message construction (verify blocks contain expected buttons)

**Not tested:** Bolt/SDK reconnection internals. Trusted until observed to be broken.
