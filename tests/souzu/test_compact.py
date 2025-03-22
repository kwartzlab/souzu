import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from anyio import Path as AsyncPath
from pytest_mock import MockerFixture

from souzu.bambu.mqtt import SERIALIZER, BambuStatusReport
from souzu.commands.compact import compact, compact_log_file


@pytest.fixture
def test_report1() -> BambuStatusReport:
    return BambuStatusReport(
        bed_temper=60,
        nozzle_temper=210,
    )


@pytest.fixture
def test_report2() -> BambuStatusReport:
    return BambuStatusReport(
        bed_temper=70,
        nozzle_temper=220,
    )


@pytest.mark.asyncio
async def test_compact_log_file_with_duplicates(
    tmp_path: Path,
    test_report1: BambuStatusReport,
    test_report2: BambuStatusReport,
) -> None:
    input_log = tmp_path / "test_input.log"
    output_log = tmp_path / "test_output.log"

    timestamp1 = "2023-01-01T12:00:00+00:00"
    timestamp2 = "2023-01-01T12:01:00+00:00"

    report1_json = json.dumps(SERIALIZER.unstructure(test_report1))
    report2_json = json.dumps(SERIALIZER.unstructure(test_report2))

    log_content = (
        f"{timestamp1} {report1_json}\n"
        f"{timestamp1} {report1_json}\n"
        f"{timestamp2} {report2_json}\n"
        f"{timestamp2} {report2_json}\n"
        f"{timestamp2} {report1_json}\n"
    )

    async_input = AsyncPath(input_log)
    await async_input.write_text(log_content)

    line_count, compacted_count = await compact_log_file(input_log, output_log)

    assert line_count == 5
    assert compacted_count == 3

    async_output = AsyncPath(output_log)
    output_content = await async_output.read_text()
    output_lines = output_content.strip().split('\n')

    assert len(output_lines) == 3

    expected_lines = [
        f"{timestamp1} {report1_json}",
        f"{timestamp2} {report2_json}",
        f"{timestamp2} {report1_json}",
    ]
    assert output_lines == expected_lines


@pytest.mark.asyncio
async def test_compact_log_file_no_duplicates(
    tmp_path: Path,
    test_report1: BambuStatusReport,
    test_report2: BambuStatusReport,
) -> None:
    input_log = tmp_path / "test_input.log"
    output_log = tmp_path / "test_output.log"

    timestamp1 = "2023-01-01T12:00:00+00:00"
    timestamp2 = "2023-01-01T12:01:00+00:00"

    report1_json = json.dumps(SERIALIZER.unstructure(test_report1))
    report2_json = json.dumps(SERIALIZER.unstructure(test_report2))

    log_content = f"{timestamp1} {report1_json}\n{timestamp2} {report2_json}\n"

    async_input = AsyncPath(input_log)
    await async_input.write_text(log_content)

    line_count, compacted_count = await compact_log_file(input_log, output_log)

    assert line_count == 2
    assert compacted_count == 2

    async_output = AsyncPath(output_log)
    output_content = await async_output.read_text()
    output_lines = output_content.strip().split('\n')

    assert len(output_lines) == 2

    expected_lines = [
        f"{timestamp1} {report1_json}",
        f"{timestamp2} {report2_json}",
    ]
    assert output_lines == expected_lines


@pytest.mark.asyncio
async def test_compact_log_file_invalid_lines(
    tmp_path: Path,
    test_report1: BambuStatusReport,
) -> None:
    input_log = tmp_path / "test_input.log"
    output_log = tmp_path / "test_output.log"

    timestamp = "2023-01-01T12:00:00+00:00"
    report_json = json.dumps(SERIALIZER.unstructure(test_report1))

    log_content = f"{timestamp} {report_json}\nInvalid line without proper format\n"

    async_input = AsyncPath(input_log)
    await async_input.write_text(log_content)

    line_count, compacted_count = await compact_log_file(input_log, output_log)

    assert line_count == 2
    assert compacted_count == 2  # Both lines preserved

    async_output = AsyncPath(output_log)
    output_content = await async_output.read_text()
    output_lines = output_content.strip().split('\n')

    assert len(output_lines) == 2

    assert output_lines[0] == f"{timestamp} {report_json}"
    assert output_lines[1] == "Invalid line without proper format"


@pytest.mark.asyncio
async def test_compact_log_file_error_handling(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    """Test error handling in compact_log_file."""
    input_log = tmp_path / "test_input.log"
    output_log = tmp_path / "test_output.log"

    async_input = AsyncPath(input_log)
    await async_input.write_text("Test content")

    mock_open = AsyncMock()
    mock_open.side_effect = Exception("Test exception")

    with patch("anyio.Path.open", mock_open):
        mock_logging = mocker.patch("souzu.commands.compact.logging.exception")

        with pytest.raises(Exception, match="Test exception"):
            await compact_log_file(input_log, output_log)

        mock_logging.assert_called_once_with(f"Failed to compact log file {input_log}")


@pytest.mark.asyncio
async def test_compact_with_default_output(
    tmp_path: Path,
    test_report1: BambuStatusReport,
    mocker: MockerFixture,
) -> None:
    """Test compact function with default output file."""
    input_log = tmp_path / "test.log"
    expected_output_log = input_log.with_suffix('.compact.log')

    timestamp = "2023-01-01T12:00:00+00:00"
    report_json = json.dumps(SERIALIZER.unstructure(test_report1))
    log_content = f"{timestamp} {report_json}\n{timestamp} {report_json}\n"

    async_input = AsyncPath(input_log)
    await async_input.write_text(log_content)

    mock_logging = mocker.patch("souzu.commands.compact.logging.info")

    await compact(input_log)

    async_output = AsyncPath(expected_output_log)
    assert await async_output.exists()

    mock_logging.assert_called_once()
    log_message = mock_logging.call_args[0][0]
    assert "Compacted" in log_message
    assert "from 2 to 1 lines" in log_message
    assert "50.0% reduction" in log_message


@pytest.mark.asyncio
async def test_compact_with_custom_output(
    tmp_path: Path,
    test_report1: BambuStatusReport,
    mocker: MockerFixture,
) -> None:
    """Test compact function with custom output file."""
    input_log = tmp_path / "test.log"
    output_log = tmp_path / "custom_output.log"

    timestamp = "2023-01-01T12:00:00+00:00"
    report_json = json.dumps(SERIALIZER.unstructure(test_report1))
    log_content = f"{timestamp} {report_json}\n{timestamp} {report_json}\n"

    async_input = AsyncPath(input_log)
    await async_input.write_text(log_content)

    mock_logging = mocker.patch("souzu.commands.compact.logging.info")

    await compact(input_log, output_log)

    async_output = AsyncPath(output_log)
    assert await async_output.exists()

    mock_logging.assert_called_once()
    log_message = mock_logging.call_args[0][0]
    assert "Compacted" in log_message
    assert "from 2 to 1 lines" in log_message
    assert "50.0% reduction" in log_message


@pytest.mark.asyncio
async def test_compact_empty_file(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    """Test compact function with an empty file (edge case)."""
    input_log = tmp_path / "empty.log"
    output_log = tmp_path / "empty_output.log"

    async_input = AsyncPath(input_log)
    await async_input.write_text("")

    mock_logging = mocker.patch("souzu.commands.compact.logging.info")

    await compact(input_log, output_log)

    async_output = AsyncPath(output_log)
    assert await async_output.exists()

    mock_logging.assert_called_once()
    log_message = mock_logging.call_args[0][0]
    assert "Compacted" in log_message
    assert "from 0 to 0 lines" in log_message
    assert "0.0% reduction" in log_message
