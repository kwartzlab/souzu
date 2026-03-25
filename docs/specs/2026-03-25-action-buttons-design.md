# Action Buttons Design

## Goal

Add interactive action buttons (pause, resume, cancel, photo) to print job threads in Slack. Buttons appear in an in-thread "actions message" that is updated as the job's state changes. All buttons are stub implementations for now, gated by an authorization check.

## Data Model

### New enum: `JobAction`

```python
class JobAction(Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    PHOTO = "photo"
```

Each value maps to an `action_id` with a `print_` prefix: `print_pause`, `print_resume`, `print_cancel`, `print_photo`.

### New field on `PrintJob`

```python
actions_ts: str | None = None
```

Stores the Slack message ts of the in-thread actions message. Used to edit the message when available actions change.

### `available_actions(job: PrintJob | None) -> list[JobAction]`

Returns the valid actions for the job's current state. Returns `[]` for `None`.

| `job` / `job.state` | Actions |
|---|---|
| `None` | `[]` |
| `RUNNING` | `[PAUSE, CANCEL, PHOTO]` |
| `PAUSED` | `[RESUME, CANCEL, PHOTO]` |

## Message Flow

### Job starts (`_job_started`)

1. Post parent message with Claim button (unchanged).
2. Post in-thread actions message with buttons for `available_actions(job)`. Store its ts in `job.actions_ts`.
3. Wrap the actions message post in try/except — log on failure, leave `actions_ts` as `None`.

### Status updates (`_update_thread`)

`_update_thread` receives the list of actions to display as an explicit parameter (`actions: list[JobAction]`), computed by the caller before the state transition. This avoids ordering issues where the caller sets `current_job = None` after `_update_thread` returns.

1. Edit parent message with status blocks (unchanged).
2. Post thread reply with status update text (unchanged).
3. Update the actions message:
   - If `job.actions_ts` exists and `actions` is non-empty: edit it with buttons for `actions`.
   - If `job.actions_ts` is `None` and `actions` is non-empty: post a new actions message, store the ts. This handles recovery for jobs started before the bot was updated, or if the actions message was deleted.
   - If `actions` is empty (job ending): edit the actions message to a terminal context block (see below). If `actions_ts` is `None`, skip — nothing to clear.
   - If the edit fails with `SlackApiError` (e.g. stale ts after restart): log the error and post a new actions message instead, updating `actions_ts`. If the post also fails, log and continue.

### Job claimed (claim handler)

1. Update parent message with "Claimed by" context (unchanged).
2. Post in-thread @mention of claimant (unchanged).
3. No change to the actions message — actions are independent of ownership.

### Terminal state callers

Each terminal transition (`_job_completed`, `_job_failed`, `_job_tracking_lost`) passes `actions=[]` and a `terminal_reason` string to `_update_thread` before setting `current_job = None`. The `terminal_reason` is used in the context block text.

## Block Kit Structure

### Actions message (active job)

```json
{
  "type": "actions",
  "elements": [
    {"type": "button", "text": {"type": "plain_text", "text": "Pause"}, "action_id": "print_pause"},
    {"type": "button", "text": {"type": "plain_text", "text": "Cancel"}, "action_id": "print_cancel", "style": "danger"},
    {"type": "button", "text": {"type": "plain_text", "text": "Photo"}, "action_id": "print_photo"}
  ]
}
```

- Cancel gets `"style": "danger"`.
- Pause/Resume and Photo have no style.
- No `value` field on buttons — handlers derive context from thread and registry.

### Actions message (job ended)

Terminal context block text by outcome:

| Outcome | Text |
|---|---|
| Completed | `"No actions available — print completed."` |
| Cancelled | `"No actions available — print cancelled."` |
| Failed | `"No actions available — print failed."` |
| Tracking lost | `"No actions available — print tracking lost."` |

```json
{
  "type": "context",
  "elements": [
    {"type": "mrkdwn", "text": "No actions available — print completed."}
  ]
}
```

## Authorization

### `can_control_job(user_id: str, job: PrintJob) -> bool`

A plain function. Current logic:

```python
def can_control_job(user_id: str, job: PrintJob) -> bool:
    return job.owner is not None and job.owner == user_id
```

**Unclaimed jobs return `False` for all users.** This is intentional: the stub handlers don't perform real actions yet, and by the time they do, team member detection will broaden this check. In the interim, users who want to control a print should claim it first.

This will be broadened later when 3D printer team member detection is added (same function, wider check).

## Handler Pattern

All four action handlers (`print_pause`, `print_resume`, `print_cancel`, `print_photo`) follow the same pattern:

1. `await ack()`
2. Look up the job via `job_registry` using `body["message"]["thread_ts"]`. This differs from the claim handler (which uses `body["message"]["ts"]`) because the actions message is a thread reply — its `thread_ts` points to the parent message, which is the registry key.
3. If the job isn't found or `current_job is None`, return silently.
4. **State check:** Verify the action is in `available_actions(job)`. If not, send ephemeral: "This action isn't available right now." This handles race conditions where buttons are stale due to a concurrent status update.
5. **Authorization check:** Call `can_control_job(user_id, job)`. If not authorized, send ephemeral: "Sorry, this isn't your print."
6. **Stub response:** Send ephemeral: "Sorry, this isn't implemented yet, but stay tuned!"

## Files Affected

### Modify
- `src/souzu/job_tracking.py` — add `JobAction` enum, `available_actions()`, `actions_ts` field on `PrintJob`, actions message posting/editing in `_job_started` and `_update_thread`, pass `actions` and `terminal_reason` through the call chain
- `src/souzu/slack/handlers.py` — add `can_control_job()`, register four new action handlers, refactor common handler pattern
- `tests/souzu/test_job_tracking.py` — tests for `available_actions()`, actions message posting/editing, terminal reason text
- `tests/souzu/slack/test_handlers.py` — tests for action handlers, authorization, state validation, stale button handling
