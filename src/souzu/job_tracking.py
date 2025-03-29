import json
import logging
from concurrent.futures import CancelledError
from datetime import datetime, timedelta
from enum import Enum
from math import ceil

from anyio import Path as AsyncPath
from attrs import define
from cattrs import Converter
from xdg_base_dirs import xdg_state_home

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.errors import CANCELLED_ERROR_CODES, parse_error_code
from souzu.bambu.mqtt import BambuMqttConnection, BambuStatusReport
from souzu.config import CONFIG
from souzu.slack.thread import (
    SlackApiError,
    edit_message,
    post_to_channel,
    post_to_thread,
)

_ONE_MINUTE = timedelta(minutes=1)
_FIVE_MINUTES = timedelta(minutes=5)
_HALF_HOUR = timedelta(minutes=30)
_FIFTY_FIVE_MINUTES = timedelta(minutes=55)
_ONE_HOUR = timedelta(hours=1)
_EIGHT_HOURS = timedelta(hours=8)

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


class JobState(Enum):
    RUNNING = 'running'
    PAUSED = 'paused'


_STATE_SERIALIZER.register_unstructure_hook(JobState, lambda state: state.value)
_STATE_SERIALIZER.register_structure_hook(
    JobState, lambda state_value, _: JobState(state_value)
)


@define
class PrintJob:
    duration: timedelta
    eta: datetime | None = None
    state: JobState = JobState.RUNNING
    slack_channel: str | None = None
    slack_thread_ts: str | None = None
    start_message: str | None = None


@define
class PrinterState:
    current_job: PrintJob | None = None


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


async def _update_thread(
    job: PrintJob, device: BambuDevice, edited_message: str, update_message: str
) -> None:
    if job.slack_thread_ts is None:
        try:
            await post_to_channel(
                job.slack_channel or CONFIG.slack.print_notification_channel,
                update_message,
            )
        except SlackApiError as e:
            logging.error(f"Failed to notify channel: {e}")
        return

    try:
        await post_to_thread(
            job.slack_channel or CONFIG.slack.print_notification_channel,
            job.slack_thread_ts,
            update_message,
        )
    except SlackApiError as e:
        logging.error(f"Failed to notify thread: {e}")
        if job.slack_thread_ts:
            # we tried to post to thread, we can try posting to the channel instead
            try:
                await post_to_channel(
                    job.slack_channel or CONFIG.slack.print_notification_channel,
                    update_message,
                )
            except SlackApiError as e:
                logging.error(f"Failed to notify channel as fallback: {e}")
    try:
        await edit_message(
            job.slack_channel or CONFIG.slack.print_notification_channel,
            job.slack_thread_ts,
            edited_message,
        )
    except SlackApiError as e:
        logging.error(f"Failed to edit message: {e}")


async def _update_job(
    job: PrintJob,
    device: BambuDevice,
    emoji: str,
    short_message: str,
    long_message: str | None = None,
) -> None:
    update_prefix = f"{emoji} {device.device_name}: "
    edit_prefix = (
        f"~{job.start_message}~\n{emoji} " if job.start_message else update_prefix
    )
    await _update_thread(
        job,
        device,
        f"{edit_prefix}{short_message}",
        f"{update_prefix}{long_message or short_message}",
    )


async def _job_started(
    report: BambuStatusReport, state: PrinterState, device: BambuDevice
) -> None:
    assert report.mc_remaining_time is not None
    duration = timedelta(minutes=report.mc_remaining_time)
    eta = datetime.now(tz=CONFIG.timezone) + duration
    start_message = f"{device.device_name}: Print started, {_format_duration(duration)}, done around {_format_eta(eta)}"
    job = PrintJob(
        duration=duration,
        eta=eta,
        state=JobState.RUNNING,
        start_message=start_message,
    )
    try:
        thread_ts = await post_to_channel(
            CONFIG.slack.print_notification_channel,
            f":progress_bar: {job.start_message}",
        )
        job.slack_channel = CONFIG.slack.print_notification_channel
        job.slack_thread_ts = thread_ts
    except SlackApiError as e:
        logging.error(f"Failed to notify channel: {e}")
    state.current_job = job


async def _job_paused(
    report: BambuStatusReport, state: PrinterState, device: BambuDevice
) -> None:
    assert state.current_job is not None
    error_message = parse_error_code(report.print_error) if report.print_error else None
    await _update_job(
        state.current_job,
        device,
        ":warning:",
        "Paused",
        f"Print paused\nMessage from printer: {error_message}"
        if error_message
        else "Print paused!",
    )
    state.current_job.state = JobState.PAUSED
    state.current_job.eta = None


async def _job_resumed(
    report: BambuStatusReport, state: PrinterState, device: BambuDevice
) -> None:
    assert state.current_job is not None and report.mc_remaining_time is not None
    remaining_duration = timedelta(minutes=report.mc_remaining_time)
    eta = datetime.now(tz=CONFIG.timezone) + remaining_duration
    await _update_job(
        state.current_job,
        device,
        ":progress_bar:",
        f"Resumed, done around {_format_eta(eta)}",
        f"Print resumed, now done around {_format_eta(eta)}",
    )
    state.current_job.state = JobState.RUNNING
    state.current_job.eta = eta


async def _job_failed(
    report: BambuStatusReport, state: PrinterState, device: BambuDevice
) -> None:
    assert state.current_job is not None
    if report.print_error in CANCELLED_ERROR_CODES:
        await _update_job(
            state.current_job,
            device,
            ":heavy_minus_sign:",
            "Cancelled",
            "Print cancelled",
        )
        state.current_job = None
    else:
        error_message = parse_error_code(report.print_error)
        await _update_job(
            state.current_job,
            device,
            ":x:",
            "Failed!",
            f"Print failed!\nMessage from printer: {error_message}",
        )
        state.current_job = None


async def _job_completed(
    report: BambuStatusReport, state: PrinterState, device: BambuDevice
) -> None:
    assert state.current_job is not None
    await _update_job(
        state.current_job,
        device,
        ":white_check_mark:",
        "Finished!",
        "Print finished!",
    )
    state.current_job = None


async def _job_tracking_lost(
    report: BambuStatusReport, state: PrinterState, device: BambuDevice
) -> None:
    assert state.current_job is not None
    await _update_job(
        state.current_job,
        device,
        ":question:",
        "Tracking lost",
        "Lost tracking for print job - maybe the printer was disconnected?",
    )
    state.current_job = None


async def monitor_printer_status(
    device: BambuDevice, connection: BambuMqttConnection
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
        else:
            state = PrinterState()

        try:
            async with connection.subscribe() as reports:
                async for report in reports:
                    match (state.current_job, report.gcode_state):
                        case (None, 'RUNNING'):
                            await _job_started(report, state, device)
                        case (PrintJob(state=JobState.RUNNING), 'PAUSE'):
                            await _job_paused(report, state, device)
                        case (PrintJob(state=JobState.PAUSED), 'RUNNING'):
                            await _job_resumed(report, state, device)
                        case (PrintJob(), 'FINISH'):
                            await _job_completed(report, state, device)
                        case (PrintJob(), 'FAILED'):
                            await _job_failed(report, state, device)
                        case (PrintJob(), 'IDLE'):
                            await _job_tracking_lost(report, state, device)
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
