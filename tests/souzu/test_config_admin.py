"""Tests for souzu.config_admin."""

import json
from pathlib import Path

import pytest

from souzu.config_admin import (
    ConfigDict,
    ConfigUpdateError,
    changed_paths,
    load_config_dict,
    prepare_update,
    redact_config,
    write_config,
)

BOT_TOKEN = "xoxb-1234567890-abcdefghijkl"
APP_TOKEN = "xapp-1-A000-1234567890-wxyz"


def _current() -> ConfigDict:
    return {
        "printers": {
            "SERIAL1": {
                "access_code": "12345678",
                "filename_prefix": None,
                "ip_address": None,
            },
        },
        "slack": {
            "access_token": BOT_TOKEN,
            "app_token": APP_TOKEN,
            "print_notification_channel": "C_PRINT",
            "error_notification_channel": "C_ERR",
            "admin_user_group": "3dprinterteam",
        },
        "timezone": "America/Toronto",
    }


class TestRedactConfig:
    def test_redacts_all_secrets(self) -> None:
        text = json.dumps(redact_config(_current()))
        assert BOT_TOKEN not in text
        assert APP_TOKEN not in text
        assert "12345678" not in text
        assert "C_PRINT" in text

    def test_long_secret_shows_suffix_and_short_secret_does_not(self) -> None:
        redacted = redact_config(_current())
        assert redacted["slack"]["access_token"] == "<redacted …ijkl>"
        assert redacted["printers"]["SERIAL1"]["access_code"] == "<redacted>"

    def test_leaves_input_and_null_secrets_unchanged(self) -> None:
        current = _current()
        current["slack"]["app_token"] = None
        redacted = redact_config(current)
        assert redacted["slack"]["app_token"] is None
        assert current["slack"]["access_token"] == BOT_TOKEN


class TestPrepareUpdate:
    def test_redacted_round_trip_is_a_no_op(self) -> None:
        current = _current()
        new = prepare_update(json.dumps(redact_config(current)), current)
        assert new == current
        assert changed_paths(current, new) == []

    def test_changes_one_secret_and_keeps_the_others(self) -> None:
        current = _current()
        edited = redact_config(current)
        edited["printers"]["SERIAL1"]["access_code"] = "87654321"
        new = prepare_update(json.dumps(edited), current)
        assert new["printers"]["SERIAL1"]["access_code"] == "87654321"
        assert new["slack"]["access_token"] == BOT_TOKEN
        assert changed_paths(current, new) == ["printers.SERIAL1.access_code"]

    def test_fills_in_defaults(self) -> None:
        new = prepare_update('{"printers": {"S2": {"access_code": "abc"}}}', {})
        assert new["printers"]["S2"]["ip_address"] is None
        assert new["timezone"] == "UTC"

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("{not json", "Invalid JSON"),
            ("[]", "JSON object"),
            ('{"timezone": "Mars/Olympus"}', "Mars/Olympus"),
            ('{"slakc": {}}', "slakc"),
            ('{"printers": {"S2": {}}}', "access_code"),
            ('{"printers": {"S2": {"access_code": "<redacted>"}}}', "no current"),
        ],
    )
    def test_rejects_invalid_input(self, text: str, match: str) -> None:
        with pytest.raises(ConfigUpdateError, match=match):
            prepare_update(text, {})

    @pytest.mark.parametrize("key", ["access_token", "app_token"])
    def test_rejects_removal_of_slack_tokens(self, key: str) -> None:
        current = _current()
        edited = redact_config(current)
        edited["slack"][key] = None
        with pytest.raises(ConfigUpdateError, match=f"slack.{key}"):
            prepare_update(json.dumps(edited), current)


class TestChangedPaths:
    def test_reports_added_removed_and_changed(self) -> None:
        old = {"a": {"x": 1, "y": 2}, "b": 1, "c": 1}
        new = {"a": {"x": 1, "y": 3}, "c": 1, "d": 1}
        assert changed_paths(old, new) == ["a.y", "b", "d"]


class TestFiles:
    def test_load_missing_file_gives_defaults(self, tmp_path: Path) -> None:
        loaded = load_config_dict(tmp_path / "souzu.json")
        assert loaded["printers"] == {}
        assert loaded["slack"]["admin_user_group"] == "3dprinterteam"

    def test_load_invalid_file_gives_raw_content(self, tmp_path: Path) -> None:
        config_file = tmp_path / "souzu.json"
        config_file.write_text('{"timezone": "Mars/Olympus"}')
        assert load_config_dict(config_file) == {"timezone": "Mars/Olympus"}

    def test_write_round_trip_with_backup(self, tmp_path: Path) -> None:
        config_file = tmp_path / "souzu.json"
        config_file.write_text('{"timezone": "UTC"}')

        write_config(_current(), config_file)

        assert load_config_dict(config_file) == _current()
        assert config_file.stat().st_mode & 0o777 == 0o600
        backup = tmp_path / "souzu.json.bak"
        assert json.loads(backup.read_text()) == {"timezone": "UTC"}
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "souzu.json",
            "souzu.json.bak",
        ]

    def test_write_creates_missing_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "sub" / "souzu.json"
        write_config(_current(), config_file)
        assert load_config_dict(config_file) == _current()
