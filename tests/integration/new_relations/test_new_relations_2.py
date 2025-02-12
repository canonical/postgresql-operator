# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from .. import markers
from ..helpers import (
    CHARM_BASE,
)

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
DATABASE_APP_NAME = "database"
ANOTHER_DATABASE_APP_NAME = "another-database"
DATA_INTEGRATOR_APP_NAME = "data-integrator"
APP_NAMES = [APPLICATION_APP_NAME, DATABASE_APP_NAME, ANOTHER_DATABASE_APP_NAME]
DATABASE_APP_METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
FIRST_DATABASE_RELATION_NAME = "database"
SECOND_DATABASE_RELATION_NAME = "second-database"
MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "multiple-database-clusters"
ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "aliased-multiple-database-clusters"
NO_DATABASE_RELATION_NAME = "no-database"
INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE = "invalid role(s) for extra user roles"


@markers.amd64_only  # nextcloud charm not available for arm64
@pytest.mark.skip(reason="Unstable")
async def test_nextcloud_db_blocked(ops_test: OpsTest, charm: str) -> None:
    # Deploy Database Charm and Nextcloud
    await asyncio.gather(
        ops_test.model.deploy(
            charm,
            application_name=DATABASE_APP_NAME,
            num_units=1,
            base=CHARM_BASE,
            config={"profile": "testing"},
        ),
        ops_test.model.deploy(
            "nextcloud",
            channel="edge",
            application_name="nextcloud",
            num_units=1,
            base=CHARM_BASE,
        ),
    )
    await asyncio.gather(
        ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=2000),
        ops_test.model.wait_for_idle(
            apps=["nextcloud"],
            status="blocked",
            raise_on_blocked=False,
            timeout=2000,
        ),
    )

    await ops_test.model.relate("nextcloud:database", f"{DATABASE_APP_NAME}:database")

    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME, "nextcloud"],
        status="active",
        raise_on_blocked=False,
        timeout=1000,
    )
