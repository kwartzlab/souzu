import json
import logging
from datetime import datetime
from pathlib import Path

from anyio import Path as AsyncPath

from souzu.bambu.mqtt import SERIALIZER, BambuStatusReport


async def compact_log_file(input_file: Path, output_file: Path) -> tuple[int, int]:
    """
    Compact a log file by removing duplicate consecutive reports.

    Args:
        input_file: Path to the input log file
        output_file: Path to the output log file

    Returns:
        A tuple containing (original_line_count, compacted_line_count)
    """
    try:
        async_input = AsyncPath(input_file)
        async_output = AsyncPath(output_file)

        await async_output.parent.mkdir(parents=True, exist_ok=True)

        line_count = 0
        compacted_count = 0
        last_report = None

        async with (
            await async_input.open('r') as fin,
            await async_output.open('w') as fout,
        ):
            async for line in fin:
                line_count += 1
                try:
                    timestamp_str, report_json = line.split(' ', maxsplit=1)
                    _ = datetime.fromisoformat(timestamp_str)

                    obj = json.loads(report_json)
                    report = SERIALIZER.structure(obj, BambuStatusReport)

                    if report != last_report:
                        last_report = report
                        compacted_count += 1
                        await fout.write(line)
                except Exception as err:
                    logging.warning(f"Error processing line {line_count}: {err}")
                    await fout.write(line)
                    compacted_count += 1

        return line_count, compacted_count
    except Exception:
        logging.exception(f"Failed to compact log file {input_file}")
        raise


async def compact(input_file: Path, output_file: Path | None = None) -> None:
    """
    Compact a printer log file to remove redundant entries.

    Args:
        input_file: Path to the log file to compact
        output_file: Optional path to the output file. If not provided,
                     will use the input filename with a .compact suffix
    """
    if not output_file:
        output_file = input_file.with_suffix('.compact.log')

    line_count, compacted_count = await compact_log_file(input_file, output_file)

    reduction_pct = 0.0
    if line_count > 0:
        reduction_pct = ((line_count - compacted_count) / line_count) * 100.0

    logging.info(
        f"Compacted {input_file} from {line_count} to {compacted_count} lines "
        f"({reduction_pct:.1f}% reduction)"
    )
