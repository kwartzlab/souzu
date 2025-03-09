import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from anyio import Path as AsyncPath
from xdg_base_dirs import xdg_cache_home

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import SERIALIZER, BambuMqttConnection, BambuStatusReport

LOG_DIRECTORY = xdg_cache_home() / "souzu/logs"


async def log_reports(
    device: BambuDevice,
    connection: BambuMqttConnection,
    *,
    log_directory: Path | None = None,
) -> None:
    file = (log_directory or LOG_DIRECTORY) / f"{device.filename_prefix}.log"
    # TODO add log rotation daily
    # TODO expire oldest if it's larger than 10% of available disk space
    try:
        async_file = AsyncPath(file)
        await async_file.parent.mkdir(parents=True, exist_ok=True)
        async with await async_file.open('a') as f, connection.subscribe() as reports:
            last_report = None
            async for report in reports:
                if report != last_report:
                    last_report = report
                    report_json = json.dumps(SERIALIZER.unstructure(report))
                    timestamp = datetime.now(UTC).isoformat()
                    await f.write(f"{timestamp} {report_json}\n")
    except Exception:
        logging.exception(f"Logger task failed for {device.device_name}")


async def replay_logs(file: Path) -> AsyncIterator[BambuStatusReport]:
    try:
        async with await AsyncPath(file).open('r') as f:
            async for line in f:
                report = line.split(' ', maxsplit=1)[1]
                obj = json.loads(report)
                yield SERIALIZER.structure(obj, BambuStatusReport)
    except Exception:
        logging.exception(f"Failed to replay logs from {file}")
