import argparse
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pytest_mock import MockerFixture

from souzu.cli.main import _parse_args, main


@pytest.fixture
def mock_args() -> argparse.Namespace:
    """Create a mock arguments namespace."""
    args = argparse.Namespace()
    args.verbose = False
    args.command = "monitor"
    return args


class TestParseArgs:
    def test_parse_args_with_version(self, mocker: MockerFixture) -> None:
        """Test version retrieval in argument parsing."""
        mock_version = mocker.patch("souzu.cli.main.version", return_value="1.0.0")

        with patch("sys.argv", ["souzu", "--version"]):
            with pytest.raises(SystemExit):
                _parse_args()

        mock_version.assert_called_once_with("souzu")

    def test_parse_args_package_not_found(self, mocker: MockerFixture) -> None:
        """Test version error handling in argument parsing."""
        mock_version = mocker.patch(
            "souzu.cli.main.version", side_effect=PackageNotFoundError()
        )

        with patch("sys.argv", ["souzu", "monitor"]):
            args = _parse_args()

        mock_version.assert_called_once_with("souzu")
        assert args.command == "monitor"
        assert not args.verbose

    def test_parse_args_verbose(self) -> None:
        """Test verbose flag parsing."""
        with patch("sys.argv", ["souzu", "-v", "monitor"]):
            args = _parse_args()

        assert args.verbose
        assert args.command == "monitor"

    def test_parse_args_update_with_restart(self) -> None:
        """Test update command with restart flag."""
        with patch("sys.argv", ["souzu", "update", "--restart"]):
            args = _parse_args()

        assert args.command == "update"
        assert args.restart

    def test_parse_args_install(self) -> None:
        """Test install command parsing."""
        with patch("sys.argv", ["souzu", "install"]):
            args = _parse_args()

        assert args.command == "install"

    def test_parse_args_compact(self, tmp_path: Path) -> None:
        """Test compact command parsing with input file."""
        input_file = tmp_path / "test.log"

        with patch("sys.argv", ["souzu", "compact", str(input_file)]):
            args = _parse_args()

        assert args.command == "compact"
        assert args.input_file == input_file
        assert args.output is None

    def test_parse_args_compact_with_output(self, tmp_path: Path) -> None:
        """Test compact command parsing with input and output files."""
        input_file = tmp_path / "test.log"
        output_file = tmp_path / "output.log"

        with patch(
            "sys.argv", ["souzu", "compact", str(input_file), "-o", str(output_file)]
        ):
            args = _parse_args()

        assert args.command == "compact"
        assert args.input_file == input_file
        assert args.output == output_file

    def test_parse_args_no_command(self) -> None:
        """Test error when no command is provided."""
        with patch("sys.argv", ["souzu"]):
            with pytest.raises(SystemExit):
                _parse_args()


class TestMain:
    def test_main_monitor(self, mocker: MockerFixture) -> None:
        """Test main function with monitor command."""
        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(command="monitor", verbose=False),
        )
        mock_run = mocker.patch("souzu.cli.main.run")
        mock_monitor = mocker.patch("souzu.cli.main.monitor", return_value=AsyncMock())
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once_with(frozenset({'attrs'}))
        mock_logging.assert_called_once_with(level=mocker.ANY)
        mock_monitor.assert_called_once()
        mock_run.assert_called_once()

    def test_main_update(self, mocker: MockerFixture) -> None:
        """Test main function with update command."""
        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(
                command="update", verbose=False, restart=True
            ),
        )
        mock_update = mocker.patch("souzu.cli.main.update")
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once()
        mock_update.assert_called_once_with(True)

    def test_main_update_error(self, mocker: MockerFixture) -> None:
        """Test main function with update command raising an error."""
        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(
                command="update", verbose=False, restart=False
            ),
        )
        mock_update = mocker.patch(
            "souzu.cli.main.update", side_effect=ValueError("Update error")
        )
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")
        mock_print = mocker.patch("souzu.cli.main.print")
        mock_exit = mocker.patch("souzu.cli.main.exit")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once()
        mock_update.assert_called_once_with(False)
        mock_print.assert_called_once_with(
            "Error updating: Update error", file=sys.stderr
        )
        mock_exit.assert_called_once_with(1)

    def test_main_install(self, mocker: MockerFixture) -> None:
        """Test main function with install command."""
        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(command="install", verbose=False),
        )
        mock_install = mocker.patch("souzu.cli.main.install")
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once()
        mock_install.assert_called_once()

    def test_main_install_error(self, mocker: MockerFixture) -> None:
        """Test main function with install command raising an error."""
        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(command="install", verbose=False),
        )
        mock_install = mocker.patch(
            "souzu.cli.main.install", side_effect=ValueError("Install error")
        )
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")
        mock_print = mocker.patch("souzu.cli.main.print")
        mock_exit = mocker.patch("souzu.cli.main.exit")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once()
        mock_install.assert_called_once()
        mock_print.assert_called_once_with(
            "Error installing: Install error", file=sys.stderr
        )
        mock_exit.assert_called_once_with(1)

    def test_main_compact(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Test main function with compact command."""
        input_file = tmp_path / "test.log"
        output_file = tmp_path / "output.log"

        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(
                command="compact",
                verbose=False,
                input_file=input_file,
                output=output_file,
            ),
        )
        mock_run = mocker.patch("souzu.cli.main.run")
        mock_compact = mocker.patch("souzu.cli.main.compact", return_value=AsyncMock())
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once()
        mock_compact.assert_called_once_with(input_file, output_file)
        mock_run.assert_called_once()

    def test_main_compact_error(self, mocker: MockerFixture, tmp_path: Path) -> None:
        """Test main function with compact command raising an error."""
        input_file = tmp_path / "test.log"

        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(
                command="compact", verbose=False, input_file=input_file, output=None
            ),
        )
        mock_run = mocker.patch(
            "souzu.cli.main.run", side_effect=ValueError("Compact error")
        )
        mock_compact = mocker.patch("souzu.cli.main.compact", return_value=AsyncMock())
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")
        mock_print = mocker.patch("souzu.cli.main.print")
        mock_exit = mocker.patch("souzu.cli.main.exit")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once()
        mock_compact.assert_called_once_with(input_file, None)
        mock_run.assert_called_once()
        mock_print.assert_called_once_with(
            "Error compacting log file: Compact error", file=sys.stderr
        )
        mock_exit.assert_called_once_with(1)

    def test_main_unknown_command(self, mocker: MockerFixture) -> None:
        """Test main function with unknown command."""
        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(command="unknown", verbose=False),
        )
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")

        with pytest.raises(NotImplementedError, match="Unknown command unknown"):
            main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once()

    def test_main_verbose(self, mocker: MockerFixture) -> None:
        """Test main function with verbose logging."""
        mock_parse_args = mocker.patch(
            "souzu.cli.main._parse_args",
            return_value=argparse.Namespace(command="monitor", verbose=True),
        )
        mock_run = mocker.patch("souzu.cli.main.run")
        mock_monitor = mocker.patch("souzu.cli.main.monitor", return_value=AsyncMock())
        mock_install_extras = mocker.patch("souzu.cli.main.install_extras")
        mock_logging = mocker.patch("souzu.cli.main.logging.basicConfig")

        main()

        mock_parse_args.assert_called_once()
        mock_install_extras.assert_called_once()
        mock_logging.assert_called_once_with(level=mocker.ANY)
        mock_monitor.assert_called_once()
        mock_run.assert_called_once()
