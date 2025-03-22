from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from souzu.commands.update import find_uv, get_souzu_version, update


def test_find_uv(mocker: MockerFixture) -> None:
    """Test find_uv function."""
    # Mock is_file and access
    mock_is_file = mocker.patch.object(Path, "is_file", return_value=True)
    mock_access = mocker.patch("os.access", return_value=True)

    # Mock PATH environment variable
    mock_env = mocker.patch("os.environ.get", return_value="/usr/bin:/usr/local/bin")

    # Call the function
    result = find_uv()

    # Assert expected results
    assert result == Path("/usr/bin/uv")
    mock_env.assert_called_once_with("PATH", "")
    mock_is_file.assert_called()
    mock_access.assert_called()


def test_find_uv_exists(mocker: MockerFixture) -> None:
    """Test find_uv returns a Path when uv is found."""
    # Testing real behavior rather than mocked paths to verify actual file finding logic
    result = find_uv()

    # Verify it returned a Path
    assert isinstance(result, Path)

    # Verify it ends with 'uv'
    assert str(result).endswith('uv')


def test_find_uv_not_found(mocker: MockerFixture) -> None:
    """Test find_uv raises FileNotFoundError when uv isn't found."""
    # Mock is_file to always return False
    mocker.patch.object(Path, "is_file", return_value=False)

    # Mock PATH environment variable
    mocker.patch("os.environ.get", return_value="/usr/bin:/usr/local/bin")

    # Call the function and expect exception
    with pytest.raises(FileNotFoundError, match="uv executable not found"):
        find_uv()


def test_get_souzu_version(mocker: MockerFixture) -> None:
    """Test get_souzu_version function."""
    # Mock find_souzu
    mock_find_souzu = mocker.patch(
        "souzu.commands.update.find_souzu", return_value=Path("/usr/bin/souzu")
    )

    # Mock subprocess.run
    mock_process = MagicMock()
    mock_process.stdout = b"souzu 1.0.0\n"
    mock_run = mocker.patch(
        "souzu.commands.update.subprocess.run", return_value=mock_process
    )

    # Call the function
    result = get_souzu_version()

    # Assert expected results
    assert result == "souzu 1.0.0"
    mock_find_souzu.assert_called_once()
    mock_run.assert_called_once_with(
        [Path("/usr/bin/souzu"), "--version"], capture_output=True, check=True
    )


def test_update_with_new_version(mocker: MockerFixture) -> None:
    """Test update function when a new version is installed."""
    # Mock dependencies
    mock_find_uv = mocker.patch(
        "souzu.commands.update.find_uv", return_value=Path("/usr/bin/uv")
    )

    # Mock get_souzu_version to return different values on successive calls
    mock_get_version = mocker.patch(
        "souzu.commands.update.get_souzu_version",
        side_effect=["souzu 1.0.0", "souzu 1.1.0"],
    )

    # Mock subprocess.run
    mock_run = mocker.patch("souzu.commands.update.subprocess.run")

    # Mock print
    mock_print = mocker.patch("builtins.print")

    # Call the function
    update(restart=False)

    # Verify behavior
    mock_find_uv.assert_called_once()
    assert mock_get_version.call_count == 2
    mock_run.assert_called_once_with([Path("/usr/bin/uv"), "tool", "update", "souzu"])
    mock_print.assert_called_once_with("Updated from souzu 1.0.0 to souzu 1.1.0")


def test_update_with_restart(mocker: MockerFixture) -> None:
    """Test update function with restart=True."""
    # Mock dependencies
    mocker.patch("souzu.commands.update.find_uv", return_value=Path("/usr/bin/uv"))

    # Mock get_souzu_version to return different values on successive calls
    mocker.patch(
        "souzu.commands.update.get_souzu_version",
        side_effect=["souzu 1.0.0", "souzu 1.1.0"],
    )

    # Mock subprocess.run
    mock_run = mocker.patch("souzu.commands.update.subprocess.run")

    # Call the function
    update(restart=True)

    # Verify behavior
    assert mock_run.call_count == 2
    mock_run.assert_any_call([Path("/usr/bin/uv"), "tool", "update", "souzu"])
    mock_run.assert_any_call(
        ['/usr/bin/systemctl', '--user', 'restart', 'souzu.service']
    )


def test_update_no_changes(mocker: MockerFixture) -> None:
    """Test update function when no update is available."""
    # Mock dependencies
    mocker.patch("souzu.commands.update.find_uv", return_value=Path("/usr/bin/uv"))

    # Mock get_souzu_version to return the same value twice
    mocker.patch(
        "souzu.commands.update.get_souzu_version",
        side_effect=["souzu 1.1.0", "souzu 1.1.0"],
    )

    # Mock subprocess.run
    mock_run = mocker.patch("souzu.commands.update.subprocess.run")

    # Mock print
    mock_print = mocker.patch("builtins.print")

    # Call the function
    update(restart=True)

    # Verify behavior
    mock_run.assert_called_once_with([Path("/usr/bin/uv"), "tool", "update", "souzu"])
    mock_print.assert_called_once_with("Already up to date: souzu 1.1.0")
