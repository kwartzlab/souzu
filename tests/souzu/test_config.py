from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from souzu.config import (
    SERIALIZER,
    Config,
    PrinterConfig,
    SlackConfig,
    _convert_timezone,
)


def test_convert_timezone_valid() -> None:
    """Test that _convert_timezone correctly converts a valid timezone string."""
    tz_str = "UTC"
    result = _convert_timezone(tz_str, ZoneInfo)

    assert isinstance(result, ZoneInfo)
    assert str(result) == tz_str


def test_convert_timezone_invalid() -> None:
    """Test that _convert_timezone falls back to UTC for invalid timezone."""
    invalid_tz = "InvalidTimezone"

    with patch("logging.warning") as mock_warning:
        with patch(
            "zoneinfo.ZoneInfo", side_effect=[ZoneInfoNotFoundError(), ZoneInfo("UTC")]
        ):
            result = _convert_timezone(invalid_tz, ZoneInfo)

            mock_warning.assert_called_once_with(
                f"Invalid timezone {invalid_tz}, falling back to UTC"
            )
            assert isinstance(result, ZoneInfo)
            assert str(result) == "UTC"


def test_serializer_structure_hook() -> None:
    """Test that SERIALIZER correctly converts timezone strings."""
    config_dict = {"timezone": "America/New_York"}

    result = SERIALIZER.structure(config_dict, Config)

    assert isinstance(result.timezone, ZoneInfo)
    assert str(result.timezone) == "America/New_York"


def test_printer_config() -> None:
    """Test PrinterConfig initialization and attributes."""
    access_code = "1234567890"
    filename_prefix = "test_printer"
    ip_address = "192.168.1.100"

    printer_config = PrinterConfig(
        access_code=access_code,
        filename_prefix=filename_prefix,
        ip_address=ip_address,
    )

    assert printer_config.access_code == access_code
    assert printer_config.filename_prefix == filename_prefix
    assert printer_config.ip_address == ip_address

    printer_config = PrinterConfig(access_code=access_code)

    assert printer_config.access_code == access_code
    assert printer_config.filename_prefix is None
    assert printer_config.ip_address is None


def test_slack_config() -> None:
    """Test SlackConfig initialization and attributes."""
    access_token = "xoxb-123456789"  # noqa: S105 - Test token, not real
    print_channel = "print-notifications"
    error_channel = "error-notifications"

    slack_config = SlackConfig(
        access_token=access_token,
        print_notification_channel=print_channel,
        error_notification_channel=error_channel,
    )

    assert slack_config.access_token == access_token
    assert slack_config.print_notification_channel == print_channel
    assert slack_config.error_notification_channel == error_channel

    slack_config = SlackConfig()

    assert slack_config.access_token is None
    assert slack_config.print_notification_channel is None
    assert slack_config.error_notification_channel is None


def test_config() -> None:
    """Test Config initialization and attributes."""
    config = Config()

    assert config.printers == {}
    assert isinstance(config.slack, SlackConfig)
    assert isinstance(config.timezone, ZoneInfo)
    assert str(config.timezone) == "UTC"

    printer_config = PrinterConfig(access_code="test_code")
    slack_config = SlackConfig(access_token="test_token")  # noqa: S106 - Test token, not real
    timezone = ZoneInfo("America/New_York")

    config = Config(
        printers={"test_printer": printer_config},
        slack=slack_config,
        timezone=timezone,
    )

    assert len(config.printers) == 1
    assert "test_printer" in config.printers
    assert config.printers["test_printer"] == printer_config
    assert config.slack == slack_config
    assert config.timezone == timezone


def test_config_loading_from_file() -> None:
    """Test loading configuration from a file directly."""
    test_config = {
        "printers": {
            "printer1": {
                "access_code": "test_code",
                "filename_prefix": "prefix1",
                "ip_address": "192.168.1.100",
            }
        },
        "slack": {
            "access_token": "xoxb-test",
            "print_notification_channel": "prints",
            "error_notification_channel": "errors",
        },
        "timezone": "Europe/London",
    }

    config = SERIALIZER.structure(test_config, Config)

    assert len(config.printers) == 1
    assert "printer1" in config.printers
    assert config.printers["printer1"].access_code == "test_code"
    assert config.printers["printer1"].filename_prefix == "prefix1"
    assert config.slack.access_token == "xoxb-test"  # noqa: S105 - Test token, not real
    assert isinstance(config.timezone, ZoneInfo)
    assert str(config.timezone) == "Europe/London"


@patch("souzu.config._CONFIG_FILE")
def test_config_loading_file_not_exists(mock_config_file: MagicMock) -> None:
    """Test loading default configuration when file doesn't exist."""
    mock_config_file.exists.return_value = False

    import importlib

    import souzu.config

    importlib.reload(souzu.config)

    assert souzu.config.CONFIG.printers == {}
    assert souzu.config.CONFIG.slack.access_token is None
    assert isinstance(souzu.config.CONFIG.timezone, ZoneInfo)
    assert str(souzu.config.CONFIG.timezone) == "UTC"
