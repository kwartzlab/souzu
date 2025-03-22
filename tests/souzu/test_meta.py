from pathlib import Path
from unittest.mock import patch

from souzu.meta import find_souzu


def test_find_souzu() -> None:
    """Test that find_souzu returns the correct path based on sys.argv[0]."""
    mock_path = "/path/to/souzu"

    with patch("sys.argv", [mock_path]):
        with patch("os.path.abspath", return_value=mock_path):
            result = find_souzu()

            assert result == Path(mock_path)
            assert isinstance(result, Path)


def test_find_souzu_relative_path() -> None:
    """Test that find_souzu handles relative paths correctly."""
    mock_relative_path = "souzu"
    mock_absolute_path = "/absolute/path/to/souzu"

    with patch("sys.argv", [mock_relative_path]):
        with patch("os.path.abspath", return_value=mock_absolute_path):
            result = find_souzu()

            assert result == Path(mock_absolute_path)
            assert isinstance(result, Path)
