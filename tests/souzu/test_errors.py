from souzu.bambu.errors import (
    CANCELLED_ERROR_CODES,
    ERROR_CODES,
    FILAMENT_RUNOUT_ERROR_CODES,
    parse_error_code,
)


def test_error_codes_existence() -> None:
    """Test that ERROR_CODES dictionary contains expected error codes."""
    assert len(ERROR_CODES) > 0

    assert 0x0300800A in ERROR_CODES  # Filament pile-up
    assert 0x03008004 in ERROR_CODES  # Filament ran out
    assert 0x03008001 in ERROR_CODES  # Printing was paused by the user

    for code in ERROR_CODES:
        assert isinstance(code, int)

    for message in ERROR_CODES.values():
        assert isinstance(message, str)


def test_cancelled_error_codes() -> None:
    """Test that CANCELLED_ERROR_CODES set contains expected error codes."""
    assert len(CANCELLED_ERROR_CODES) > 0

    assert 0x0300400C in CANCELLED_ERROR_CODES
    assert 0x0500400E in CANCELLED_ERROR_CODES

    for code in CANCELLED_ERROR_CODES:
        assert code in ERROR_CODES


def test_filament_runout_error_codes() -> None:
    """Test that FILAMENT_RUNOUT_ERROR_CODES set contains expected error codes."""
    assert len(FILAMENT_RUNOUT_ERROR_CODES) > 0

    assert 0x03008004 in FILAMENT_RUNOUT_ERROR_CODES
    assert 0x03008015 in FILAMENT_RUNOUT_ERROR_CODES

    for code in FILAMENT_RUNOUT_ERROR_CODES:
        assert code in ERROR_CODES


def test_parse_error_code_with_known_code() -> None:
    """Test parsing known error codes returns the correct message."""
    error_code = 0x0300800A  # Filament pile-up
    expected_message = ERROR_CODES[error_code]

    assert parse_error_code(error_code) == expected_message


def test_parse_error_code_with_unknown_code() -> None:
    """Test parsing unknown error codes returns a formatted message."""
    error_code = 0x99999999
    expected_message = f"Unknown error code {hex(error_code)}"

    assert parse_error_code(error_code) == expected_message


def test_parse_error_code_with_none() -> None:
    """Test parsing None returns a default message."""
    expected_message = "Unknown error code"

    assert parse_error_code(None) == expected_message
