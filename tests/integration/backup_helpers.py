#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_exponential

from .helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
)

logger = logging.getLogger(__name__)

CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"


async def backup_deploy(
    ops_test: OpsTest,
    s3_integrator_app_name: str,
    tls_certificates_app_name: str | None,
    tls_channel,
    credentials,
    cloud,
    config,
    charm,
) -> str:
    use_tls = all([tls_certificates_app_name, tls_channel])
    # Deploy S3 Integrator and TLS Certificates Operator.
    await ops_test.model.deploy(s3_integrator_app_name)
    if use_tls:
        await ops_test.model.deploy(tls_certificates_app_name, channel=tls_channel)

    # Deploy and relate PostgreSQL to S3 integrator (one database app for each cloud for now
    # as archive_mode is disabled after restoring the backup) and to TLS Certificates Operator
    # (to be able to create backups from replicas).
    database_app_name = f"{DATABASE_APP_NAME}-{cloud.lower()}"
    await ops_test.model.deploy(
        charm,
        application_name=database_app_name,
        num_units=2,
        base=CHARM_BASE,
        config={"profile": "testing"},
    )

    if use_tls:
        await ops_test.model.relate(
            f"{database_app_name}:client-certificates", f"{tls_certificates_app_name}:certificates"
        )
        await ops_test.model.relate(
            f"{database_app_name}:peer-certificates", f"{tls_certificates_app_name}:certificates"
        )
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(apps=[database_app_name], status="active", timeout=1000)

    # Configure and set access and secret keys.
    logger.info(f"configuring S3 integrator for {cloud}")
    await ops_test.model.applications[s3_integrator_app_name].set_config(config)
    action = await ops_test.model.units.get(f"{s3_integrator_app_name}/0").run_action(
        "sync-s3-credentials",
        **credentials,
    )
    await action.wait()

    await ops_test.model.relate(database_app_name, s3_integrator_app_name)
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[database_app_name, s3_integrator_app_name], status="active", timeout=1500
        )
    return database_app_name


async def backup_operations(
    ops_test: OpsTest,
    s3_integrator_app_name: str,
    tls_certificates_app_name: str | None,
    tls_channel,
    credentials,
    cloud,
    config,
    charm,
) -> None:
    """Basic set of operations for backup testing in different cloud providers."""
    database_app_name = await backup_deploy(
        ops_test,
        s3_integrator_app_name,
        tls_certificates_app_name,
        tls_channel,
        credentials,
        cloud,
        config,
        charm,
    )

    primary = await get_primary(ops_test, f"{database_app_name}/0")
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != primary:
            replica = unit.name
            break

    # Write some data.
    password = await get_password(ops_test, database_app_name=database_app_name)
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
        apps=[database_app_name, s3_integrator_app_name], status="active", timeout=1000
    )

    # With a stable cluster, Run the "create backup" action
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000, idle_period=30)
    logger.info("listing the available backups")
    action = await ops_test.model.units.get(replica).run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    # 5 lines for header output, 1 backup line ==> 6 total lines
    assert len(backups.split("\n")) == 6, "full backup is not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Write some data.
    logger.info("creating a second table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE backup_table_2 (test_collumn INT );")
    connection.close()

    # Run the "create backup" action.
    logger.info("creating a backup")
    action = await ops_test.model.units.get(replica).run_action(
        "create-backup", **{"type": "differential"}
    )
    await action.wait()
    backup_status = action.results.get("backup-status")
    assert backup_status, "backup hasn't succeeded"
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Run the "list backups" action.
    logger.info("listing the available backups")
    action = await ops_test.model.units.get(replica).run_action("list-backups")
    await action.wait()
    backups = action.results.get("backups")
    # 5 lines for header output, 2 backup lines ==> 7 total lines
    assert len(backups.split("\n")) == 7, "differential backup is not outputted"
    await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Write some data.
    logger.info("creating a second table in the database")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE backup_table_3 (test_collumn INT );")
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

    # Run the "restore backup" action for differential backup.
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("restoring the backup")
            last_diff_backup = backups.split("\n")[-1]
            backup_id = last_diff_backup.split()[0]
            action = await remaining_unit.run_action("restore", **{"backup-id": backup_id})
            await action.wait()
            restore_status = action.results.get("restore-status")
            assert restore_status, "restore hasn't succeeded"

    # Wait for the restore to complete.
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(status="active", timeout=1000)

    # Check that the backup was correctly restored by having only the first created table.
    logger.info("checking that the backup was correctly restored")
    primary = await get_primary(ops_test, remaining_unit.name)
    address = get_unit_address(ops_test, primary)
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_2' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_3');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_3' exists"
        )
    connection.close()

    # Run the "restore backup" action for full backup.
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            logger.info("restoring the backup")
            last_full_backup = backups.split("\n")[-2]
            backup_id = last_full_backup.split()[0]
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
    with db_connect(host=address, password=password) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_1');"
        )
        assert cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_1' doesn't exist"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_2');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_2' exists"
        )
        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = 'backup_table_3');"
        )
        assert not cursor.fetchone()[0], (
            "backup wasn't correctly restored: table 'backup_table_3' exists"
        )
    connection.close()


async def pitr_backup_operations(
    ops_test: OpsTest,
    s3_integrator_app_name: str,
    tls_certificates_app_name: str | None,
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
    use_tls = all([tls_certificates_app_name, tls_channel])
    database_app_name = await backup_deploy(
        ops_test,
        s3_integrator_app_name,
        tls_certificates_app_name,
        tls_channel,
        credentials,
        cloud,
        config,
        charm,
    )

    primary = await get_primary(ops_test, f"{database_app_name}/0")
    for unit in ops_test.model.applications[database_app_name].units:
        if unit.name != primary:
            replica = unit.name
            break
    password = await get_password(ops_test, database_app_name=database_app_name)
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
    if use_tls:
        # Remove the TLS operator.
        await ops_test.model.remove_application(tls_certificates_app_name, block_until_done=True)


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
