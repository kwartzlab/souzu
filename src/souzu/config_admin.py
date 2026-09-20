"""Logic to show and replace the config file on request from an admin."""

import json
import os
import re
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cattrs import BaseValidationError, Converter, transform_error
from cattrs.v import format_exception

from souzu.config import CONFIG_FILE, Config

_REDACTED_PATTERN = re.compile(r"^<redacted.*>$")
# Secrets shorter than this show no suffix, because 4 characters are a large
# part of a short secret such as a printer access code.
_MIN_LENGTH_FOR_SUFFIX = 16
_SLACK_TOKEN_KEYS = ("access_token", "app_token")

ConfigDict = dict[str, Any]


class ConfigUpdateError(Exception):
    """Raised when a submitted config cannot be applied."""


class _TimezoneError(ValueError):
    pass


def _format_exception(exc: BaseException, type_: type | None) -> str:
    if isinstance(exc, _TimezoneError):
        return str(exc)
    return format_exception(exc, type_)


def _strict_timezone(tz_str: str, _: type[ZoneInfo]) -> ZoneInfo:
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise _TimezoneError(f"unknown timezone {tz_str!r}") from e


_STRICT_SERIALIZER = Converter(forbid_extra_keys=True)
_STRICT_SERIALIZER.register_structure_hook(ZoneInfo, _strict_timezone)
_STRICT_SERIALIZER.register_unstructure_hook(ZoneInfo, str)


def _validate(config_dict: ConfigDict) -> ConfigDict:
    """Validate a config dict. Returns the full dict, with defaults filled in."""
    try:
        config = _STRICT_SERIALIZER.structure(config_dict, Config)
    except BaseValidationError as e:
        raise ConfigUpdateError(
            "; ".join(
                transform_error(e, path="config", format_exception=_format_exception)
            )
        ) from e
    except Exception as e:
        raise ConfigUpdateError(f"Invalid config: {e}") from e
    return _STRICT_SERIALIZER.unstructure(config)


def load_config_dict(config_file: Path = CONFIG_FILE) -> ConfigDict:
    """Load the config from disk, with defaults filled in.

    This reads the file and not the in-memory config, so that the result includes
    edits made after the process started.
    """
    if not config_file.exists():
        return _STRICT_SERIALIZER.unstructure(Config())
    with config_file.open("r") as f:
        raw = json.load(f)
    try:
        return _validate(raw)
    except ConfigUpdateError:
        # The file on disk can predate strict validation. Show it as it is so
        # an admin can repair it.
        return raw


def _secret_slots(config_dict: ConfigDict) -> Iterator[tuple[str, ConfigDict, str]]:
    """Yield (dotted path, container, key) for each secret in the config."""
    slack = config_dict.get("slack")
    if isinstance(slack, dict):
        for key in _SLACK_TOKEN_KEYS:
            if key in slack:
                yield f"slack.{key}", slack, key
    printers = config_dict.get("printers")
    if isinstance(printers, dict):
        for serial, printer in printers.items():
            if isinstance(printer, dict) and "access_code" in printer:
                yield f"printers.{serial}.access_code", printer, "access_code"


def _redact_value(value: str) -> str:
    if len(value) >= _MIN_LENGTH_FOR_SUFFIX:
        return f"<redacted …{value[-4:]}>"
    return "<redacted>"


def redact_config(config_dict: ConfigDict) -> ConfigDict:
    """Return a copy of the config with each secret replaced by a placeholder."""
    redacted = deepcopy(config_dict)
    for _path, container, key in _secret_slots(redacted):
        if isinstance(container[key], str):
            container[key] = _redact_value(container[key])
    return redacted


def _restore_secrets(new: ConfigDict, current: ConfigDict) -> None:
    current_secrets = {
        path: container[key] for path, container, key in _secret_slots(current)
    }
    for path, container, key in _secret_slots(new):
        value = container[key]
        if isinstance(value, str) and _REDACTED_PATTERN.match(value):
            if not isinstance(current_secrets.get(path), str):
                raise ConfigUpdateError(
                    f"{path} is a redacted placeholder, but there is no current "
                    "value to keep"
                )
            container[key] = current_secrets[path]


def prepare_update(text: str, current: ConfigDict) -> ConfigDict:
    """Turn submitted JSON text into a validated config dict ready to write.

    Redacted placeholders keep the current value of the secret. Raises
    ConfigUpdateError with a message for the admin if the text is not usable.
    """
    try:
        new = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigUpdateError(f"Invalid JSON: {e}") from e
    if not isinstance(new, dict):
        raise ConfigUpdateError("The config must be a JSON object")

    _restore_secrets(new, current)
    validated = _validate(new)

    # Without these tokens the bot cannot receive commands, so an admin could
    # not undo the change over Slack.
    current_slack = current.get("slack") or {}
    for key in _SLACK_TOKEN_KEYS:
        if current_slack.get(key) and not validated["slack"].get(key):
            raise ConfigUpdateError(f"slack.{key} cannot be removed over Slack")
    return validated


def changed_paths(old: object, new: object, prefix: str = "") -> list[str]:
    """List the dotted paths whose values differ. Does not include the values."""
    if isinstance(old, dict) and isinstance(new, dict):
        paths: list[str] = []
        for key in sorted(old.keys() | new.keys()):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in old or key not in new:
                paths.append(path)
            else:
                paths.extend(changed_paths(old[key], new[key], path))
        return paths
    return [] if old == new else [prefix]


def write_config(config_dict: ConfigDict, config_file: Path = CONFIG_FILE) -> None:
    """Replace the config file atomically. Keeps the previous file as a backup."""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    if config_file.exists():
        backup = config_file.with_name(config_file.name + ".bak")
        backup.write_bytes(config_file.read_bytes())
        backup.chmod(0o600)
    with NamedTemporaryFile(
        "w", dir=config_file.parent, prefix=config_file.name, delete=False
    ) as f:
        json.dump(config_dict, f, indent=2)
        f.write("\n")
    # NamedTemporaryFile creates the file with mode 0600.
    os.replace(f.name, config_file)
