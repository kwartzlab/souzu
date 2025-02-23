import json
import logging
from collections.abc import AsyncIterable
from concurrent.futures import CancelledError
from datetime import datetime, timedelta
from math import ceil

from anyio import Path as AsyncPath
from attrs import define, frozen
from cattrs import Converter
from xdg_base_dirs import xdg_state_home

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.errors import parse_error_code
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
_DATE_TIME_FORMAT = '%A at %I:%M %p'


_STATE_DIR = AsyncPath(xdg_state_home() / 'souzu')


_STATE_SERIALIZER = Converter()
_STATE_SERIALIZER.register_unstructure_hook(timedelta, lambda td: td.total_seconds())
_STATE_SERIALIZER.register_structure_hook(
    timedelta, lambda td, _: timedelta(seconds=td)
)


@define
class PrintJob:
    duration: timedelta
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


@frozen
class Eta:
    duration: str
    finish_time: str


def _format_eta(duration: timedelta) -> Eta:
    """
    Return a human-readable string representing the duration and finish time of the print job.

    Add a fudge factor to account for estimation error.
    """

    finish_time = datetime.now() + duration

    if duration < _ONE_MINUTE:
        return Eta(
            duration="1 minute",
            finish_time=_round_up(finish_time, _ONE_MINUTE).strftime(_TIME_FORMAT),
        )
    elif duration < _FIFTY_FIVE_MINUTES:
        # round up to next 5 minutes
        minutes = ceil(duration / _FIVE_MINUTES) * 5
        return Eta(
            duration=f"{minutes} minutes",
            finish_time=_round_up(finish_time, _FIVE_MINUTES).strftime(_TIME_FORMAT),
        )
    elif duration < _EIGHT_HOURS:
        # round up to next half hour
        hours = ceil(duration / _HALF_HOUR) / 2
        if hours == 1:
            hours_str = "1 hour"
        elif hours.is_integer():
            hours_str = f"{int(hours)} hours"
        else:
            hours_str = f"{hours:.1f} hours"
        return Eta(
            duration=hours_str,
            finish_time=_round_up(finish_time, _HALF_HOUR).strftime(_TIME_FORMAT),
        )
    else:
        # round up to next hour
        hours = int(ceil(duration / _ONE_HOUR))
        rounded_finish_time = _round_up(finish_time, _ONE_HOUR)
        if rounded_finish_time.date != datetime.now().date:
            finish_str = rounded_finish_time.strftime(_DATE_TIME_FORMAT)
        else:
            finish_str = rounded_finish_time.strftime(_TIME_FORMAT)
        return Eta(
            duration=f"{hours} hours",
            finish_time=finish_str,
        )


async def _wait_for_job(reports: AsyncIterable[BambuStatusReport]) -> PrintJob:
    """
    Consume messages from the queue until a job is running with estimated time available.
    """
    async for report in reports:
        if report.gcode_state == 'RUNNING' and report.mc_remaining_time:
            return PrintJob(duration=timedelta(minutes=report.mc_remaining_time))
    raise CancelledError("No print job found")


async def _wait_for_job_completion(
    reports: AsyncIterable[BambuStatusReport],
) -> str | None:
    """
    Wait until the print job completes or errors.

    If the print job completes, return None.
    If the print job has an error, return a human-readable error message, or the error code, or some other string.
    """
    async for report in reports:
        if report.gcode_state == 'FAILED':
            return parse_error_code(report.print_error)
        elif report.gcode_state == 'FINISH':
            return None
    raise CancelledError("Job completion not found")


async def _notify_job_started(job: PrintJob, device: BambuDevice) -> None:
    try:
        eta = _format_eta(job.duration)
        job.start_message = f"{device.device_name}: Print started, {eta.duration}, done around {eta.finish_time}"
        thread_ts = await post_to_channel(
            CONFIG.slack.print_notification_channel,
            f":progress_bar: {job.start_message}",
        )
        job.slack_channel = CONFIG.slack.print_notification_channel
        job.slack_thread_ts = thread_ts
    except SlackApiError as e:
        logging.error(f"Failed to notify channel: {e}")


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
                while True:
                    if state.current_job is None:
                        state.current_job = await _wait_for_job(reports)
                        await _notify_job_started(state.current_job, device)
                    else:
                        # TODO detect paused prints
                        error = await _wait_for_job_completion(reports)
                        if error:
                            await _update_job(
                                state.current_job,
                                device,
                                ":x:",
                                "Failed!",
                                f"Print failed!\nMessage from printer: {error}",
                            )
                        else:
                            await _update_job(
                                state.current_job,
                                device,
                                ":white_check_mark:",
                                "Finished!",
                                "Print finished!",
                            )
                        state.current_job = None
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
