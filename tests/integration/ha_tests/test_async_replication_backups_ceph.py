#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import gather

import pytest
from juju.model import Model
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, retry_if_exception_message, stop_after_delay, wait_fixed

from .. import markers
from ..conftest import ConnectionInformation
from ..helpers import DATABASE_APP_NAME, get_leader_unit, get_primary
from .conftest import fast_forward

logger = logging.getLogger(__name__)

CLUSTER_SIZE = 3
FAST_INTERVAL = "10s"
IDLE_PERIOD = 5
TIMEOUT = 2000

PRIMARY_S3_APP = "s3-primary"
STANDBY_S3_APP = "s3-standby"
EXPECTED_STANDBY_BACKUP_MESSAGE = (
    "Backups are not supported on a standby cluster. "
    "Run create-backup on the primary cluster instead."
)


async def _ensure_postgresql_deployed(model: Model, charm: str) -> None:
    """Deploy PostgreSQL in the given model when it is not already present."""
    if DATABASE_APP_NAME not in model.applications:
        await model.deploy(charm, num_units=CLUSTER_SIZE, config={"profile": "testing"})


async def _configure_s3_integrator(
    model: Model,
    app_name_to_deploy: str,
    microceph: ConnectionInformation,
) -> None:
    """Deploy and configure one S3 integrator app against microceph RGW."""
    if app_name_to_deploy not in model.applications:
        await model.deploy(
            "s3-integrator", application_name=app_name_to_deploy, channel="1/stable"
        )
        await model.wait_for_idle(
            apps=[app_name_to_deploy], idle_period=IDLE_PERIOD, timeout=TIMEOUT
        )

    await model.applications[app_name_to_deploy].set_config({
        "endpoint": f"https://{microceph.host}",
        "bucket": f"{app_name_to_deploy}-bucket",
        "path": "/pg",
        "region": "",
        "s3-uri-style": "path",
        "tls-ca-chain": microceph.cert,
    })

    action = await model.units.get(f"{app_name_to_deploy}/0").run_action(
        "sync-s3-credentials",
        **{
            "access-key": microceph.access_key_id,
            "secret-key": microceph.secret_access_key,
        },
    )
    await action.wait()

    await model.relate(DATABASE_APP_NAME, app_name_to_deploy)


@markers.juju3
@pytest.mark.abort_on_fail
async def test_standby_backup_rejected_with_clear_message(
    ops_test: OpsTest,
    first_model: Model,
    second_model: Model,
    microceph: ConnectionInformation,
    charm: str,
) -> None:
    """Validate backup behavior with async replication and Ceph-backed S3 configuration.

    This test mirrors the live scenario:
    1. Two PostgreSQL clusters in separate models.
    2. Ceph-backed s3-integrator configured in each model.
    3. Async replication created between clusters.
    4. Backup succeeds on primary cluster and fails with clear message on standby.
    """
    await _ensure_postgresql_deployed(first_model, charm)
    await _ensure_postgresql_deployed(second_model, charm)

    await _configure_s3_integrator(first_model, PRIMARY_S3_APP, microceph)
    await _configure_s3_integrator(second_model, STANDBY_S3_APP, microceph)

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME, PRIMARY_S3_APP],
                status="active",
                idle_period=IDLE_PERIOD,
                timeout=TIMEOUT,
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME, STANDBY_S3_APP],
                status="active",
                idle_period=IDLE_PERIOD,
                timeout=TIMEOUT,
            ),
        )

    # Cross-model replication wiring.
    offer_command = f"offer {DATABASE_APP_NAME}:replication-offer replication-offer"
    offer_rc, _, offer_stderr = await ops_test.juju(*offer_command.split())
    assert offer_rc == 0, f"offer failed: {offer_stderr}"

    consume_command = (
        f"consume -m {second_model.info.name} admin/{first_model.info.name}.replication-offer"
    )
    consume_rc, consume_stdout, consume_stderr = await ops_test.juju(*consume_command.split())
    assert consume_rc == 0, f"consume failed: {consume_stderr or consume_stdout}"

    async with ops_test.fast_forward(FAST_INTERVAL), fast_forward(second_model, FAST_INTERVAL):
        await gather(
            first_model.wait_for_idle(
                apps=[DATABASE_APP_NAME, PRIMARY_S3_APP],
                status="active",
                idle_period=IDLE_PERIOD,
                timeout=TIMEOUT,
            ),
            second_model.wait_for_idle(
                apps=[DATABASE_APP_NAME, STANDBY_S3_APP],
                status="active",
                idle_period=IDLE_PERIOD,
                timeout=TIMEOUT,
            ),
        )

    for attempt in Retrying(
        stop=stop_after_delay(180),
        wait=wait_fixed(5),
        retry=retry_if_exception_message(match='application "replication-offer" not found'),
        reraise=True,
    ):
        with attempt:
            await second_model.relate(DATABASE_APP_NAME, "replication-offer")

    # Promote first model as primary cluster in async replication.
    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME, model=first_model)
    assert leader_unit is not None, "No leader unit found in primary model"
    create_replication = await leader_unit.run_action("create-replication")
    await create_replication.wait()
    assert (create_replication.results.get("return-code", None) == 0) or (
        create_replication.results.get("Code", None) == "0"
    ), "create-replication failed"

    # Primary backup should succeed.
    primary_unit_name = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0", model=first_model)
    replica_unit_name = next(
        unit.name
        for unit in first_model.applications[DATABASE_APP_NAME].units
        if unit.name != primary_unit_name
    )
    primary_backup_action = await first_model.units[replica_unit_name].run_action("create-backup")
    await primary_backup_action.wait()
    assert (primary_backup_action.results.get("return-code", None) == 0) or (
        primary_backup_action.results.get("Code", None) == "0"
    ), "create-backup failed on primary cluster"

    # Standby backup should fail with explicit unsupported-operation message.
    standby_unit = second_model.units[f"{DATABASE_APP_NAME}/0"]
    for attempt in Retrying(stop=stop_after_delay(180), wait=wait_fixed(5), reraise=True):
        with attempt:
            standby_backup_action = await standby_unit.run_action("create-backup")
            await standby_backup_action.wait()
            assert standby_backup_action.status == "failed"
            action_message = standby_backup_action.data.get("message", "")
            assert EXPECTED_STANDBY_BACKUP_MESSAGE in action_message
