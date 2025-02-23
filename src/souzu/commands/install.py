import subprocess
import sys
from importlib import resources

from souzu.meta import find_souzu
from souzu.systemd import USER_SERVICE_PATH, res


def install() -> bool:
    """
    Install a systemd user service that runs souzu on boot and restarts it if it crashes.
    """
    try:
        with resources.path(res, "souzu.service.template") as template_path:
            with open(template_path) as template_file:
                template = template_file.read()

        souzu_path = '"' + str(find_souzu()).replace('"', '\\"') + '"'

        USER_SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)

        with USER_SERVICE_PATH.open("w") as user_service_file:
            user_service_file.write(template.format(souzu_path=souzu_path))

        print(f"Installed systemd user service at {USER_SERVICE_PATH}")  # noqa: T201
        return True
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(e, file=sys.stderr)  # noqa: T201
        return False
