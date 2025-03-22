from pathlib import Path
from unittest.mock import MagicMock, mock_open

from pytest_mock import MockerFixture

from souzu.commands.install import _install_template, install
from souzu.systemd import (
    MONITOR_SERVICE_PATH,
    UPDATE_SERVICE_PATH,
    UPDATE_TIMER_PATH,
)


class TestInstallTemplate:
    def test_install_template(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Test installing a template file with formatting."""
        # Setup
        mock_template = "This is a {key1} template with {key2}"
        mock_formatted = "This is a value1 template with value2"

        # Create a real path to use
        test_dir = tmp_path / "testdir"
        test_output_path = test_dir / "output.txt"

        # Mock Path.mkdir to avoid actually creating directories
        mkdir_mock = mocker.patch.object(Path, "mkdir")

        # Create mocks for file operations
        mock_template_file = mock_open(read_data=mock_template)()
        mock_output_file = mock_open()()

        # Mock Path.open to return our mock file
        mock_path_open = mocker.patch.object(Path, "open")
        mock_path_open.return_value.__enter__.return_value = mock_output_file

        # Mock the resources.path context manager
        mock_template_path = MagicMock(spec=Path)
        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_template_path
        mocker.patch("souzu.commands.install.resources.path", return_value=mock_context)

        # Mock builtins.open only for the template file
        mock_builtins_open = mocker.patch("builtins.open")
        mock_builtins_open.return_value.__enter__.return_value = mock_template_file

        # Call function under test
        _install_template(
            "test.template", test_output_path, key1="value1", key2="value2"
        )

        # Assertions - parent directory is created
        mkdir_mock.assert_called_once_with(parents=True, exist_ok=True)

        # Template file was read
        mock_builtins_open.assert_called_once_with(mock_template_path)
        mock_template_file.read.assert_called_once()

        # Output file was written with formatted content
        mock_path_open.assert_called_once_with("w")
        mock_output_file.write.assert_called_once_with(mock_formatted)


class TestInstall:
    def test_install(self, mocker: MockerFixture) -> None:
        """Test the install function."""
        mock_find_souzu = mocker.patch(
            "souzu.commands.install.find_souzu", return_value=Path("/usr/bin/souzu")
        )
        mock_install_template = mocker.patch("souzu.commands.install._install_template")
        mock_print = mocker.patch("souzu.commands.install.print")

        install()

        mock_find_souzu.assert_called_once()

        # Check all three template installations
        assert mock_install_template.call_count == 3

        # Check monitor service installation
        mock_install_template.assert_any_call(
            "souzu.service.template",
            MONITOR_SERVICE_PATH,
            souzu_path='"/usr/bin/souzu"',
        )

        # Check update service installation
        mock_install_template.assert_any_call(
            "souzu-update.service.template",
            UPDATE_SERVICE_PATH,
            souzu_path='"/usr/bin/souzu"',
        )

        # Check update timer installation
        mock_install_template.assert_any_call("souzu-update.timer", UPDATE_TIMER_PATH)

        # Check that help instructions were printed
        mock_print.assert_called_once()
        printed_text = mock_print.call_args[0][0]
        assert "Installed systemd user services" in printed_text
        assert "systemctl --user enable souzu.service" in printed_text
        assert "systemctl --user enable --now souzu-update.timer" in printed_text

    def test_install_with_path_containing_quotes(self, mocker: MockerFixture) -> None:
        """Test the install function with a path containing quotes."""
        mock_find_souzu = mocker.patch(
            "souzu.commands.install.find_souzu",
            return_value=Path('/usr/bin/souzu"with"quotes'),
        )
        mock_install_template = mocker.patch("souzu.commands.install._install_template")
        mocker.patch("souzu.commands.install.print")

        install()

        mock_find_souzu.assert_called_once()

        # Check that quotes in the path are properly escaped
        expected_path = '"/usr/bin/souzu\\"with\\"quotes"'

        # Check monitor service installation with escaped quotes
        mock_install_template.assert_any_call(
            "souzu.service.template", MONITOR_SERVICE_PATH, souzu_path=expected_path
        )

        # Check update service installation with escaped quotes
        mock_install_template.assert_any_call(
            "souzu-update.service.template",
            UPDATE_SERVICE_PATH,
            souzu_path=expected_path,
        )
