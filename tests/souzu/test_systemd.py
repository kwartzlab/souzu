import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from souzu.systemd import (
    MONITOR_SERVICE_PATH,
    UPDATE_SERVICE_PATH,
    UPDATE_TIMER_PATH,
    _user_service_dir,
)


def test_systemd_paths() -> None:
    """Test that systemd paths are correctly set."""
    assert isinstance(MONITOR_SERVICE_PATH, Path)
    assert isinstance(UPDATE_SERVICE_PATH, Path)
    assert isinstance(UPDATE_TIMER_PATH, Path)

    assert MONITOR_SERVICE_PATH.name == "souzu.service"
    assert UPDATE_SERVICE_PATH.name == "souzu-update.service"
    assert UPDATE_TIMER_PATH.name == "souzu-update.timer"

    assert MONITOR_SERVICE_PATH.parent == _user_service_dir
    assert UPDATE_SERVICE_PATH.parent == _user_service_dir
    assert UPDATE_TIMER_PATH.parent == _user_service_dir


@patch("xdg_base_dirs.xdg_config_home")
def test_user_service_dir(mock_xdg_config_home: MagicMock) -> None:
    """Test that _user_service_dir is correctly set based on XDG config home."""
    mock_config_home = "/mock/xdg/config"
    mock_xdg_config_home.return_value = mock_config_home

    import souzu.systemd

    importlib.reload(souzu.systemd)

    expected_service_dir = Path(mock_config_home) / "systemd/user"
    assert souzu.systemd._user_service_dir == expected_service_dir

    assert souzu.systemd.MONITOR_SERVICE_PATH == expected_service_dir / "souzu.service"
    assert (
        souzu.systemd.UPDATE_SERVICE_PATH
        == expected_service_dir / "souzu-update.service"
    )
    assert (
        souzu.systemd.UPDATE_TIMER_PATH == expected_service_dir / "souzu-update.timer"
    )
