# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
# from ops.framework import EventBase
import pathlib
from unittest.mock import patch

import pytest
from ops.testing import Harness
from single_kernel_postgresql.config.literals import PEER_RELATION

from charm import PostgresqlOperatorCharm

CREATE_CLUSTER_CONF_PATH = "/etc/postgresql-common/createcluster.d/pgcharm.conf"

# used for assert functions


@pytest.fixture(autouse=True)
def _no_render(tmp_path):
    """Point the Patroni config render at nothing for the unit suite.

    The real render needs the snap's live paths.
    """
    with (
        patch("single_kernel_postgresql.managers.config.render_file"),
        patch(
            "single_kernel_postgresql.workload.vm.VMWorkload.is_patroni_running",
            return_value=False,
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def harness():
    harness = Harness(
        PostgresqlOperatorCharm,
        meta=pathlib.Path("metadata.yaml").read_text(),
        actions=pathlib.Path("actions.yaml").read_text(),
    )
    harness.begin()
    harness.add_relation(PEER_RELATION, harness.charm.app.name)
    harness.add_relation("restart", harness.charm.app.name)
    yield harness
    harness.cleanup()


def test_config_fallback(harness):
    """Test that config options with dashes (-) override config options with underscores (_)."""
    harness.disable_hooks()
    charm: PostgresqlOperatorCharm = harness.charm

    assert charm.config.connection_authentication_timeout == 60

    harness.update_config({"connection_authentication_timeout": 50})
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 50

    harness.update_config({"connection-authentication-timeout": 90})
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 90

    harness.update_config(unset=["connection-authentication-timeout"])
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 50

    harness.update_config(unset=["connection_authentication_timeout"])
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 60

    harness.update_config({"connection-authentication-timeout": 120})
    del harness.charm.config
    assert charm.config.connection_authentication_timeout == 120
