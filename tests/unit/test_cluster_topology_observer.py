# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import patch

import pytest
from jinja2 import Template
from ops.testing import Harness
from single_kernel_postgresql.config.enums import Substrates
from single_kernel_postgresql.config.literals import PEER_RELATION

from charm import PostgresqlOperatorCharm
from cluster_topology_observer import start_raft_observer


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(PostgresqlOperatorCharm)
    harness.add_relation(PEER_RELATION, "postgresql")
    harness.begin()
    yield harness
    harness.cleanup()


def test_start_raft_observer(harness):
    with (
        patch("cluster_topology_observer.daemon_reload") as _daemon_reload,
        patch("cluster_topology_observer.service_enable") as _service_enable,
        patch("cluster_topology_observer.render_file") as _render_file,
        patch(
            "cluster_topology_observer.copy_environment", return_value={"ENV": "var"}
        ) as _copy_environment,
    ):
        # Get the expected content from a file.
        with open("templates/raft-observer.service.j2") as file:
            contents = file.read()
            template = Template(contents)
        expected_service = template.render(
            envvars={"ENV": "var"},
            script="python3 -m single_kernel_postgresql.scripts.raft_observer",
        )
        with open("templates/raft-observer.timer.j2") as file:
            contents = file.read()
            template = Template(contents)
        expected_timer = template.render()

        start_raft_observer()

        _daemon_reload.assert_called_once_with()
        _service_enable.assert_called_once_with("/etc/systemd/system/raft-observer.timer", "--now")
        assert _render_file.call_count == 2
        _render_file.assert_any_call(
            Substrates.VM,
            "/etc/systemd/system/raft-observer.service",
            expected_service,
            0o644,
            change_owner=False,
        )
        _render_file.assert_any_call(
            Substrates.VM,
            "/etc/systemd/system/raft-observer.timer",
            expected_timer,
            0o644,
            change_owner=False,
        )
