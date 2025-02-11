import json

from xdg_base_dirs import xdg_config_home

BAMBU_ACCESS_CODES: dict[str, str] = {}

_CONFIG_FILE = xdg_config_home() / "souzu.json"

if _CONFIG_FILE.exists():
    with _CONFIG_FILE.open('r') as f:
        config = json.load(f)
        BAMBU_ACCESS_CODES = config.get("bambu_access_codes")
