import json
import logging
from concurrent.futures import CancelledError
from datetime import datetime, timedelta
from enum import Enum
from math import ceil
from typing import Any

from anyio import Path as AsyncPath
from attrs import define
from cattrs import Converter
from cattrs.gen import make_dict_unstructure_fn
from cattrs.gen import override as cattrs_override
from xdg_base_dirs import xdg_state_home

from souzu.bambu.camera import CameraClient, P1CameraClient
from souzu.bambu.discovery import BambuDevice
from souzu.bambu.errors import CANCELLED_ERROR_CODES, parse_error_code
from souzu.bambu.mqtt import BambuMqttConnection, BambuStatusReport
from souzu.config import CONFIG
from souzu.slack.client import SlackApiError, SlackClient

_ONE_MINUTE = timedelta(minutes=1)
_FIVE_MINUTES = timedelta(minutes=5)
_HALF_HOUR = timedelta(minutes=30)
_FIFTY_FIVE_MINUTES = timedelta(minutes=55)
_ONE_HOUR = timedelta(hours=1)
_EIGHT_HOURS = timedelta(hours=8)

_ADOPTION_TIME_WINDOW = timedelta(minutes=10)
_ADOPTION_DURATION_TOLERANCE = 0.10

_TIME_FORMAT = '%I:%M %p'
_DATE_TIME_FORMAT = '%I:%M %p on %A'


_STATE_DIR = AsyncPath(xdg_state_home() / 'souzu')


_STATE_SERIALIZER = Converter()
_STATE_SERIALIZER.register_unstructure_hook(timedelta, lambda td: td.total_seconds())
_STATE_SERIALIZER.register_structure_hook(
    timedelta, lambda td, _: timedelta(seconds=td)
)
_STATE_SERIALIZER.register_unstructure_hook(datetime, lambda dt: dt.isoformat())
_STATE_SERIALIZER.register_structure_hook(
    datetime, lambda dt, _: datetime.fromisoformat(dt)
)


# Type alias for the job registry — maps Slack thread_ts to PrinterState
JobRegistry = dict[str, "PrinterState"]


class JobState(Enum):
    RUNNING = 'running'
    PAUSED = 'paused'


_STATE_SERIALIZER.register_unstructure_hook(JobState, lambda state: state.value)
_STATE_SERIALIZER.register_structure_hook(
    JobState, lambda state_value, _: JobState(state_value)
)


class JobAction(Enum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    PHOTO = "photo"


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


def available_actions(job: PrintJob | None) -> list[JobAction]:
    """Return the valid actions for a job's current state."""
    if job is None or job.owner is None:
        return []
    if job.state == JobState.RUNNING:
        return [JobAction.PAUSE, JobAction.CANCEL, JobAction.PHOTO]
    if job.state == JobState.PAUSED:
        return [JobAction.RESUME, JobAction.CANCEL, JobAction.PHOTO]
    return []


@define
class PreviousJobInfo:
    """Adoption metadata captured when an unclaimed print ends in cancel/tracking-lost.

    Used by ``_job_started`` to decide whether to re-use the previous attempt's
    Slack thread instead of starting a new one.
    """

    slack_channel: str
    slack_thread_ts: str
    actions_ts: str | None
    duration: timedelta
    ended_at: datetime


@define
class PrinterState:
    current_job: PrintJob | None = None
    previous_job: PreviousJobInfo | None = None
    connection: BambuMqttConnection | None = None

    def camera_client(self) -> CameraClient | None:
        """Construct a camera client for this printer, or None if unavailable."""
        if self.connection is None:
            return None
        return P1CameraClient(
            ip_address=self.connection.device.ip_address,
            access_code=self.connection.access_code,
        )


_STATE_SERIALIZER.register_unstructure_hook(
    PrinterState,
    make_dict_unstructure_fn(
        PrinterState,
        _STATE_SERIALIZER,
        connection=cattrs_override(omit=True),
    ),
)


def _round_up(time: datetime, unit: timedelta) -> datetime:
    """
    Round up the given time to the next multiple of the given unit.
    """

    start_of_day = time.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = (time - start_of_day).total_seconds()
    return start_of_day + unit * ceil(seconds / unit.total_seconds())


def _format_duration(duration: timedelta) -> str:
    """
    Format a timedelta object as a human-readable string like "1 minute" or "2 hours".

    Add a fudge factor to account for estimation error.
    """
    if duration < _ONE_MINUTE:
        return "1 minute"
    elif duration < _FIFTY_FIVE_MINUTES:
        # round up to next 5 minutes
        minutes = ceil(duration / _FIVE_MINUTES) * 5
        return f"{minutes} minutes"
    elif duration < _EIGHT_HOURS:
        # round up to next half hour
        hours = ceil(duration / _HALF_HOUR) / 2
        if hours == 1:
            hours_str = "1 hour"
        elif hours.is_integer():
            hours_str = f"{int(hours)} hours"
        else:
            hours_str = f"{hours:.1f} hours"
        return hours_str
    else:
        # round up to next hour
        hours = int(ceil(duration / _ONE_HOUR))
        return f"{hours} hours"


def _format_time(time: datetime) -> str:
    """
    Format a datetime object as a time string like 9:32 AM.
    """
    return time.strftime(_TIME_FORMAT).lstrip('0')


def _format_date_time(time: datetime) -> str:
    """
    Format a datetime object as a date and time string like 9:32 AM on Monday.
    """
    return time.strftime(_DATE_TIME_FORMAT).lstrip('0')


def _format_eta(eta: datetime) -> str:
    """
    Return a human-readable string representing the finish time of the print job.

    Add a fudge factor to account for estimation error.
    """

    duration = eta - datetime.now(tz=CONFIG.timezone)

    if duration < _ONE_MINUTE:
        return _format_time(_round_up(eta, _ONE_MINUTE))
    elif duration < _FIFTY_FIVE_MINUTES:
        return _format_time(_round_up(eta, _FIVE_MINUTES))
    elif duration < _EIGHT_HOURS:
        return _format_time(_round_up(eta, _HALF_HOUR))
    else:
        rounded_eta = _round_up(eta, _ONE_HOUR)
        if rounded_eta.date != datetime.now(tz=CONFIG.timezone).date:
            return _format_date_time(rounded_eta)
        else:
            return _format_time(rounded_eta)


def _should_adopt(
    previous: PreviousJobInfo,
    new_duration: timedelta,
    now: datetime,
) -> bool:
    """Decide whether to re-use the previous attempt's Slack thread for a new print.

    Returns True only when the new attempt looks like a quick slicer-tweak retry of
    the previous one: started within ``_ADOPTION_TIME_WINDOW`` of the previous end,
    and with an estimated duration within ``_ADOPTION_DURATION_TOLERANCE`` of the
    previous estimate.
    """
    if now - previous.ended_at > _ADOPTION_TIME_WINDOW:
        return False
    prev_secs = previous.duration.total_seconds()
    if prev_secs <= 0:
        return False
    return (
        abs(new_duration.total_seconds() - prev_secs)
        <= prev_secs * _ADOPTION_DURATION_TOLERANCE
    )


def _build_previous_job_info(
    job: PrintJob, ended_at: datetime
) -> PreviousJobInfo | None:
    """Capture adoption metadata from a job, or None if it isn't eligible.

    A job is eligible only when it was unclaimed and has a Slack thread to adopt.
    """
    if job.owner is not None:
        return None
    if job.slack_channel is None or job.slack_thread_ts is None:
        return None
    return PreviousJobInfo(
        slack_channel=job.slack_channel,
        slack_thread_ts=job.slack_thread_ts,
        actions_ts=job.actions_ts,
        duration=job.duration,
        ended_at=ended_at,
    )


_ACTION_LABELS: dict[JobAction, str] = {
    JobAction.PAUSE: "Pause",
    JobAction.RESUME: "Resume",
    JobAction.CANCEL: "Cancel",
    JobAction.PHOTO: "Photo",
}

_ACTION_STYLES: dict[JobAction, str] = {
    JobAction.CANCEL: "danger",
}


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


def _build_status_blocks(
    text: str, owner: str | None, *, terminal: bool = False
) -> list[dict[str, Any]]:
    """Build Block Kit blocks for a status message, preserving claim info."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        },
    ]
    if owner is not None:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Claimed by <@{owner}>"},
                ],
            }
        )
    elif terminal:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": ":cry: Nobody claimed this print"},
                ],
            }
        )
    else:
        blocks.append(
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
            }
        )
    return blocks


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
    if job.slack_thread_ts is None:
        try:
            await slack.post_to_channel(
                job.slack_channel or CONFIG.slack.print_notification_channel,
                update_message,
            )
        except SlackApiError as e:
            logging.error(f"Failed to notify channel: {e}")
        return

    try:
        await slack.post_to_thread(
            job.slack_channel or CONFIG.slack.print_notification_channel,
            job.slack_thread_ts,
            update_message,
        )
    except SlackApiError as e:
        logging.error(f"Failed to notify thread: {e}")
        if job.slack_thread_ts:
            try:
                await slack.post_to_channel(
                    job.slack_channel or CONFIG.slack.print_notification_channel,
                    update_message,
                )
            except SlackApiError as e:
                logging.error(f"Failed to notify channel as fallback: {e}")
    try:
        blocks = _build_status_blocks(
            edited_message, job.owner, terminal=terminal_reason is not None
        )
        await slack.edit_message(
            job.slack_channel or CONFIG.slack.print_notification_channel,
            job.slack_thread_ts,
            edited_message,
            blocks=blocks,
        )
    except SlackApiError as e:
        logging.error(f"Failed to edit message: {e}")

    # Manage the in-thread actions message
    if actions is None:
        pass  # No action update requested
    elif actions:
        action_blocks = build_actions_blocks(actions)
        channel = job.slack_channel or CONFIG.slack.print_notification_channel
        if job.actions_ts is not None:
            try:
                await slack.edit_message(
                    channel,
                    job.actions_ts,
                    "Actions",
                    blocks=action_blocks,
                )
            except SlackApiError:
                logging.warning("Failed to edit actions message, posting new one")
                job.actions_ts = None  # Fall through to post
        if job.actions_ts is None:
            try:
                actions_ts = await slack.post_to_thread(
                    channel,
                    job.slack_thread_ts,
                    "Actions",
                    blocks=action_blocks,
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


async def _adopt_thread(
    slack: SlackClient,
    previous: PreviousJobInfo,
    job: PrintJob,
    device: BambuDevice,
) -> None:
    """Re-use the previous attempt's Slack thread for a new print.

    Edits the top-level message to reflect the new attempt, posts a restart
    notification as a reply, and replaces the previous attempt's terminal
    actions placeholder with an "awaiting claim" placeholder for the new
    attempt. The previous attempt's status replies are intentionally left in
    place as a loose audit trail.
    """
    text = f":progress_bar: {job.start_message}"
    blocks = _build_status_blocks(text, None)
    try:
        await slack.edit_message(
            previous.slack_channel,
            previous.slack_thread_ts,
            text,
            blocks=blocks,
        )
    except SlackApiError as e:
        logging.error(f"Failed to edit adopted message: {e}")

    eta_str = _format_eta(job.eta) if job.eta is not None else "unknown"
    restart_text = (
        f":repeat: {device.device_name}: Print restarted, "
        f"{_format_duration(job.duration)}, done around {eta_str}"
    )
    try:
        await slack.post_to_thread(
            previous.slack_channel,
            previous.slack_thread_ts,
            restart_text,
        )
    except SlackApiError as e:
        logging.error(f"Failed to post restart notification: {e}")

    if previous.actions_ts is not None:
        try:
            await slack.edit_message(
                previous.slack_channel,
                previous.actions_ts,
                "No actions available — awaiting claim.",
                blocks=build_terminal_actions_blocks("awaiting claim"),
            )
        except SlackApiError as e:
            logging.error(f"Failed to update actions message on adoption: {e}")


async def _job_started(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
    job_registry: JobRegistry,
) -> None:
    assert report.mc_remaining_time is not None
    duration = timedelta(minutes=report.mc_remaining_time)
    now = datetime.now(tz=CONFIG.timezone)
    eta = now + duration
    start_message = f"{device.device_name}: Print started, {_format_duration(duration)}, done around {_format_eta(eta)}"

    previous = state.previous_job
    state.previous_job = None  # Consumed regardless of adoption outcome

    job = PrintJob(
        duration=duration,
        eta=eta,
        state=JobState.RUNNING,
        start_message=start_message,
    )

    if previous is not None and _should_adopt(previous, duration, now):
        job.slack_channel = previous.slack_channel
        job.slack_thread_ts = previous.slack_thread_ts
        job.actions_ts = previous.actions_ts
        await _adopt_thread(slack, previous, job, device)
    else:
        claim_blocks = _build_status_blocks(f":progress_bar: {start_message}", None)
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
    if job.slack_thread_ts is not None:
        job_registry[job.slack_thread_ts] = state
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


async def _job_paused(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None
    error_message = parse_error_code(report.print_error) if report.print_error else None
    state.current_job.state = JobState.PAUSED
    state.current_job.eta = None
    await _update_job(
        slack,
        state.current_job,
        device,
        ":warning:",
        "Paused",
        f"Print paused\nMessage from printer: {error_message}"
        if error_message
        else "Print paused!",
        actions=available_actions(state.current_job),
    )


async def _job_resumed(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None and report.mc_remaining_time is not None
    remaining_duration = timedelta(minutes=report.mc_remaining_time)
    eta = datetime.now(tz=CONFIG.timezone) + remaining_duration
    state.current_job.state = JobState.RUNNING
    state.current_job.eta = eta
    await _update_job(
        slack,
        state.current_job,
        device,
        ":progress_bar:",
        f"Resumed, done around {_format_eta(eta)}",
        f"Print resumed, now done around {_format_eta(eta)}",
        actions=available_actions(state.current_job),
    )


async def _job_failed(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None
    if report.print_error in CANCELLED_ERROR_CODES:
        await _update_job(
            slack,
            state.current_job,
            device,
            ":heavy_minus_sign:",
            "Cancelled",
            "Print cancelled",
            actions=[],
            terminal_reason="print cancelled",
        )
        ended_at = datetime.now(tz=CONFIG.timezone)
        state.previous_job = _build_previous_job_info(state.current_job, ended_at)
        state.current_job = None
    else:
        error_message = parse_error_code(report.print_error)
        await _update_job(
            slack,
            state.current_job,
            device,
            ":x:",
            "Failed!",
            f"Print failed!\nMessage from printer: {error_message}",
            actions=[],
            terminal_reason="print failed",
        )
        state.previous_job = None
        state.current_job = None


async def _job_completed(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None
    await _update_job(
        slack,
        state.current_job,
        device,
        ":white_check_mark:",
        "Finished!",
        "Print finished!",
        actions=[],
        terminal_reason="print completed",
    )
    state.previous_job = None
    state.current_job = None


async def _job_tracking_lost(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None
    await _update_job(
        slack,
        state.current_job,
        device,
        ":question:",
        "Tracking lost",
        "Lost tracking for print job - maybe the printer was disconnected?",
        actions=[],
        terminal_reason="print tracking lost",
    )
    ended_at = datetime.now(tz=CONFIG.timezone)
    state.previous_job = _build_previous_job_info(state.current_job, ended_at)
    state.current_job = None


async def monitor_printer_status(
    device: BambuDevice,
    connection: BambuMqttConnection,
    slack: SlackClient,
    job_registry: JobRegistry,
) -> None:
    """
    Subscribe to events from the given printer and report on print status.

    This function operates as a state machine, with state persisted to a file.
    """
    try:
        await _STATE_DIR.mkdir(exist_ok=True, parents=True)
        state_file = _STATE_DIR / f'job.{device.filename_prefix}.json'
        if await state_file.exists():
            async with await state_file.open('r') as f:
                state_str = json.loads(await f.read())
                logging.info(f"Loading state file {state_file}")
                state = _STATE_SERIALIZER.structure(state_str, PrinterState)
                if (
                    state.current_job is not None
                    and state.current_job.slack_thread_ts is not None
                ):
                    job_registry[state.current_job.slack_thread_ts] = state
        else:
            state = PrinterState()

        state.connection = connection

        try:
            async with connection.subscribe() as reports:
                async for report in reports:
                    match (
                        state.current_job,
                        report.gcode_state,
                        report.mc_remaining_time,
                    ):
                        case (_, 'RUNNING', None | 0):
                            # wait until we get the remaining time
                            pass
                        case (None, 'RUNNING', _):
                            await _job_started(
                                slack, report, state, device, job_registry
                            )
                        case (PrintJob(state=JobState.RUNNING), 'PAUSE', _):
                            await _job_paused(slack, report, state, device)
                        case (PrintJob(state=JobState.PAUSED), 'RUNNING', _):
                            await _job_resumed(slack, report, state, device)
                        case (PrintJob(), 'FINISH', _):
                            await _job_completed(slack, report, state, device)
                        case (PrintJob(), 'FAILED', _):
                            await _job_failed(slack, report, state, device)
                        case (PrintJob(), 'IDLE', _):
                            await _job_tracking_lost(slack, report, state, device)
        finally:
            serialized = json.dumps(_STATE_SERIALIZER.unstructure(state))
            async with await state_file.open('w') as f:
                await f.write(serialized)
                logging.info(f"Saved state file {state_file}")
    except CancelledError:
        raise
    except Exception:
        logging.exception(
            f"Error while monitoring printer {device.device_id} ({device.device_name})"
        )
