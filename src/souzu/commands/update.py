import os
import subprocess
import sys
from pathlib import Path

from souzu.meta import find_souzu


def find_uv() -> Path:
    paths = Path(os.environ.get("PATH", "")).as_posix().split(os.pathsep)
    for path in paths:
        uv_path = Path(path) / "uv"
        if uv_path.is_file() and os.access(uv_path, os.X_OK):
            return uv_path

    local_uv = Path.home() / ".local/bin/uv"
    if local_uv.is_file() and os.access(local_uv, os.X_OK):
        return local_uv

    raise FileNotFoundError("uv executable not found")


def get_souzu_version() -> str:
    """Get the souzu version"""
    return (
        subprocess.run([find_souzu(), "--version"], capture_output=True, check=True)  # noqa: S603
        .stdout.decode()
        .strip()
    )


def update() -> bool:
    """
    Update souzu.

    If the update is successful and a new version is installed, return True, otherwise return False.
    """
    try:
        uv_path = find_uv()
        old_version = get_souzu_version()
        subprocess.run([uv_path, "tool", "update", "souzu"])  # noqa: S603
        new_version = get_souzu_version()
        if old_version != new_version:
            print(f"Updated from {old_version} to {new_version}")  # noqa: T201
            return True
        else:
            print(f"Already up to date: {old_version}")  # noqa: T201
            return False
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(e, file=sys.stderr)  # noqa: T201
        return False
