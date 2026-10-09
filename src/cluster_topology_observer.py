# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Cluster topology changes observer."""

import logging

from charmlibs.systemd import daemon_reload, service_enable
from jinja2 import Template
from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.managers.observer import copy_environment
from single_kernel_postgresql.utils import render_file

logger = logging.getLogger(__name__)


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
