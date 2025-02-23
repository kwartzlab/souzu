import json
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from attrs import frozen
from cattrs import Converter
from xdg_base_dirs import xdg_config_home

_CONFIG_FILE = xdg_config_home() / "souzu.json"


def _convert_timezone(tz_str: str, _: type[ZoneInfo]) -> ZoneInfo:
    """Convert timezone string to ZoneInfo, falling back to UTC on invalid input."""
    try:
        return ZoneInfo(tz_str)
    except ZoneInfoNotFoundError:
        logging.warning(f"Invalid timezone {tz_str}, falling back to UTC")
        return ZoneInfo('UTC')


SERIALIZER = Converter()
SERIALIZER.register_structure_hook(ZoneInfo, _convert_timezone)


@frozen
class PrinterConfig:
    access_code: str
    filename_prefix: str | None = None
    ip_address: str | None = None


@frozen
class SlackConfig:
    access_token: str | None = None
    print_notification_channel: str | None = None
    error_notification_channel: str | None = None


@frozen
class Config:
    printers: dict[str, PrinterConfig] = {}
    slack: SlackConfig = SlackConfig()
    timezone: ZoneInfo = ZoneInfo("UTC")


CONFIG = Config()

if _CONFIG_FILE.exists():
    with _CONFIG_FILE.open('r') as f:
        config_dict = json.load(f)
        CONFIG = SERIALIZER.structure(config_dict, Config)
