import json

from attrs import frozen
from cattrs import structure
from xdg_base_dirs import xdg_config_home

_CONFIG_FILE = xdg_config_home() / "souzu.json"


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


CONFIG = Config()

if _CONFIG_FILE.exists():
    with _CONFIG_FILE.open('r') as f:
        config_dict = json.load(f)
        CONFIG = structure(config_dict, Config)
