"""
Self-update command for souzu.

This module implements a smart update strategy that:
1. Checks GitHub API for the latest commit hash (avoiding unnecessary clones)
2. Compares with the currently installed version
3. Uses uv export to respect uv.lock dependencies during upgrade

WORKAROUND: This clone-export-upgrade strategy works around astral-sh/uv#5815,
which tracks the lack of a --locked flag for `uv tool install`. Once that issue
is resolved and the fix is available on deployment targets, this can be simplified
to `uv tool upgrade souzu --locked` (or similar).

See: https://github.com/astral-sh/uv/issues/5815
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests

from souzu.meta import find_souzu

GITHUB_REPO = 'kwartzlab/souzu'
GITHUB_API_URL = f'https://api.github.com/repos/{GITHUB_REPO}/commits/main'
GITHUB_CLONE_URL = f'https://github.com/{GITHUB_REPO}.git'

logger = logging.getLogger(__name__)


def find_uv() -> Path:
    """Find the uv executable in PATH or common locations."""
    paths = Path(os.environ.get('PATH', '')).as_posix().split(os.pathsep)
    for path in paths:
        uv_path = Path(path) / 'uv'
        if uv_path.is_file() and os.access(uv_path, os.X_OK):
            return uv_path

    local_uv = Path.home() / '.local/bin/uv'
    if local_uv.is_file() and os.access(local_uv, os.X_OK):
        return local_uv

    raise FileNotFoundError("uv executable not found")


def get_souzu_version() -> str:
    """Get the souzu version string (e.g., 'souzu 0.1.dev73+g3296c1d9b')."""
    return (
        subprocess.run(  # noqa: S603
            [find_souzu(), '--version'], capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )


def get_installed_commit_hash() -> str | None:
    """
    Extract the git commit hash from the installed souzu version.

    Version format from hatch-vcs: 'souzu 0.1.dev73+g3296c1d9b'
    The commit hash follows '+g' prefix (the 'g' indicates git).

    Returns None if the version doesn't contain a commit hash
    (e.g., release versions like '1.0.0').
    """
    version = get_souzu_version()
    match = re.search(r'\+g([a-f0-9]+)', version)
    if match:
        return match.group(1)
    return None


def get_latest_commit_hash() -> str | None:
    """
    Fetch the latest commit hash from GitHub API.

    Returns the full SHA of the latest commit on main branch,
    or None if the API request fails.
    """
    try:
        response = requests.get(
            GITHUB_API_URL,
            headers={'Accept': 'application/vnd.github.v3+json'},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get('sha')
    except requests.RequestException as e:
        logger.warning("Failed to fetch latest commit from GitHub: %s", e)
        return None


def is_update_available() -> bool:
    """
    Check if an update is available by comparing commit hashes.

    Returns True if:
    - We can't determine the installed commit (conservative: assume update needed)
    - We can't fetch the latest commit (conservative: assume update needed)
    - The commits differ
    """
    installed = get_installed_commit_hash()
    if installed is None:
        logger.info("Could not determine installed commit hash, will check for update")
        return True

    latest = get_latest_commit_hash()
    if latest is None:
        logger.info("Could not fetch latest commit from GitHub, will check for update")
        return True

    # Compare short hash (installed) with full hash (from API)
    if latest.startswith(installed):
        logger.info("Already at latest commit: %s", installed)
        return False

    logger.info("Update available: %s -> %s", installed, latest[: len(installed)])
    return True


def clone_export_upgrade() -> bool:
    """
    Clone the repo, export locked dependencies, and upgrade with constraints.

    This is the workaround for astral-sh/uv#5815 - we clone the repo to access
    the uv.lock file, export it to a requirements.txt format, then use that
    as constraints for the tool upgrade.

    Returns True if the upgrade command succeeded.
    """
    uv_path = find_uv()

    with tempfile.TemporaryDirectory(prefix='souzu-update-') as tmpdir:
        repo_dir = Path(tmpdir) / 'souzu'
        constraints_file = Path(tmpdir) / 'constraints.txt'

        logger.info("Cloning repository to %s", repo_dir)
        result = subprocess.run(  # noqa: S603
            ['git', 'clone', '--depth=1', GITHUB_CLONE_URL, str(repo_dir)],  # noqa: S607
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error("Failed to clone repository: %s", result.stderr.decode())
            return False

        logger.info("Exporting locked dependencies")
        result = subprocess.run(  # noqa: S603
            [
                uv_path,
                'export',
                '--frozen',
                '--no-hashes',
                '--no-emit-project',
                '-o',
                str(constraints_file),
            ],
            cwd=repo_dir,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error("Failed to export dependencies: %s", result.stderr.decode())
            return False

        logger.info("Reinstalling souzu with locked constraints")
        result = subprocess.run(  # noqa: S603
            [
                uv_path,
                'tool',
                'install',
                '--reinstall',
                f'git+{GITHUB_CLONE_URL}',
                '--constraints',
                str(constraints_file),
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error("Failed to install souzu: %s", result.stderr.decode())
            return False

        logger.info("Upgrade completed successfully")
        return True


def update(restart: bool = False) -> None:
    """
    Update souzu to the latest version if available.

    This implements a smart update strategy:
    1. Query GitHub API to check if an update is available (fast, no clone)
    2. If update available, use clone-export-upgrade to respect uv.lock
    3. Optionally restart the systemd service

    Args:
        restart: If True, restart souzu.service after a successful update
    """
    old_version = get_souzu_version()

    if not is_update_available():
        print(f"Already up to date: {old_version}")  # noqa: T201
        return

    if not clone_export_upgrade():
        print("Update failed, see logs for details")  # noqa: T201
        return

    new_version = get_souzu_version()
    if old_version != new_version:
        print(f"Updated from {old_version} to {new_version}")  # noqa: T201
        if restart:
            subprocess.run(  # noqa: S603
                ['/usr/bin/systemctl', '--user', 'restart', 'souzu.service']
            )
    else:
        print(f"Already up to date: {old_version}")  # noqa: T201
