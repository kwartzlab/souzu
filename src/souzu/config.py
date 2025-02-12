import json

from xdg_base_dirs import xdg_config_home

BAMBU_ACCESS_CODES: dict[str, str] = {}
SLACK_ACCESS_TOKEN: str | None = None
SLACK_PRINT_NOTIFICATION_CHANNEL: str | None = None
SLACK_ERROR_NOTIFICATION_CHANNEL: str | None = None

_CONFIG_FILE = xdg_config_home() / "souzu.json"

if _CONFIG_FILE.exists():
    with _CONFIG_FILE.open('r') as f:
        config = json.load(f)
        BAMBU_ACCESS_CODES = config.get("bambu_access_codes")
        SLACK_ACCESS_TOKEN = config.get("slack", {}).get("access_token")
        SLACK_PRINT_NOTIFICATION_CHANNEL = config.get("slack", {}).get(
            "print_notification_channel"
        )
        SLACK_ERROR_NOTIFICATION_CHANNEL = config.get("slack", {}).get(
            "error_notification_channel"
        )
