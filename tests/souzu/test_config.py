import json
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from souzu.config import (
    SERIALIZER,
    Config,
    PrinterConfig,
    SlackConfig,
    _convert_timezone,
)

# Register an unstructure hook for ZoneInfo for our tests
SERIALIZER.register_unstructure_hook(ZoneInfo, lambda tz: str(tz))


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


def test_serializer_hook_timezone_structure() -> None:
    """Test that SERIALIZER correctly converts timezone strings to ZoneInfo objects."""
    config_dict = {"timezone": "America/New_York"}

    result = SERIALIZER.structure(config_dict, Config)

    assert isinstance(result.timezone, ZoneInfo)
    assert str(result.timezone) == "America/New_York"


def test_serializer_hook_timezone_unstructure() -> None:
    """Test that SERIALIZER correctly converts ZoneInfo objects to strings."""
    config = Config(timezone=ZoneInfo("Europe/Paris"))

    result = SERIALIZER.unstructure(config)

    assert "timezone" in result
    assert result["timezone"] == "Europe/Paris"
    assert isinstance(result["timezone"], str)


def test_serializer_hook_timezone_round_trip() -> None:
    """Test that ZoneInfo objects survive a round-trip through serialization."""

    # Test with various timezone strings
    timezones = [
        "UTC",
        "America/New_York",
        "Europe/Paris",
        "Asia/Tokyo",
        "Australia/Sydney",
    ]

    for tz_str in timezones:
        # Create config with timezone
        original_tz = ZoneInfo(tz_str)
        original_config = Config(timezone=original_tz)

        # Serialize to JSON
        config_dict = SERIALIZER.unstructure(original_config)
        json_str = json.dumps(config_dict)

        # Deserialize from JSON
        json_loaded = json.loads(json_str)
        loaded_config = SERIALIZER.structure(json_loaded, Config)

        # Verify timezone was preserved correctly
        assert isinstance(loaded_config.timezone, ZoneInfo)
        assert str(loaded_config.timezone) == tz_str


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


def test_config_serialization_round_trip() -> None:
    """Test complete serialization cycle for Config objects."""

    # Create a config with various data
    original_printer_config = PrinterConfig(
        access_code="test_code",
        filename_prefix="prefix1",
        ip_address="192.168.1.100",
    )

    original_slack_config = SlackConfig(
        access_token="xoxb-test",  # noqa: S106 - Test token, not real
        print_notification_channel="prints",
        error_notification_channel="errors",
    )

    original_config = Config(
        printers={"printer1": original_printer_config},
        slack=original_slack_config,
        timezone=ZoneInfo("Europe/London"),
    )

    # Step 1: Unstructure to dictionary using the serializer
    config_dict = SERIALIZER.unstructure(original_config)

    # Step 2: Convert to JSON
    json_str = json.dumps(config_dict)

    # Step 3: Convert back from JSON
    json_loaded = json.loads(json_str)

    # Step 4: Structure back to object using the serializer
    restored_config = SERIALIZER.structure(json_loaded, Config)

    # Verify the round trip worked correctly
    assert len(restored_config.printers) == 1
    assert "printer1" in restored_config.printers
    assert restored_config.printers["printer1"].access_code == "test_code"
    assert restored_config.printers["printer1"].filename_prefix == "prefix1"
    assert restored_config.printers["printer1"].ip_address == "192.168.1.100"

    assert restored_config.slack.access_token == "xoxb-test"  # noqa: S105 - Test token, not real
    assert restored_config.slack.print_notification_channel == "prints"
    assert restored_config.slack.error_notification_channel == "errors"

    assert isinstance(restored_config.timezone, ZoneInfo)
    assert str(restored_config.timezone) == "Europe/London"

    # Check that the JSON contains the expected fields
    assert "printers" in json_loaded
    assert "printer1" in json_loaded["printers"]
    assert json_loaded["printers"]["printer1"]["access_code"] == "test_code"
    assert "slack" in json_loaded
    assert json_loaded["slack"]["access_token"] == "xoxb-test"  # noqa: S105 - Test token, not real
    assert "timezone" in json_loaded
    assert json_loaded["timezone"] == "Europe/London"


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


def test_config_file_persistence() -> None:
    """Test saving and loading Config to/from a real file."""
    import tempfile
    from pathlib import Path

    # Create a test config
    printer_config = PrinterConfig(
        access_code="test_code",
        filename_prefix="test_prefix",
        ip_address="192.168.1.200",
    )

    slack_config = SlackConfig(
        access_token="xoxb-testtoken",  # noqa: S106 - Test token, not real
        print_notification_channel="test-prints",
        error_notification_channel="test-errors",
    )

    original_config = Config(
        printers={"test_printer": printer_config},
        slack=slack_config,
        timezone=ZoneInfo("America/Chicago"),
    )

    # Create a temporary file for testing
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

        try:
            # Serialize and save to file
            config_dict = SERIALIZER.unstructure(original_config)
            json_str = json.dumps(config_dict, indent=2)
            temp_file.write(json_str.encode('utf-8'))
            temp_file.flush()

            # Now load it back using the same code path as souzu.config
            with temp_path.open('r') as f:
                loaded_dict = json.load(f)
                loaded_config = SERIALIZER.structure(loaded_dict, Config)

            # Verify all fields were preserved
            assert len(loaded_config.printers) == 1
            assert "test_printer" in loaded_config.printers
            assert loaded_config.printers["test_printer"].access_code == "test_code"
            assert (
                loaded_config.printers["test_printer"].filename_prefix == "test_prefix"
            )
            assert loaded_config.printers["test_printer"].ip_address == "192.168.1.200"

            assert loaded_config.slack.access_token == "xoxb-testtoken"  # noqa: S105 - Test token, not real
            assert loaded_config.slack.print_notification_channel == "test-prints"
            assert loaded_config.slack.error_notification_channel == "test-errors"

            assert isinstance(loaded_config.timezone, ZoneInfo)
            assert str(loaded_config.timezone) == "America/Chicago"
        finally:
            # Clean up the temporary file
            temp_path.unlink()


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
