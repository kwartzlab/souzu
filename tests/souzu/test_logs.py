import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from anyio import Path as AsyncPath
from pytest_mock import MockerFixture

from souzu.bambu.discovery import BambuDevice
from souzu.bambu.mqtt import SERIALIZER, BambuMqttConnection, BambuStatusReport
from souzu.logs import log_reports, replay_logs


@pytest.fixture
def temp_log_dir(tmp_path: Path) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


@pytest.fixture
def mock_device() -> BambuDevice:
    return BambuDevice(
        device_id="DEVICE123456",
        device_name="Test Printer",
        ip_address="192.168.1.100",
        filename_prefix="test_printer",
    )


@pytest.fixture
def mock_report() -> BambuStatusReport:
    return BambuStatusReport(
        bed_temper=60,
        nozzle_temper=210,
    )


@pytest.fixture
def mock_connection(mocker: MockerFixture) -> MagicMock:
    """Create a mock BambuMqttConnection object for testing."""
    connection = MagicMock(spec=BambuMqttConnection)
    subscribe_context = MagicMock()
    connection.subscribe.return_value = subscribe_context
    return connection


@pytest.mark.asyncio
async def test_log_reports(
    mock_device: BambuDevice,
    mock_connection: MagicMock,
    mock_report: BambuStatusReport,
    temp_log_dir: Path,
    mocker: MockerFixture,
) -> None:
    fixed_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
    mocker.patch("souzu.logs.datetime", wraps=datetime)
    datetime_mock = mocker.patch("souzu.logs.datetime.now")
    datetime_mock.return_value = fixed_time

    async def async_iter() -> AsyncGenerator[BambuStatusReport, None]:
        yield mock_report

    mock_connection.subscribe.return_value.__aenter__.return_value = async_iter()
    await log_reports(mock_device, mock_connection, log_directory=temp_log_dir)

    log_file = temp_log_dir / f"{mock_device.filename_prefix}.log"
    assert log_file.exists()

    async_file = AsyncPath(log_file)
    content = await async_file.read_text()
    content = content.strip()
    expected_json = json.dumps(SERIALIZER.unstructure(mock_report))
    expected_line = f"{fixed_time.isoformat()} {expected_json}"
    assert content == expected_line


@pytest.mark.asyncio
async def test_log_reports_with_changing_reports(
    mock_device: BambuDevice,
    mock_connection: MagicMock,
    temp_log_dir: Path,
    mocker: MockerFixture,
) -> None:
    report1 = BambuStatusReport(bed_temper=60, nozzle_temper=210)
    report2 = BambuStatusReport(bed_temper=70, nozzle_temper=220)

    fixed_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
    mocker.patch("souzu.logs.datetime", wraps=datetime)
    datetime_mock = mocker.patch("souzu.logs.datetime.now")
    datetime_mock.return_value = fixed_time

    async def async_iter() -> AsyncGenerator[BambuStatusReport, None]:
        yield report1
        yield report2

    mock_connection.subscribe.return_value.__aenter__.return_value = async_iter()
    await log_reports(mock_device, mock_connection, log_directory=temp_log_dir)

    log_file = temp_log_dir / f"{mock_device.filename_prefix}.log"
    assert log_file.exists()

    async_file = AsyncPath(log_file)
    content = await async_file.read_text()
    lines = content.strip().split('\n')
    assert len(lines) == 2

    expected_json1 = json.dumps(SERIALIZER.unstructure(report1))
    expected_line1 = f"{fixed_time.isoformat()} {expected_json1}"
    assert lines[0] == expected_line1

    expected_json2 = json.dumps(SERIALIZER.unstructure(report2))
    expected_line2 = f"{fixed_time.isoformat()} {expected_json2}"
    assert lines[1] == expected_line2


@pytest.mark.asyncio
async def test_log_reports_exception_handling(
    mock_device: BambuDevice,
    mock_connection: MagicMock,
    temp_log_dir: Path,
    mocker: MockerFixture,
) -> None:
    mock_connection.subscribe.return_value.__aenter__.side_effect = Exception(
        "Test exception"
    )

    mock_logging = mocker.patch("souzu.logs.logging.exception")
    await log_reports(mock_device, mock_connection, log_directory=temp_log_dir)

    mock_logging.assert_called_once_with(
        f"Logger task failed for {mock_device.device_name}"
    )


@pytest.mark.asyncio
async def test_replay_logs(
    mock_report: BambuStatusReport,
    temp_log_dir: Path,
    mocker: MockerFixture,
) -> None:
    log_file = temp_log_dir / "test_replay.log"

    timestamp = "2023-01-01T12:00:00+00:00"
    report_json = json.dumps(SERIALIZER.unstructure(mock_report))
    async_file = AsyncPath(log_file)
    content = f"{timestamp} {report_json}\n{timestamp} {report_json}\n"
    await async_file.write_text(content)

    reports = []
    async for report in replay_logs(log_file):
        reports.append(report)

    assert len(reports) == 2
    assert all(isinstance(report, BambuStatusReport) for report in reports)
    assert all(report.bed_temper == mock_report.bed_temper for report in reports)
    assert all(report.nozzle_temper == mock_report.nozzle_temper for report in reports)


@pytest.mark.asyncio
async def test_replay_logs_exception_handling(
    temp_log_dir: Path,
    mocker: MockerFixture,
) -> None:
    log_file = temp_log_dir / "invalid_log.log"
    async_file = AsyncPath(log_file)
    await async_file.write_text(
        "invalid json format\n"
    )  # Create file with invalid format

    mock_logging = mocker.patch("souzu.logs.logging.exception")

    reports = []
    async for report in replay_logs(log_file):
        reports.append(report)

    assert len(reports) == 0
    mock_logging.assert_called_once_with(f"Failed to replay logs from {log_file}")
