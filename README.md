🎋 Souzu
=======

A simple service to monitor Bambu printers on the local network and deliver print status notifications to a Slack channel.

On start, Souzu listens for Bambu printer advertisements on the local network. For each printer, if it has a corresponding access code in its config file, it tries to monitor the MQTT stream from the printer.

Souzu never sends any commands to the printer, not even to poll for full status reports. This means that the load on the printer should be minimal.

## Configuration

Put a configuration file in `~/.config/souzu.json`:

```json
{
  "printers": {
    "PRINTER_SERIAL_NUMBER": {
      "access_code": "ACCESS_CODE",
      "filename_prefix": "prefix for log file names (optional)"
    },
    "OTHER_PRINTER_SERIAL_NUMBER": {
      "access_code": "ACCESS_CODE",
      "ip_address": "hardcoded ip address (optional)"
    }
  },
  "slack": {
    "access_token": "SLACK_ACCESS_TOKEN",
    "print_notification_channel": "SOME_CHANNEL_ID",
    "error_notification_channel": "SOME_CHANNEL_ID"
  }
}
```

## Installation

Install [uv](https://github.com/astral-sh/uv).

To install from GitHub:

```sh
uv tool install git+https://github.com/kwartzlab/souzu
```

Then run `souzu monitor` to start the service.

### Updating

Souzu includes a simple self-updater that wraps uv. Run `souzu update` to update to the latest version.

### Systemd service

Souzu includes definitions for several systemd units, which can be installed with `souzu install`.

To enable a service that runs the Souzu monitor service on boot:

```sh
systemctl --user enable souzu-monitor.service
systemctl --user start souzu-monitor.service  # if you want to start the service immediately
```

To enable a service that automatically updates and restarts Souzu, checking for updates every 5 minutes:

```sh
systemctl --user enable --now souzu-update.timer
```

(yes, checking for updates every 5 minutes is excessive, but we're using it as a poor man's continuous deployment)

## Developing

Install [uv](https://github.com/astral-sh/uv).

To install git pre-commit hooks for linting and formatting:

```sh
./install-hooks.sh
```

To run from the source tree:

```sh
uv run souzu
```

To build and install locally:

```sh
./build.sh -i
```

To build and push to a remote host over SSH, assuming the remote host also has `uv` installed:

```sh
./build.sh -p host
```