# Action Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add interactive action buttons (pause, resume, cancel, photo) to print job threads, with authorization and state validation, as stubs for future printer control.

**Architecture:** `JobAction` enum and `available_actions()` in `job_tracking.py` determine which buttons to show. `_update_thread` gains responsibility for posting/editing an in-thread actions message. Four new Bolt handlers in `handlers.py` follow a common pattern: ack, lookup, state check, auth check, stub response.

**Tech Stack:** Python 3.12+, slack-bolt, slack-sdk, asyncio, attrs, pytest, pytest-asyncio

**Design spec:** `docs/specs/2026-03-25-action-buttons-design.md`

---

## File Structure

**Modify:**
- `src/souzu/job_tracking.py` — add `JobAction` enum, `available_actions()`, `actions_ts` field, `build_actions_blocks()`, update `_update_thread`/`_update_job`/`_job_started`/`_job_*` signatures
- `src/souzu/slack/handlers.py` — add `can_control_job()`, four action handlers, refactor `_make_action_body` helper
- `tests/souzu/test_job_tracking.py` — tests for new enum, available_actions, actions message flow
- `tests/souzu/slack/test_handlers.py` — tests for action handlers, auth, state validation

---

### Task 1: Add JobAction enum, available_actions(), and actions_ts field

**Files:**
- Modify: `src/souzu/job_tracking.py:49-68` (enums and PrintJob)
- Modify: `tests/souzu/test_job_tracking.py:14-46` (imports and basic tests)

- [ ] **Step 1: Write tests for JobAction and available_actions**

Add to `tests/souzu/test_job_tracking.py`:

```python
from souzu.job_tracking import (
    JobAction,
    available_actions,
    # ... existing imports
)


def test_job_action_enum() -> None:
    assert JobAction.PAUSE.value == "pause"
    assert JobAction.RESUME.value == "resume"
    assert JobAction.CANCEL.value == "cancel"
    assert JobAction.PHOTO.value == "photo"


def test_available_actions_running() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    actions = available_actions(job)
    assert actions == [JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO]


def test_available_actions_paused() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.PAUSED)
    actions = available_actions(job)
    assert actions == [JobAction.RESUME, JobAction.CANCEL, JobAction.PHOTO]


def test_available_actions_none() -> None:
    assert available_actions(None) == []


def test_print_job_actions_ts_default() -> None:
    job = PrintJob(duration=timedelta(hours=1))
    assert job.actions_ts is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_action_enum tests/souzu/test_job_tracking.py::test_available_actions_running -v --no-cov`
Expected: ImportError (JobAction/available_actions not defined yet).

- [ ] **Step 3: Implement JobAction, available_actions, and actions_ts**

In `src/souzu/job_tracking.py`, after the `JobState` enum and its serializer hooks (line 57):

```python
class JobAction(Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    PHOTO = "photo"


def available_actions(job: PrintJob | None) -> list[JobAction]:
    """Return the valid actions for a job's current state."""
    if job is None:
        return []
    if job.state == JobState.RUNNING:
        return [JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO]
    if job.state == JobState.PAUSED:
        return [JobAction.RESUME, JobAction.CANCEL, JobAction.PHOTO]
    return []
```

Note: `available_actions` references `PrintJob` which is defined after it. Move `available_actions` to after the `PrintJob` class definition (after line 68), or use a string forward reference. Since `PrintJob` is a class (not a type alias), placing `available_actions` after `PrintJob` is cleanest.

Add `actions_ts` field to `PrintJob`:

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
    actions_ts: str | None = None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v --no-cov`
Expected: All pass including new tests.

- [ ] **Step 5: Run formatter and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
uv run ruff check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add JobAction enum, available_actions(), and actions_ts field"
```

---

### Task 2: Add build_actions_blocks() and terminal blocks helper

**Files:**
- Modify: `src/souzu/job_tracking.py:151-168` (near _build_status_blocks)
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write tests for build_actions_blocks**

Add to `tests/souzu/test_job_tracking.py`:

```python
from souzu.job_tracking import (
    build_actions_blocks,
    # ... existing imports
)


def test_build_actions_blocks_running() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    blocks = build_actions_blocks(available_actions(job))
    assert len(blocks) == 1
    assert blocks[0]["type"] == "actions"
    action_ids = [e["action_id"] for e in blocks[0]["elements"]]
    assert action_ids == ["print_pause", "print_cancel", "print_photo"]


def test_build_actions_blocks_paused() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.PAUSED)
    blocks = build_actions_blocks(available_actions(job))
    action_ids = [e["action_id"] for e in blocks[0]["elements"]]
    assert action_ids == ["print_resume", "print_cancel", "print_photo"]


def test_build_actions_blocks_cancel_is_danger() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    blocks = build_actions_blocks(available_actions(job))
    cancel_btn = [e for e in blocks[0]["elements"] if e["action_id"] == "print_cancel"][0]
    assert cancel_btn["style"] == "danger"


def test_build_actions_blocks_pause_has_no_style() -> None:
    job = PrintJob(duration=timedelta(hours=1), state=JobState.RUNNING)
    blocks = build_actions_blocks(available_actions(job))
    pause_btn = [e for e in blocks[0]["elements"] if e["action_id"] == "print_pause"][0]
    assert "style" not in pause_btn


def test_build_actions_blocks_empty() -> None:
    blocks = build_actions_blocks([])
    assert blocks == []


def test_build_terminal_actions_blocks() -> None:
    from souzu.job_tracking import build_terminal_actions_blocks

    blocks = build_terminal_actions_blocks("print completed")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "context"
    assert "print completed" in blocks[0]["elements"][0]["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_build_actions_blocks_running -v --no-cov`
Expected: ImportError.

- [ ] **Step 3: Implement build_actions_blocks and build_terminal_actions_blocks**

In `src/souzu/job_tracking.py`, near `_build_status_blocks`:

```python
_ACTION_LABELS: dict[JobAction, str] = {
    JobAction.PAUSE: "Pause",
    JobAction.RESUME: "Resume",
    JobAction.CANCEL: "Cancel",
    JobAction.PHOTO: "Photo",
}

_ACTION_STYLES: dict[JobAction, str] = {
    JobAction.CANCEL: "danger",
}


def build_actions_blocks(actions: list[JobAction]) -> list[dict[str, Any]]:
    """Build Block Kit blocks for the actions message."""
    if not actions:
        return []
    elements: list[dict[str, Any]] = []
    for action in actions:
        btn: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": _ACTION_LABELS[action]},
            "action_id": f"print_{action.value}",
        }
        if action in _ACTION_STYLES:
            btn["style"] = _ACTION_STYLES[action]
        elements.append(btn)
    return [{"type": "actions", "elements": elements}]


def build_terminal_actions_blocks(reason: str) -> list[dict[str, Any]]:
    """Build Block Kit blocks for a terminal actions message (no buttons)."""
    return [
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"No actions available — {reason}."},
            ],
        }
    ]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v --no-cov`
Expected: All pass.

- [ ] **Step 5: Run formatter and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
uv run ruff check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add build_actions_blocks and build_terminal_actions_blocks"
```

---

### Task 3: Update _update_thread and _update_job to manage the actions message

**Files:**
- Modify: `src/souzu/job_tracking.py:171-234` (_update_thread, _update_job)
- Modify: `tests/souzu/test_job_tracking.py`

This is the core change: `_update_thread` gains `actions: list[JobAction]` and `terminal_reason: str | None` parameters, and manages posting/editing the actions message.

- [ ] **Step 1: Write tests for actions message management in _update_thread**

Add to `tests/souzu/test_job_tracking.py`:

```python
@pytest.mark.asyncio
async def test_update_thread_posts_actions_message_when_no_actions_ts() -> None:
    """When actions_ts is None and actions are non-empty, post a new actions message."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_thread.side_effect = ["status_ts", "9999.0001"]

    await _update_thread(
        mock_slack, job, device, "Edited", "Update",
        actions=[JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO],
    )

    # Should have posted an actions message in-thread
    post_calls = mock_slack.post_to_thread.call_args_list
    assert len(post_calls) == 2  # status update + actions message
    actions_call = post_calls[1]
    assert "blocks" in actions_call.kwargs or (len(actions_call.args) > 3)
    assert job.actions_ts == "9999.0001"


@pytest.mark.asyncio
async def test_update_thread_edits_actions_message_when_actions_ts_exists() -> None:
    """When actions_ts exists, edit the actions message."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        actions_ts="8888.0001",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(
        mock_slack, job, device, "Edited", "Update",
        actions=[JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO],
    )

    # Should have edited the actions message
    edit_calls = mock_slack.edit_message.call_args_list
    assert len(edit_calls) == 2  # parent edit + actions edit
    actions_edit = edit_calls[1]
    assert actions_edit.args[1] == "8888.0001"


@pytest.mark.asyncio
async def test_update_thread_clears_actions_on_terminal() -> None:
    """When actions is empty and terminal_reason is set, edit to terminal block."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        actions_ts="8888.0001",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(
        mock_slack, job, device, "Edited", "Update",
        actions=[], terminal_reason="print completed",
    )

    # Should have edited the actions message to terminal
    edit_calls = mock_slack.edit_message.call_args_list
    actions_edit = edit_calls[1]
    assert "No actions available" in str(actions_edit.kwargs.get("blocks", actions_edit.args))


@pytest.mark.asyncio
async def test_update_thread_skips_actions_when_empty_and_no_ts() -> None:
    """When actions is empty and no actions_ts exists, do nothing for actions."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)

    await _update_thread(
        mock_slack, job, device, "Edited", "Update",
        actions=[], terminal_reason="print completed",
    )

    # Only one edit call (parent message), no actions edit
    assert mock_slack.edit_message.call_count == 1


@pytest.mark.asyncio
async def test_update_thread_recovers_when_actions_edit_fails() -> None:
    """When editing the actions message fails, post a new one and update actions_ts."""
    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="test-channel",
        slack_thread_ts="1234.5678",
        actions_ts="stale.0001",
        state=JobState.RUNNING,
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    mock_slack = AsyncMock(spec=SlackClient)
    # First edit_message (parent) succeeds; second (actions) fails
    mock_slack.edit_message.side_effect = [None, SlackApiError("stale")]
    mock_slack.post_to_thread.side_effect = ["status_ts", "new_actions.0002"]

    await _update_thread(
        mock_slack, job, device, "Edited", "Update",
        actions=[JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO],
    )

    # Should have fallen back to posting a new actions message
    post_calls = mock_slack.post_to_thread.call_args_list
    assert len(post_calls) == 2  # status update + fallback actions post
    assert job.actions_ts == "new_actions.0002"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_update_thread_posts_actions_message_when_no_actions_ts -v --no-cov`
Expected: TypeError (unexpected keyword argument 'actions').

- [ ] **Step 3: Update _update_thread to accept and manage actions**

Update `_update_thread` signature to:

```python
async def _update_thread(
    slack: SlackClient,
    job: PrintJob,
    device: BambuDevice,
    edited_message: str,
    update_message: str,
    *,
    actions: list[JobAction] | None = None,
    terminal_reason: str | None = None,
) -> None:
```

After the existing parent-message edit logic (the `try: blocks = _build_status_blocks(...)` block), add actions message management:

```python
    # Manage the in-thread actions message
    if actions is None:
        pass  # No action update requested
    elif actions:
        action_blocks = build_actions_blocks(actions)
        channel = job.slack_channel or CONFIG.slack.print_notification_channel
        if job.actions_ts is not None:
            try:
                await slack.edit_message(
                    channel, job.actions_ts, "Actions", blocks=action_blocks,
                )
            except SlackApiError:
                logging.warning("Failed to edit actions message, posting new one")
                job.actions_ts = None  # Fall through to post
        if job.actions_ts is None:
            try:
                actions_ts = await slack.post_to_thread(
                    channel, job.slack_thread_ts, "Actions", blocks=action_blocks,
                )
                job.actions_ts = actions_ts
            except SlackApiError as e:
                logging.error(f"Failed to post actions message: {e}")
    elif job.actions_ts is not None and terminal_reason is not None:
        terminal_blocks = build_terminal_actions_blocks(terminal_reason)
        try:
            await slack.edit_message(
                job.slack_channel or CONFIG.slack.print_notification_channel,
                job.actions_ts,
                f"No actions available — {terminal_reason}.",
                blocks=terminal_blocks,
            )
        except SlackApiError as e:
            logging.error(f"Failed to clear actions message: {e}")
```

- [ ] **Step 4: Update _update_job to pass actions through**

Update `_update_job` signature to accept and forward the new parameters:

```python
async def _update_job(
    slack: SlackClient,
    job: PrintJob,
    device: BambuDevice,
    emoji: str,
    short_message: str,
    long_message: str | None = None,
    *,
    actions: list[JobAction] | None = None,
    terminal_reason: str | None = None,
) -> None:
    update_prefix = f"{emoji} {device.device_name}: "
    edit_prefix = (
        f"~{job.start_message}~\n{emoji} " if job.start_message else update_prefix
    )
    await _update_thread(
        slack,
        job,
        device,
        f"{edit_prefix}{short_message}",
        f"{update_prefix}{long_message or short_message}",
        actions=actions,
        terminal_reason=terminal_reason,
    )
```

- [ ] **Step 5: Update existing _update_thread tests**

The existing tests call `_update_thread` without the new kwargs, which is fine (they default to `None`). However, `test_update_thread_with_thread` now needs to account for the fact that edit_message is called with `blocks=` kwarg. The existing tests already handle this from the previous migration. Verify they still pass.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v --no-cov`
Expected: All pass.

- [ ] **Step 7: Run formatter and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
uv run ruff check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: update _update_thread to manage in-thread actions message"
```

---

### Task 4: Update _job_* functions to pass actions and terminal_reason

**Files:**
- Modify: `src/souzu/job_tracking.py:237-395` (_job_started and all _job_* functions)
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write test for _job_started posting actions message**

Add to `tests/souzu/test_job_tracking.py`:

```python
from souzu.job_tracking import _job_started


@pytest.mark.asyncio
async def test_job_started_posts_actions_message(mocker: MockerFixture) -> None:
    """Test that _job_started posts an actions message after the parent message."""
    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 1, 1, 12, 0, 0, tzinfo=pytz.UTC
    )

    mock_slack = AsyncMock(spec=SlackClient)
    # First post_to_channel returns parent ts, then post_to_thread returns actions ts
    mock_slack.post_to_channel.return_value = "1111.0001"
    mock_slack.post_to_thread.return_value = "1111.0002"

    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 60

    state = PrinterState()
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    assert state.current_job is not None
    assert state.current_job.actions_ts == "1111.0002"

    # Verify actions message was posted in thread
    mock_slack.post_to_thread.assert_called_once()
    call_kwargs = mock_slack.post_to_thread.call_args.kwargs
    assert "blocks" in call_kwargs
    action_ids = [
        e["action_id"] for e in call_kwargs["blocks"][0]["elements"]
    ]
    assert "print_pause" in action_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_started_posts_actions_message -v --no-cov`
Expected: Assertion failure (no actions message posted).

- [ ] **Step 3: Update _job_started to post actions message**

In `_job_started`, after the parent message post succeeds and `job.slack_thread_ts` is set, add:

```python
    # Post the actions message in-thread
    if job.slack_thread_ts is not None:
        actions = available_actions(job)
        if actions:
            action_blocks = build_actions_blocks(actions)
            try:
                actions_ts = await slack.post_to_thread(
                    CONFIG.slack.print_notification_channel,
                    job.slack_thread_ts,
                    "Actions",
                    blocks=action_blocks,
                )
                job.actions_ts = actions_ts
            except SlackApiError as e:
                logging.error(f"Failed to post actions message: {e}")
```

- [ ] **Step 4: Update _job_paused and _job_resumed to pass actions**

```python
async def _job_paused(...) -> None:
    assert state.current_job is not None
    error_message = parse_error_code(report.print_error) if report.print_error else None
    # Compute actions for the PAUSED state before the transition
    await _update_job(
        slack,
        state.current_job,
        device,
        ":warning:",
        "Paused",
        f"Print paused\nMessage from printer: {error_message}"
        if error_message
        else "Print paused!",
        actions=[JobAction.RESUME, JobAction.CANCEL, JobAction.PHOTO],
    )
    state.current_job.state = JobState.PAUSED
    state.current_job.eta = None


async def _job_resumed(...) -> None:
    assert state.current_job is not None and report.mc_remaining_time is not None
    remaining_duration = timedelta(minutes=report.mc_remaining_time)
    eta = datetime.now(tz=CONFIG.timezone) + remaining_duration
    await _update_job(
        slack,
        state.current_job,
        device,
        ":progress_bar:",
        f"Resumed, done around {_format_eta(eta)}",
        f"Print resumed, now done around {_format_eta(eta)}",
        actions=[JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO],
    )
    state.current_job.state = JobState.RUNNING
    state.current_job.eta = eta
```

- [ ] **Step 5: Update terminal _job_* functions to pass actions=[] and terminal_reason**

```python
async def _job_failed(...) -> None:
    assert state.current_job is not None
    if report.print_error in CANCELLED_ERROR_CODES:
        await _update_job(
            slack, state.current_job, device,
            ":heavy_minus_sign:", "Cancelled", "Print cancelled",
            actions=[], terminal_reason="print cancelled",
        )
        state.current_job = None
    else:
        error_message = parse_error_code(report.print_error)
        await _update_job(
            slack, state.current_job, device,
            ":x:", "Failed!",
            f"Print failed!\nMessage from printer: {error_message}",
            actions=[], terminal_reason="print failed",
        )
        state.current_job = None


async def _job_completed(...) -> None:
    assert state.current_job is not None
    await _update_job(
        slack, state.current_job, device,
        ":white_check_mark:", "Finished!", "Print finished!",
        actions=[], terminal_reason="print completed",
    )
    state.current_job = None


async def _job_tracking_lost(...) -> None:
    assert state.current_job is not None
    await _update_job(
        slack, state.current_job, device,
        ":question:", "Tracking lost",
        "Lost tracking for print job - maybe the printer was disconnected?",
        actions=[], terminal_reason="print tracking lost",
    )
    state.current_job = None
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v --no-cov`
Expected: All pass.

- [ ] **Step 7: Run formatter and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
uv run ruff check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: wire actions and terminal_reason through job status transitions"
```

---

### Task 5: Add can_control_job and action handlers to handlers.py

**Files:**
- Modify: `src/souzu/slack/handlers.py`
- Modify: `tests/souzu/slack/test_handlers.py`

- [ ] **Step 1: Write tests for can_control_job**

Add to `tests/souzu/slack/test_handlers.py`:

```python
from souzu.slack.handlers import can_control_job


def test_can_control_job_owner_matches() -> None:
    job = PrintJob(duration=timedelta(hours=1), owner="U_ALICE")
    assert can_control_job("U_ALICE", job) is True


def test_can_control_job_owner_mismatch() -> None:
    job = PrintJob(duration=timedelta(hours=1), owner="U_ALICE")
    assert can_control_job("U_BOB", job) is False


def test_can_control_job_unclaimed() -> None:
    job = PrintJob(duration=timedelta(hours=1))
    assert can_control_job("U_ALICE", job) is False
```

- [ ] **Step 2: Implement can_control_job**

Add to `src/souzu/slack/handlers.py`:

```python
from souzu.job_tracking import JobRegistry, PrinterState, PrintJob


def can_control_job(user_id: str, job: PrintJob) -> bool:
    """Whether this user is allowed to control this job."""
    return job.owner is not None and job.owner == user_id
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/souzu/slack/test_handlers.py::test_can_control_job_owner_matches tests/souzu/slack/test_handlers.py::test_can_control_job_owner_mismatch tests/souzu/slack/test_handlers.py::test_can_control_job_unclaimed -v --no-cov`
Expected: All pass.

- [ ] **Step 4: Write tests for action handlers**

Add a helper to build action message bodies (different from the claim body — uses `thread_ts` for parent lookup):

```python
def _make_action_body(
    thread_ts: str,
    message_ts: str = "9999.0001",
    user_id: str = "U123",
    user_name: str = "testuser",
    channel_id: str = "C456",
) -> dict[str, Any]:
    """Build a body dict as Slack sends for an action on a thread reply."""
    return {
        "user": {"id": user_id, "name": user_name},
        "message": {"ts": message_ts, "thread_ts": thread_ts},
        "channel": {"id": channel_id},
    }
```

Then the tests:

```python
from souzu.job_tracking import JobAction


@pytest.mark.asyncio
async def test_action_handler_authorized_stub_response(
    job_registry_with_job: tuple[JobRegistry, str],
) -> None:
    """Authorized owner gets the 'not implemented' stub message."""
    registry, thread_ts = job_registry_with_job
    state = registry[thread_ts]
    assert state.current_job is not None
    state.current_job.owner = "U_OWNER"

    _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
    register_job_handlers(mock_slack, registry)

    mock_ack = AsyncMock()
    mock_client = AsyncMock()
    body = _make_action_body(thread_ts, user_id="U_OWNER")

    await handlers["print_pause"](ack=mock_ack, body=body, client=mock_client)

    mock_ack.assert_awaited_once()
    mock_client.chat_postEphemeral.assert_awaited_once()
    assert "not implemented yet" in mock_client.chat_postEphemeral.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_action_handler_unauthorized_rejection(
    job_registry_with_job: tuple[JobRegistry, str],
) -> None:
    """Non-owner gets 'not your print' rejection."""
    registry, thread_ts = job_registry_with_job
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
    assert "isn't your print" in mock_client.chat_postEphemeral.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_action_handler_wrong_state(
    job_registry_with_job: tuple[JobRegistry, str],
) -> None:
    """Pause on an already-paused job sends 'not available' ephemeral."""
    registry, thread_ts = job_registry_with_job
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
    assert "isn't available right now" in mock_client.chat_postEphemeral.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_action_handler_unknown_job() -> None:
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
async def test_action_handler_no_current_job() -> None:
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
async def test_resume_handler_authorized_on_paused_job(
    job_registry_with_job: tuple[JobRegistry, str],
) -> None:
    """Resume on a paused job by owner gets the stub response."""
    registry, thread_ts = job_registry_with_job
    state = registry[thread_ts]
    assert state.current_job is not None
    state.current_job.state = JobState.PAUSED
    state.current_job.owner = "U_OWNER"

    _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
    register_job_handlers(mock_slack, registry)

    mock_ack = AsyncMock()
    mock_client = AsyncMock()
    body = _make_action_body(thread_ts, user_id="U_OWNER")

    await handlers["print_resume"](ack=mock_ack, body=body, client=mock_client)

    mock_ack.assert_awaited_once()
    mock_client.chat_postEphemeral.assert_awaited_once()
    assert "not implemented yet" in mock_client.chat_postEphemeral.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_all_four_action_handlers_registered() -> None:
    """Verify all four action handlers are registered."""
    _mock_app, mock_slack, handlers = _make_mock_app_and_slack()
    register_job_handlers(mock_slack, {})

    assert "print_pause" in handlers
    assert "print_resume" in handlers
    assert "print_cancel" in handlers
    assert "print_photo" in handlers
```

- [ ] **Step 5: Implement action handlers**

In `src/souzu/slack/handlers.py`, update imports and add the handlers:

```python
from souzu.job_tracking import JobAction, JobRegistry, PrinterState, PrintJob, available_actions


def can_control_job(user_id: str, job: PrintJob) -> bool:
    """Whether this user is allowed to control this job."""
    return job.owner is not None and job.owner == user_id
```

Then in `register_job_handlers`, after the `handle_claim` handler:

```python
    _ACTION_IDS = [
        f"print_{action.value}" for action in JobAction
    ]

    for action_id in _ACTION_IDS:

        @slack.app.action(action_id)
        async def handle_action(
            ack: Any, body: Any, client: Any, _action_id: str = action_id,
        ) -> None:  # noqa: ANN401
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

            # Parse the JobAction from the action_id
            action_value = _action_id.removeprefix("print_")
            try:
                action = JobAction(action_value)
            except ValueError:
                return

            if action not in available_actions(job):
                await client.chat_postEphemeral(
                    channel=body["channel"]["id"],
                    user=user_id,
                    text="This action isn't available right now.",
                )
                return

            if not can_control_job(user_id, job):
                await client.chat_postEphemeral(
                    channel=body["channel"]["id"],
                    user=user_id,
                    text="Sorry, this isn't your print.",
                )
                return

            await client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text="Sorry, this isn't implemented yet, but stay tuned!",
            )
```

Note: The `_action_id=action_id` default parameter captures the loop variable in the closure.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/souzu/slack/test_handlers.py -v --no-cov`
Expected: All pass.

- [ ] **Step 7: Run full test suite and prek**

```bash
uv run pytest --no-cov -q
uv run prek run --all-files
```

Expected: All tests pass, prek clean.

- [ ] **Step 8: Commit**

```bash
uv run ruff format src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
uv run ruff check src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
git add src/souzu/slack/handlers.py tests/souzu/slack/test_handlers.py
git commit -m "feat: add action button handlers with authorization and state validation"
```
