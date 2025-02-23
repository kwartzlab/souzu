from importlib import resources
from pathlib import Path
from textwrap import dedent

from souzu.meta import find_souzu
from souzu.systemd import (
    MONITOR_SERVICE_PATH,
    UPDATE_SERVICE_PATH,
    UPDATE_TIMER_PATH,
    res,
)


def _install_template(template_name: str, path: Path, **kwargs: str) -> None:
    with resources.path(res, template_name) as template_path:
        with open(template_path) as template_file:
            template = template_file.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as user_service_file:
        user_service_file.write(template.format(**kwargs))


def install() -> None:
    """
    Install a systemd user service that runs souzu on boot and restarts it if it crashes.
    """
    souzu_path = '"' + str(find_souzu()).replace('"', '\\"') + '"'

    _install_template(
        "souzu.service.template", MONITOR_SERVICE_PATH, souzu_path=souzu_path
    )
    _install_template(
        "souzu-update.service.template", UPDATE_SERVICE_PATH, souzu_path=souzu_path
    )
    _install_template("souzu-update.timer", UPDATE_TIMER_PATH)

    print(  # noqa: T201
        dedent("""
    Installed systemd user services.

    To enable the monitor service:

        systemctl --user enable souzu.service
        systemctl --user start souzu.service  # if you want to start the service immediately

    To enable the update service:

        systemctl --user enable --now souzu-update.timer
    """)
    )
