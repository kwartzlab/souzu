from pathlib import Path

from xdg_base_dirs import xdg_config_home

_user_service_dir = Path(xdg_config_home()) / "systemd/user"

MONITOR_SERVICE_PATH = _user_service_dir / "souzu.service"
UPDATE_SERVICE_PATH = _user_service_dir / "souzu-update.service"
UPDATE_TIMER_PATH = _user_service_dir / "souzu-update.timer"
