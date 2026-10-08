# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cluster topology changes observer."""

import logging
import os
from pathlib import Path
from sys import version_info

from charmlibs.systemd import daemon_reload, service_enable
from jinja2 import Template
from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.utils import render_file

logger = logging.getLogger(__name__)

# File path for the spawned cluster topology observer process to write logs.
LOG_FILE_PATH = "/var/log/cluster_topology_observer.log"


def copy_environment() -> dict[str, str]:
    """Environment variables to pass to a script."""
    new_env = os.environ.copy()
    if "JUJU_CONTEXT_ID" in new_env:
        new_env.pop("JUJU_CONTEXT_ID")
    # Generate the venv path based on the existing lib path
    for loc in new_env["PYTHONPATH"].split(":"):
        path = Path(loc)
        venv_path = (
            path
            / ".."
            / "venv"
            / "lib"
            / f"python{version_info.major}.{version_info.minor}"
            / "site-packages"
        )
        if path.stem == "lib":
            new_env["PYTHONPATH"] = f"{venv_path.resolve()}:{new_env['PYTHONPATH']}"
            break
    return {var: val for var, val in new_env.items() if "JUJU" in var or "PATH" in var}


def start_raft_observer() -> None:
    """Render systemd units and start the observer."""
    timer_service_file = "/etc/systemd/system/raft-observer.timer"
    oneshot_service_file = "/etc/systemd/system/raft-observer.service"

    with open("templates/raft-observer.service.j2") as file:
        template = Template(file.read())

    rendered = template.render(
        envvars=copy_environment(),
        script="-m single_kernel_postgresql.scripts.raft_observer",
    )
    render_file(Substrates.VM, oneshot_service_file, rendered, 0o644, change_owner=False)

    with open("templates/raft-observer.timer.j2") as file:
        template = Template(file.read())

    rendered = template.render()
    render_file(Substrates.VM, timer_service_file, rendered, 0o644, change_owner=False)

    # Reload systemd to pick up the new service
    daemon_reload()
    service_enable(timer_service_file, "--now")
    logger.info("Installed and enabled raft observer timer")
