from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from souzu.commands.update import (
    clone_export_upgrade,
    find_uv,
    get_installed_commit_hash,
    get_latest_commit_hash,
    get_souzu_version,
    is_update_available,
    update,
)


def test_find_uv(mocker: MockerFixture) -> None:
    """Test find_uv function."""
    # Mock is_file and access
    mock_is_file = mocker.patch.object(Path, 'is_file', return_value=True)
    mock_access = mocker.patch('os.access', return_value=True)

    # Mock PATH environment variable
    mock_env = mocker.patch('os.environ.get', return_value='/usr/bin:/usr/local/bin')

    # Call the function
    result = find_uv()

    # Assert expected results
    assert result == Path('/usr/bin/uv')
    mock_env.assert_called_once_with('PATH', '')
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
    mocker.patch.object(Path, 'is_file', return_value=False)

    # Mock PATH environment variable
    mocker.patch('os.environ.get', return_value='/usr/bin:/usr/local/bin')

    # Call the function and expect exception
    with pytest.raises(FileNotFoundError, match="uv executable not found"):
        find_uv()


def test_get_souzu_version(mocker: MockerFixture) -> None:
    """Test get_souzu_version function."""
    # Mock find_souzu
    mock_find_souzu = mocker.patch(
        'souzu.commands.update.find_souzu', return_value=Path('/usr/bin/souzu')
    )

    # Mock subprocess.run
    mock_process = MagicMock()
    mock_process.stdout = b"souzu 1.0.0\n"
    mock_run = mocker.patch(
        'souzu.commands.update.subprocess.run', return_value=mock_process
    )

    # Call the function
    result = get_souzu_version()

    # Assert expected results
    assert result == "souzu 1.0.0"
    mock_find_souzu.assert_called_once()
    mock_run.assert_called_once_with(
        [Path('/usr/bin/souzu'), '--version'], capture_output=True, check=True
    )


class TestGetInstalledCommitHash:
    """Tests for get_installed_commit_hash."""

    def test_extracts_commit_hash(self, mocker: MockerFixture) -> None:
        """Test extraction of commit hash from dev version."""
        mocker.patch(
            'souzu.commands.update.get_souzu_version',
            return_value="souzu 0.1.dev73+g3296c1d9b",
        )
        result = get_installed_commit_hash()
        assert result == '3296c1d9b'

    def test_returns_none_for_release_version(self, mocker: MockerFixture) -> None:
        """Test returns None for release versions without commit hash."""
        mocker.patch(
            'souzu.commands.update.get_souzu_version',
            return_value="souzu 1.0.0",
        )
        result = get_installed_commit_hash()
        assert result is None


class TestGetLatestCommitHash:
    """Tests for get_latest_commit_hash."""

    def test_fetches_commit_hash(self, mocker: MockerFixture) -> None:
        """Test successful API fetch."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'sha': 'abc123def456'}
        mocker.patch('souzu.commands.update.requests.get', return_value=mock_response)

        result = get_latest_commit_hash()
        assert result == 'abc123def456'

    def test_returns_none_on_request_error(self, mocker: MockerFixture) -> None:
        """Test returns None when API request fails."""
        import requests

        mocker.patch(
            'souzu.commands.update.requests.get',
            side_effect=requests.RequestException("Network error"),
        )

        result = get_latest_commit_hash()
        assert result is None

    def test_returns_none_on_http_error(self, mocker: MockerFixture) -> None:
        """Test returns None when API returns error status."""
        import requests

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mocker.patch('souzu.commands.update.requests.get', return_value=mock_response)

        result = get_latest_commit_hash()
        assert result is None


class TestIsUpdateAvailable:
    """Tests for is_update_available."""

    def test_returns_true_when_commits_differ(self, mocker: MockerFixture) -> None:
        """Test returns True when installed and latest commits differ."""
        mocker.patch(
            'souzu.commands.update.get_installed_commit_hash', return_value='abc123'
        )
        mocker.patch(
            'souzu.commands.update.get_latest_commit_hash',
            return_value='def456789012345678901234567890123456789',
        )

        assert is_update_available() is True

    def test_returns_false_when_commits_match(self, mocker: MockerFixture) -> None:
        """Test returns False when installed hash is prefix of latest."""
        mocker.patch(
            'souzu.commands.update.get_installed_commit_hash', return_value='abc123'
        )
        mocker.patch(
            'souzu.commands.update.get_latest_commit_hash',
            return_value='abc123def456789012345678901234567890123',
        )

        assert is_update_available() is False

    def test_returns_true_when_installed_unknown(self, mocker: MockerFixture) -> None:
        """Test returns True when installed commit can't be determined."""
        mocker.patch(
            'souzu.commands.update.get_installed_commit_hash', return_value=None
        )
        mocker.patch(
            'souzu.commands.update.get_latest_commit_hash', return_value='abc123'
        )

        assert is_update_available() is True

    def test_returns_true_when_latest_unknown(self, mocker: MockerFixture) -> None:
        """Test returns True when latest commit can't be fetched."""
        mocker.patch(
            'souzu.commands.update.get_installed_commit_hash', return_value='abc123'
        )
        mocker.patch('souzu.commands.update.get_latest_commit_hash', return_value=None)

        assert is_update_available() is True


class TestCloneExportUpgrade:
    """Tests for clone_export_upgrade."""

    @pytest.fixture(autouse=True)
    def mock_find_uv(self, mocker: MockerFixture) -> None:
        mocker.patch('souzu.commands.update.find_uv', return_value=Path('/usr/bin/uv'))

    def test_successful_upgrade(self, mocker: MockerFixture) -> None:
        """Test successful clone, export, and upgrade flow."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mocker.patch('souzu.commands.update.subprocess.run', return_value=mock_result)

        result = clone_export_upgrade()
        assert result is True

    def test_clone_failure(self, mocker: MockerFixture) -> None:
        """Test returns False when git clone fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"Clone failed"
        mocker.patch('souzu.commands.update.subprocess.run', return_value=mock_result)

        result = clone_export_upgrade()
        assert result is False

    def test_export_failure(self, mocker: MockerFixture) -> None:
        """Test returns False when uv export fails."""
        mock_clone_result = MagicMock()
        mock_clone_result.returncode = 0

        mock_export_result = MagicMock()
        mock_export_result.returncode = 1
        mock_export_result.stderr = b"Export failed"

        mocker.patch(
            'souzu.commands.update.subprocess.run',
            side_effect=[mock_clone_result, mock_export_result],
        )

        result = clone_export_upgrade()
        assert result is False

    def test_upgrade_failure(self, mocker: MockerFixture) -> None:
        """Test returns False when uv tool upgrade fails."""
        mock_success = MagicMock()
        mock_success.returncode = 0

        mock_upgrade_result = MagicMock()
        mock_upgrade_result.returncode = 1
        mock_upgrade_result.stderr = b"Upgrade failed"

        mocker.patch(
            'souzu.commands.update.subprocess.run',
            side_effect=[mock_success, mock_success, mock_upgrade_result],
        )

        result = clone_export_upgrade()
        assert result is False


class TestUpdate:
    """Tests for the main update function."""

    def test_skips_when_up_to_date(self, mocker: MockerFixture) -> None:
        """Test prints 'up to date' and skips clone when no update available."""
        mocker.patch(
            'souzu.commands.update.get_souzu_version',
            return_value="souzu 0.1.dev73+g3296c1d9b",
        )
        mocker.patch('souzu.commands.update.is_update_available', return_value=False)
        mock_print = mocker.patch('builtins.print')
        mock_clone = mocker.patch('souzu.commands.update.clone_export_upgrade')

        update(restart=False)

        mock_print.assert_called_once_with(
            "Already up to date: souzu 0.1.dev73+g3296c1d9b"
        )
        mock_clone.assert_not_called()

    def test_update_with_new_version(self, mocker: MockerFixture) -> None:
        """Test update function when a new version is installed."""
        mocker.patch(
            'souzu.commands.update.get_souzu_version',
            side_effect=["souzu 1.0.0", "souzu 1.1.0"],
        )
        mocker.patch('souzu.commands.update.is_update_available', return_value=True)
        mocker.patch('souzu.commands.update.clone_export_upgrade', return_value=True)
        mock_print = mocker.patch('builtins.print')

        update(restart=False)

        mock_print.assert_called_once_with("Updated from souzu 1.0.0 to souzu 1.1.0")

    def test_update_with_restart(self, mocker: MockerFixture) -> None:
        """Test update function with restart=True."""
        mocker.patch(
            'souzu.commands.update.get_souzu_version',
            side_effect=["souzu 1.0.0", "souzu 1.1.0"],
        )
        mocker.patch('souzu.commands.update.is_update_available', return_value=True)
        mocker.patch('souzu.commands.update.clone_export_upgrade', return_value=True)
        mock_run = mocker.patch('souzu.commands.update.subprocess.run')
        mocker.patch('builtins.print')

        update(restart=True)

        mock_run.assert_called_once_with(
            ['/usr/bin/systemctl', '--user', 'restart', 'souzu.service']
        )

    def test_update_no_restart_when_version_unchanged(
        self, mocker: MockerFixture
    ) -> None:
        """Test no restart when version stays the same after upgrade attempt."""
        mocker.patch(
            'souzu.commands.update.get_souzu_version',
            side_effect=["souzu 1.1.0", "souzu 1.1.0"],
        )
        mocker.patch('souzu.commands.update.is_update_available', return_value=True)
        mocker.patch('souzu.commands.update.clone_export_upgrade', return_value=True)
        mock_run = mocker.patch('souzu.commands.update.subprocess.run')
        mock_print = mocker.patch('builtins.print')

        update(restart=True)

        mock_run.assert_not_called()
        mock_print.assert_called_once_with("Already up to date: souzu 1.1.0")

    def test_update_failure(self, mocker: MockerFixture) -> None:
        """Test update prints failure message when clone_export_upgrade fails."""
        mocker.patch(
            'souzu.commands.update.get_souzu_version', return_value="souzu 1.0.0"
        )
        mocker.patch('souzu.commands.update.is_update_available', return_value=True)
        mocker.patch('souzu.commands.update.clone_export_upgrade', return_value=False)
        mock_print = mocker.patch('builtins.print')

        update(restart=False)

        mock_print.assert_called_once_with("Update failed, see logs for details")
