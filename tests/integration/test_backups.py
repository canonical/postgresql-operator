#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import uuid
from typing import Dict, Tuple

import boto3
import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

from .helpers import (
    CHARM_SERIES,
    DATABASE_APP_NAME,
    construct_endpoint,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
    wait_for_idle_on_blocked,
)

ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE = "the S3 repository has backups from another cluster"
FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE = (
    "failed to access/create the bucket, check your S3 settings"
)
FAILED_TO_INITIALIZE_STANZA_ERROR_MESSAGE = "failed to initialize stanza, check your S3 settings"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"

logger = logging.getLogger(__name__)

AWS = "AWS"
GCP = "GCP"


@pytest.fixture(scope="module")
async def cloud_configs(ops_test: OpsTest, github_secrets) -> None:
    # Define some configurations and credentials.
    configs = {
        AWS: {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-vm/{uuid.uuid1()}",
            "region": "us-east-1",
        },
        GCP: {
            "endpoint": "https://storage.googleapis.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-vm/{uuid.uuid1()}",
            "region": "",
        },
    }
    credentials = {
        AWS: {
            "access-key": github_secrets["AWS_ACCESS_KEY"],
            "secret-key": github_secrets["AWS_SECRET_KEY"],
        },
        GCP: {
            "access-key": github_secrets["GCP_ACCESS_KEY"],
            "secret-key": github_secrets["GCP_SECRET_KEY"],
        },
    }
    yield configs, credentials
    # Delete the previously created objects.
    for cloud, config in configs.items():
        session = boto3.session.Session(
            aws_access_key_id=credentials[cloud]["access-key"],
            aws_secret_access_key=credentials[cloud]["secret-key"],
            region_name=config["region"],
        )
        s3 = session.resource(
            "s3", endpoint_url=construct_endpoint(config["endpoint"], config["region"])
        )
        bucket = s3.Bucket(config["bucket"])
        # GCS doesn't support batch delete operation, so delete the objects one by one.
        for bucket_object in bucket.objects.filter(Prefix=config["path"].lstrip("/")):
            bucket_object.delete()


@pytest.mark.group(1)
async def test_none() -> None:
    """Empty test so that the suite will not fail if all tests are skippedi."""
    pass


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_backup(ops_test: OpsTest, cloud_configs: Tuple[Dict, Dict]) -> None:
    """Build and deploy two units of PostgreSQL and then test the backup and restore actions."""
    # Build the PostgreSQL charm.
    charm = await ops_test.build_charm(".")

    # Deploy S3 Integrator and TLS Certificates Operator.
    await ops_test.model.deploy(S3_INTEGRATOR_APP_NAME)
    config = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
    await ops_test.model.deploy(TLS_CERTIFICATES_APP_NAME, config=config, channel="legacy/stable")

    for cloud, config in cloud_configs[0].items():
        # Deploy and relate PostgreSQL to S3 integrator (one database app for each cloud for now
        # as archive_mode is disabled after restoring the backup) and to TLS Certificates Operator
        # (to be able to create backups from replicas).
        database_app_name = f"{DATABASE_APP_NAME}-{cloud.lower()}"
        await ops_test.model.deploy(
            charm,
            application_name=database_app_name,
            num_units=2,
            series=CHARM_SERIES,
            config={"profile": "testing"},
        )
        await ops_test.model.relate(database_app_name, S3_INTEGRATOR_APP_NAME)
        await ops_test.model.relate(database_app_name, TLS_CERTIFICATES_APP_NAME)

        # Configure and set access and secret keys.
        logger.info(f"configuring S3 integrator for {cloud}")
        await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(config)
        action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
            "sync-s3-credentials",
            **cloud_configs[1][cloud],
        )
        await action.wait()
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, S3_INTEGRATOR_APP_NAME], status="active", timeout=1000
        )

        primary = await get_primary(ops_test, f"{database_app_name}/0")
        for unit in ops_test.model.applications[database_app_name].units:
            if unit.name != primary:
                replica = unit.name
                break

        # Write some data.
        password = await get_password(ops_test, primary)
        address = get_unit_address(ops_test, primary)
        logger.info("creating a table in the database")
        with db_connect(host=address, password=password) as connection:
            connection.autocommit = True
            connection.cursor().execute(
                "CREATE TABLE IF NOT EXISTS backup_table_1 (test_collumn INT );"
            )
        connection.close()

        # Run the "create backup" action.
        logger.info("creating a backup")
        action = await ops_test.model.units.get(replica).run_action("create-backup")
        await action.wait()
        backup_status = action.results.get("backup-status")
        assert backup_status, "backup hasn't succeeded"
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, S3_INTEGRATOR_APP_NAME], status="active", timeout=1000
        )

        # Run the "list backups" action.
        logger.info("listing the available backups")
        action = await ops_test.model.units.get(replica).run_action("list-backups")
        await action.wait()
        backups = action.results.get("backups")
        assert backups, "backups not outputted"
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Write some data.
        logger.info("creating a second table in the database")
        with db_connect(host=address, password=password) as connection:
            connection.autocommit = True
            connection.cursor().execute("CREATE TABLE backup_table_2 (test_collumn INT );")
        connection.close()

        # Scale down to be able to restore.
        async with ops_test.fast_forward():
            await ops_test.model.destroy_unit(replica)
            await ops_test.model.block_until(
                lambda: len(ops_test.model.applications[database_app_name].units) == 1
            )

        for unit in ops_test.model.applications[database_app_name].units:
            remaining_unit = unit
            break

        # Run the "restore backup" action.
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                logger.info("restoring the backup")
                most_recent_backup = backups.split("\n")[-1]
                backup_id = most_recent_backup.split()[0]
                action = await remaining_unit.run_action("restore", **{"backup-id": backup_id})
                await action.wait()
                restore_status = action.results.get("restore-status")
                assert restore_status, "restore hasn't succeeded"

        # Wait for the restore to complete.
        async with ops_test.fast_forward():
            await ops_test.model.wait_for_idle(status="active", timeout=1000)

        # Check that the backup was correctly restored by having only the first created table.
        primary = await get_primary(ops_test, remaining_unit.name)
        address = get_unit_address(ops_test, primary)
        logger.info("checking that the backup was correctly restored")
        with db_connect(
            host=address, password=password
        ) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
            )
            assert cursor.fetchone()[
                0
            ], "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
            cursor.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
            )
            assert not cursor.fetchone()[
                0
            ], "backup wasn't correctly restored: table 'backup_table_2' exists"
        connection.close()

        # Remove the database app.
        await ops_test.model.remove_application(database_app_name, block_until_done=True)
    # Remove the TLS operator.
    await ops_test.model.remove_application(TLS_CERTIFICATES_APP_NAME, block_until_done=True)


@pytest.mark.group(1)
async def test_restore_on_new_cluster(ops_test: OpsTest, github_secrets) -> None:
    """Test that is possible to restore a backup to another PostgreSQL cluster."""
    charm = await ops_test.build_charm(".")
    database_app_name = f"new-{DATABASE_APP_NAME}"
    await ops_test.model.deploy(
        charm,
        application_name=database_app_name,
        series=CHARM_SERIES,
    )
    await ops_test.model.relate(database_app_name, S3_INTEGRATOR_APP_NAME)
    async with ops_test.fast_forward():
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
            most_recent_backup = backups.split("\n")[-1]
            backup_id = most_recent_backup.split()[0]
            action = await ops_test.model.units.get(unit_name).run_action(
                "restore", **{"backup-id": backup_id}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "restore hasn't succeeded"

    # Wait for the restore to complete.
    async with ops_test.fast_forward():
        await wait_for_idle_on_blocked(
            ops_test,
            database_app_name,
            0,
            S3_INTEGRATOR_APP_NAME,
            ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
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
        assert cursor.fetchone()[
            0
        ], "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
    connection.close()


@pytest.mark.group(1)
async def test_invalid_config_and_recovery_after_fixing_it(
    ops_test: OpsTest, cloud_configs: Tuple[Dict, Dict]
) -> None:
    """Test that the charm can handle invalid and valid backup configurations."""
    database_app_name = f"new-{DATABASE_APP_NAME}"

    # Provide invalid backup configurations.
    logger.info("configuring S3 integrator for an invalid cloud")
    await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(
        {
            "endpoint": "endpoint",
            "bucket": "bucket",
            "path": "path",
            "region": "region",
        }
    )
    action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
        "sync-s3-credentials",
        **{
            "access-key": "access-key",
            "secret-key": "secret-key",
        },
    )
    await action.wait()
    logger.info("waiting for the database charm to become blocked")
    await wait_for_idle_on_blocked(
        ops_test,
        database_app_name,
        0,
        S3_INTEGRATOR_APP_NAME,
        FAILED_TO_ACCESS_CREATE_BUCKET_ERROR_MESSAGE,
    )

    # Provide valid backup configurations, but from another cluster repository.
    logger.info(
        "configuring S3 integrator for a valid cloud, but with the path of another cluster repository"
    )
    await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(cloud_configs[0][AWS])
    action = await ops_test.model.units.get(f"{S3_INTEGRATOR_APP_NAME}/0").run_action(
        "sync-s3-credentials",
        **cloud_configs[1][AWS],
    )
    await action.wait()
    logger.info("waiting for the database charm to become blocked")
    await wait_for_idle_on_blocked(
        ops_test,
        database_app_name,
        0,
        S3_INTEGRATOR_APP_NAME,
        ANOTHER_CLUSTER_REPOSITORY_ERROR_MESSAGE,
    )

    # Provide valid backup configurations, with another path in the S3 bucket.
    logger.info("configuring S3 integrator for a valid cloud")
    config = cloud_configs[0][AWS].copy()
    config["path"] = f"/postgresql/{uuid.uuid1()}"
    await ops_test.model.applications[S3_INTEGRATOR_APP_NAME].set_config(config)
    logger.info("waiting for the database charm to become active")
    await ops_test.model.wait_for_idle(
        apps=[database_app_name, S3_INTEGRATOR_APP_NAME], status="active"
    )
