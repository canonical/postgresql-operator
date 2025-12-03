#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import uuid

import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

from .conftest import GCP
from .helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
    backup_operations,
    db_connect,
    get_password,
    get_unit_address,
    wait_for_idle_on_blocked,
)
from .juju_ import juju_major_version

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
if juju_major_version < 3:
    tls_certificates_app_name = "tls-certificates-operator"
    tls_channel = "legacy/stable"
    tls_config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    tls_certificates_app_name = "self-signed-certificates"
    tls_channel = "1/stable"
    tls_config = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_backup_gcp(ops_test: OpsTest, gcp_cloud_configs: tuple[dict, dict], charm) -> None:
    """Build and deploy two units of PostgreSQL in GCP, test backup and restore actions."""
    config = gcp_cloud_configs[0]
    credentials = gcp_cloud_configs[1]

    await backup_operations(
        ops_test,
        S3_INTEGRATOR_APP_NAME,
        tls_certificates_app_name,
        tls_config,
        tls_channel,
        credentials,
        GCP,
        config,
        charm,
    )
    database_app_name = f"{DATABASE_APP_NAME}-gcp"

    # Remove the database app.
    await ops_test.model.remove_application(database_app_name, block_until_done=True)

    # Remove the TLS operator.
    await ops_test.model.remove_application(tls_certificates_app_name, block_until_done=True)


async def test_restore_on_new_cluster(
    ops_test: OpsTest, charm, gcp_cloud_configs: tuple[dict, dict]
) -> None:
    """Test that is possible to restore a backup to another PostgreSQL cluster."""
    previous_database_app_name = f"{DATABASE_APP_NAME}-gcp"
    database_app_name = f"new-{DATABASE_APP_NAME}"
    await ops_test.model.deploy(
        charm,
        application_name=previous_database_app_name,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )
    await ops_test.model.deploy(
        charm,
        application_name=database_app_name,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )
    await ops_test.model.relate(previous_database_app_name, S3_INTEGRATOR_APP_NAME)
    await ops_test.model.relate(database_app_name, S3_INTEGRATOR_APP_NAME)
    async with ops_test.fast_forward():
        logger.info(
            "waiting for the database charm to become blocked due to existing backups from another cluster in the repository"
        )
        await wait_for_idle_on_blocked(
            ops_test,
            previous_database_app_name,
            2,
            S3_INTEGRATOR_APP_NAME,
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        )
        logger.info(
            "waiting for the database charm to become blocked due to existing backups from another cluster in the repository"
        )
        await wait_for_idle_on_blocked(
            ops_test,
            database_app_name,
            0,
            S3_INTEGRATOR_APP_NAME,
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
        )

    # Remove the database app with the same name as the previous one (that was used only to test
    # that the cluster becomes blocked).
    await ops_test.model.remove_application(previous_database_app_name, block_until_done=True)

    # Run the "list backups" action.
    unit_name = f"{database_app_name}/0"
    logger.info("listing the available backups")
    action = await ops_test.model.units.get(unit_name).run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    assert backups, "backups not outputted"
    await wait_for_idle_on_blocked(
        ops_test,
        database_app_name,
        0,
        S3_INTEGRATOR_APP_NAME,
        ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
    )

    # Run the "restore backup" action.
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("restoring the backup")
            # Last two entries are 'action: restore', that cannot be used without restore-to-time parameter
            most_recent_real_backup = backups.split("\n")[-3]
            backup_id = most_recent_real_backup.split()[0]
            action = await ops_test.model.units.get(unit_name).run_action(
                "restore", **{"backup-id": backup_id}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "restore hasn't succeeded"

    # Wait for the restore to complete.
    async with ops_test.fast_forward():
        unit = ops_test.model.units.get(f"{database_app_name}/0")
        await ops_test.model.block_until(
            lambda: unit.workload_status_message == ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE
        )

    # Check that the backup was correctly restored by having only the first created table.
    logger.info("checking that the backup was correctly restored")
    password = await get_password(ops_test, unit_name)
    address = get_unit_address(ops_test, unit_name)
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
        )
    connection.close()


async def test_invalid_config_and_recovery_after_fixing_it(
    ops_test: OpsTest, gcp_cloud_configs: tuple[dict, dict]
) -> None:
    """Test that the charm can handle invalid and valid backup configurations."""
    database_app_name = f"new-{DATABASE_APP_NAME}"

    # Provide invalid backup configurations.
    logger.info("configuring S3 integrator for an invalid cloud")
    await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config({
        "endpoint": "endpoint",
        "bucket": "bucket",
        "path": "path",
        "region": "region",
    })
    action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
        "sync-s3-credentials",
        **{
            "access-key": "access-key",
            "secret-key": "secret-key",
        },
    )
    await action.wait()
    logger.info("waiting for the database charm to become blocked")
    unit = ops_test.model.units.get(f"{database_app_name}/0")
    await ops_test.model.block_until(
        lambda: unit.workload_status_message == FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE
    )

    # Provide valid backup configurations, but from another cluster repository.
    logger.info(
        "configuring S3 integrator for a valid cloud, but with the path of another cluster repository"
    )
    await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(gcp_cloud_configs[0])
    action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
        "sync-s3-credentials",
        **gcp_cloud_configs[1],
    )
    await action.wait()
    logger.info("waiting for the database charm to become blocked")
    unit = ops_test.model.units.get(f"{database_app_name}/0")
    await ops_test.model.block_until(
        lambda: unit.workload_status_message == ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE
    )

    # Provide valid backup configurations, with another path in the S3 bucket.
    logger.info("configuring S3 integrator for a valid cloud")
    config = gcp_cloud_configs[0].copy()
    config["path"] = f"/postgresql/{uuid.uuid1()}"
    await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(config)
    logger.info("waiting for the database charm to become active")
    await ops_test.model.wait_for_idle(
        apps=[database_app_name, S3_INTEGRATOR_APP_NAME], status="active"
    )


async def test_block_on_missing_region(
    ops_test: OpsTest, gcp_cloud_configs: tuple[dict, dict]
) -> None:
    await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config({
        **gcp_cloud_configs[0],
        "region": "",
    })
    database_app_name = f"new-{DATABASE_APP_NAME}"
    logger.info("waiting for the database charm to become blocked")
    unit = ops_test.model.units.get(f"{database_app_name}/0")
    await ops_test.model.block_until(
        lambda: unit.workload_status_message == FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE
    )
