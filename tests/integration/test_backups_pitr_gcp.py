#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

from .conftest import GCP
from .helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
)
from .juju_ import juju_major_version

CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
if juju_major_version < 3:
    TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"
    TLS_CHANNEL = "legacy/stable"
    TLS_CONFIG = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    TLS_CERTIFICATES_APP_NAME = "self-signed-certificates"
    TLS_CHANNEL = "1/stable"
    TLS_CONFIG = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)


async def pitr_backup_operations(
    ops_test: OpsTest,
    s3_integrator_app_name: str,
    tls_certificates_app_name: str,
    tls_config,
    tls_channel,
    credentials,
    cloud,
    config,
    charm,
) -> None:
    """Basic set of operations for PITR backup and timelines management testing.

    Below is presented algorithm in the next format: "(timeline): action_1 -> action_2".
    1: table -> backup_b1 -> test_data_td1 -> timestamp_ts1 -> test_data_td2 -> restore_ts1 => 2
    2: check_td1 -> check_not_td2 -> test_data_td3 -> restore_b1_latest => 3
    3: check_td1 -> check_td2 -> check_not_td3 -> test_data_td4 -> restore_t2_latest => 4
    4: check_td1 -> check_not_td2 -> check_td3 -> check_not_td4
    """
    # Set-up environment
    database_app_name = f"{DATABASE_APP_NAME}-{cloud.lower()}"

    logger.info("deploying the next charms: s3-integrator, self-signed-certificates, postgresql")
    await ops_test.model.deploy(s3_integrator_app_name)
    await ops_test.model.deploy(tls_certificates_app_name, config=tls_config, channel=tls_channel)
    await ops_test.model.deploy(
        charm,
        application_name=database_app_name,
        num_units=2,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )

    logger.info(
        "integrating self-signed-certificates with postgresql and waiting them to stabilize"
    )
    await ops_test.model.relate(
        f"{database_app_name}:certificates", f"{tls_certificates_app_name}:certificates"
    )
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, tls_certificates_app_name], status="active", timeout=1000
        )

    logger.info(f"configuring s3-integrator for {cloud}")
    await ops_test.model.applications[s3_integrator_app_name].set_config(config)
    action = await ops_test.model.units.get(f"{s3_integrator_app_name}/0").run_action(
        "sync-s3-credentials",
        **credentials,
    )
    await action.wait()

    logger.info("integrating s3-integrator with postgresql and waiting model to stabilize")
    await ops_test.model.relate(database_app_name, s3_integrator_app_name)
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    primary = await get_primary(ops_test, f"{database_app_name}/0")
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != primary:
            replica = unit.name
            break
    password = await get_password(ops_test, primary)
    address = get_unit_address(ops_test, primary)

    logger.info("1: creating table")
    _create_table(address, password)

    logger.info("1: creating backup b1")
    action = await ops_test.model.units.get(replica).run_action("create-backup")
    await action.wait()
    backup_status = action.results.get("backup-status")
    assert backup_status, "backup hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)
    backup_b1 = await _get_most_recent_backup(ops_test, ops_test.model.units.get(replica))

    logger.info("1: creating test data td1")
    _insert_test_data("test_data_td1", address, password)

    logger.info("1: get timestamp ts1")
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_timestamp;")
        timestamp_ts1 = str(cursor.fetchone()[0])
    connection.close()
    # Wrong timestamp pointing to one year ahead
    unreachable_timestamp_ts1 = timestamp_ts1.replace(
        timestamp_ts1[:4], str(int(timestamp_ts1[:4]) + 1), 1
    )

    logger.info("1: creating test data td2")
    _insert_test_data("test_data_td2", address, password)

    logger.info("1: switching wal")
    _switch_wal(address, password)

    logger.info("1: scaling down to do restore")
    async with ops_test.fast_forward():
        await ops_test.model.destroy_unit(replica)
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
    for unit in ops_test.model.applications[database_app_name].units:
        remaining_unit = unit
        break

    logger.info("1: restoring the backup b1 with bad restore-to-time parameter")
    action = await remaining_unit.run_action(
        "restore", **{"backup-id": backup_b1, "restore-to-time": "bad data"}
    )
    await action.wait()
    assert action.status == "failed", (
        "1: restore must fail with bad restore-to-time parameter, but that action succeeded"
    )

    logger.info("1: restoring the backup b1 with unreachable restore-to-time parameter")
    action = await remaining_unit.run_action(
        "restore", **{"backup-id": backup_b1, "restore-to-time": unreachable_timestamp_ts1}
    )
    await action.wait()
    logger.info("1: waiting for the database charm to become blocked after restore")
    async with ops_test.fast_forward():
        await ops_test.model.block_until(
            lambda: remaining_unit.workload_status_message == CANNOT_RESTORE_PITR,
            timeout=1000,
        )
    logger.info(
        "1: database charm become in blocked state after restore, as supposed to be with unreachable PITR parameter"
    )

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("1: restoring to the timestamp ts1")
            action = await remaining_unit.run_action(
                "restore", **{"restore-to-time": timestamp_ts1}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "1: restore to the timestamp ts1 hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("2: successful restore")
    primary = await get_primary(ops_test, remaining_unit.name)
    address = get_unit_address(ops_test, primary)
    timeline_t2 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert backup_b1 != timeline_t2, "2: timeline 2 do not exist in list-backups action or bad"

    logger.info("2: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "2: test data td1 should exist"

    logger.info("2: checking not test data td2")
    assert not _check_test_data("test_data_td2", address, password), (
        "2: test data td2 shouldn't exist"
    )

    logger.info("2: creating test data td3")
    _insert_test_data("test_data_td3", address, password)

    logger.info("2: get timestamp ts2")
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_timestamp;")
        timestamp_ts2 = str(cursor.fetchone()[0])
    connection.close()

    logger.info("2: creating test data td4")
    _insert_test_data("test_data_td4", address, password)

    logger.info("2: switching wal")
    _switch_wal(address, password)

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("2: restoring the backup b1 to the latest")
            action = await remaining_unit.run_action(
                "restore", **{"backup-id": backup_b1, "restore-to-time": "latest"}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "2: restore the backup b1 to the latest hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("3: successful restore")
    primary = await get_primary(ops_test, remaining_unit.name)
    address = get_unit_address(ops_test, primary)
    timeline_t3 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert backup_b1 != timeline_t3 and timeline_t2 != timeline_t3, (
        "3: timeline 3 do not exist in list-backups action or bad"
    )

    logger.info("3: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "3: test data td1 should exist"

    logger.info("3: checking test data td2")
    assert _check_test_data("test_data_td2", address, password), "3: test data td2 should exist"

    logger.info("3: checking not test data td3")
    assert not _check_test_data("test_data_td3", address, password), (
        "3: test data td3 shouldn't exist"
    )

    logger.info("3: checking not test data td4")
    assert not _check_test_data("test_data_td4", address, password), (
        "3: test data td4 shouldn't exist"
    )

    logger.info("3: switching wal")
    _switch_wal(address, password)

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("3: restoring the timeline 2 to the latest")
            action = await remaining_unit.run_action(
                "restore", **{"backup-id": timeline_t2, "restore-to-time": "latest"}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "3: restore the timeline 2 to the latest hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("4: successful restore")
    primary = await get_primary(ops_test, remaining_unit.name)
    address = get_unit_address(ops_test, primary)
    timeline_t4 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert (
        backup_b1 != timeline_t4 and timeline_t2 != timeline_t4 and timeline_t3 != timeline_t4
    ), "4: timeline 4 do not exist in list-backups action or bad"

    logger.info("4: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "4: test data td1 should exist"

    logger.info("4: checking not test data td2")
    assert not _check_test_data("test_data_td2", address, password), (
        "4: test data td2 shouldn't exist"
    )

    logger.info("4: checking test data td3")
    assert _check_test_data("test_data_td3", address, password), "4: test data td3 should exist"

    logger.info("4: checking test data td4")
    assert _check_test_data("test_data_td4", address, password), "4: test data td4 should exist"

    logger.info("4: switching wal")
    _switch_wal(address, password)

    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("4: restoring to the timestamp ts2")
            action = await remaining_unit.run_action(
                "restore", **{"restore-to-time": timestamp_ts2}
            )
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "4: restore to the timestamp ts2 hasn't succeeded"
    await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)

    logger.info("5: successful restore")
    primary = await get_primary(ops_test, remaining_unit.name)
    address = get_unit_address(ops_test, primary)
    timeline_t5 = await _get_most_recent_backup(ops_test, remaining_unit)
    assert (
        backup_b1 != timeline_t5
        and timeline_t2 != timeline_t5
        and timeline_t3 != timeline_t5
        and timeline_t4 != timeline_t5
    ), "5: timeline 5 do not exist in list-backups action or bad"

    logger.info("5: checking test data td1")
    assert _check_test_data("test_data_td1", address, password), "5: test data td1 should exist"

    logger.info("5: checking not test data td2")
    assert not _check_test_data("test_data_td2", address, password), (
        "5: test data td2 shouldn't exist"
    )

    logger.info("5: checking test data td3")
    assert _check_test_data("test_data_td3", address, password), "5: test data td3 should exist"

    logger.info("5: checking not test data td4")
    assert not _check_test_data("test_data_td4", address, password), (
        "5: test data td4 shouldn't exist"
    )

    # Remove the database app.
    await ops_test.model.remove_application(database_app_name, block_until_done=True)
    # Remove the TLS operator.
    await ops_test.model.remove_application(tls_certificates_app_name, block_until_done=True)


@pytest.mark.abort_on_fail
async def test_pitr_backup_gcp(
    ops_test: OpsTest, gcp_cloud_configs: tuple[dict, dict], charm
) -> None:
    """Build, deploy two units of PostgreSQL and do backup in GCP. Then, write new data into DB, switch WAL file and test point-in-time-recovery restore action."""
    config, credentials = gcp_cloud_configs

    await pitr_backup_operations(
        ops_test,
        S3_INTEGRATOR_APP_NAME,
        TLS_CERTIFICATES_APP_NAME,
        TLS_CONFIG,
        TLS_CHANNEL,
        credentials,
        GCP,
        config,
        charm,
    )


def _create_table(host: str, password: str):
    with db_connect(host=host, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE IF NOT EXISTS backup_table (test_column TEXT);")
    connection.close()


def _insert_test_data(td: str, host: str, password: str):
    with db_connect(host=host, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute(
            "INSERT INTO backup_table (test_column) VALUES (%s);",
            (td,),
        )
    connection.close()


def _check_test_data(td: str, host: str, password: str) -> bool:
    with db_connect(host=host, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM backup_table WHERE test_column = %s);",
            (td,),
        )
        res = cursor.fetchone()[0]
    connection.close()
    return res


def _switch_wal(host: str, password: str):
    with db_connect(host=host, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("SELECT pg_switch_wal();")
    connection.close()


async def _get_most_recent_backup(ops_test: OpsTest, unit: any) -> str:
    logger.info("listing the available backups")
    action = await unit.run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    assert backups, "backups not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)
    most_recent_backup = backups.split("\n")[-1]
    return most_recent_backup.split()[0]
