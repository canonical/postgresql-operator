# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import subprocess
from pathlib import Path
from typing import Optional

import psycopg2
import requests
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import RetryError, Retrying, stop_after_delay, wait_fixed

from tests.integration.helpers import get_unit_address

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
PORT = 5432
APP_NAME = METADATA["name"]
PATRONI_SERVICE_DEFAULT_PATH = "/etc/systemd/system/patroni.service"
TMP_SERVICE_PATH = "tests/integration/ha_tests/tmp.service"
RESTART_DELAY = 60 * 3
ORIGINAL_RESTART_DELAY = 30


class MemberNotListedOnClusterError(Exception):
    """Raised when a member is not listed in the cluster."""


class MemberNotUpdatedOnClusterError(Exception):
    """Raised when a member is not yet updated in the cluster."""


class ProcessError(Exception):
    pass


async def app_name(ops_test: OpsTest, application_name: str = "postgresql") -> Optional[str]:
    """Returns the name of the cluster running PostgreSQL.

    This is important since not all deployments of the PostgreSQL charm have the application name
    "postgresql".

    Note: if multiple clusters are running PostgreSQL this will return the one first found.
    """
    status = await ops_test.model.get_status()
    for app in ops_test.model.applications:
        if application_name in status["applications"][app]["charm"]:
            return app

    return None


async def change_master_start_timeout(ops_test: OpsTest, seconds: Optional[int]) -> None:
    """Change master start timeout configuration.

    Args:
        ops_test: ops_test instance.
        seconds: number of seconds to set in master_start_timeout configuration.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            app = await app_name(ops_test)
            primary_name = await get_primary(ops_test, app)
            unit_ip = get_unit_address(ops_test, primary_name)
            requests.patch(
                f"http://{unit_ip}:8008/config",
                json={"master_start_timeout": seconds},
            )


async def change_wal_settings(
    ops_test: OpsTest, unit_name: str, max_wal_size: int, min_wal_size, wal_keep_segments
) -> None:
    """Change wal_keep_segments configuration.

    Args:
        ops_test: ops_test instance.
        unit_name: name of the unit to change wal_keep_segments configuration.
        max_wal_size: maximum amount of WAL to keep (MB).
        min_wal_size: minimum amount of WAL to keep (MB).
        wal_keep_segments: number of WAL segments to keep.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            unit_ip = get_unit_address(ops_test, unit_name)
            requests.patch(
                f"http://{unit_ip}:8008/config",
                json={
                    "postgresql": {
                        "parameters": {
                            "max_wal_size": max_wal_size,
                            "min_wal_size": min_wal_size,
                            "wal_keep_segments": wal_keep_segments,
                        }
                    }
                },
            )


async def count_writes(ops_test: OpsTest, down_unit: str = None) -> int:
    """Count the number of writes in the database."""
    app = await app_name(ops_test)
    password = await get_password(ops_test, app, down_unit)
    for unit in ops_test.model.applications[app].units:
        if unit.name != down_unit:
            host = unit.public_address
            break
    connection_string = (
        f"dbname='application' user='operator'"
        f" host='{host}' password='{password}' connect_timeout=10"
    )
    try:
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                with psycopg2.connect(
                    connection_string
                ) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(number) FROM continuous_writes;")
                    count = cursor.fetchone()[0]
                connection.close()
    except RetryError:
        return -1
    return count


async def fetch_cluster_members(ops_test: OpsTest):
    """Fetches the IPs listed by Patroni as cluster members.

    Args:
        ops_test: OpsTest instance.
    """
    app = await app_name(ops_test)
    member_ips = {}
    for unit in ops_test.model.applications[app].units:
        cluster_info = requests.get(f"http://{unit.public_address}:8008/cluster")
        if len(member_ips) > 0:
            # If the list of members IPs was already fetched, also compare the
            # list provided by other members.
            assert member_ips == {
                member["host"] for member in cluster_info.json()["members"]
            }, "members report different lists of cluster members."
        else:
            member_ips = {member["host"] for member in cluster_info.json()["members"]}
    return member_ips


async def get_master_start_timeout(ops_test: OpsTest) -> Optional[int]:
    """Get the master start timeout configuration.

    Args:
        ops_test: ops_test instance.

    Returns:
        master start timeout in seconds or None if it's using the default value.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            app = await app_name(ops_test)
            primary_name = await get_primary(ops_test, app)
            unit_ip = get_unit_address(ops_test, primary_name)
            configuration_info = requests.get(f"http://{unit_ip}:8008/config")
            master_start_timeout = configuration_info.json().get("master_start_timeout")
            return int(master_start_timeout) if master_start_timeout is not None else None


async def get_password(ops_test: OpsTest, app: str, down_unit: str = None) -> str:
    """Use the charm action to retrieve the password from provided application.

    Returns:
        string with the password stored on the peer relation databag.
    """
    # Can retrieve from any unit running unit, so we pick the first.
    for unit in ops_test.model.applications[app].units:
        if unit.name != down_unit:
            unit_name = unit.name
            break
    action = await ops_test.model.units.get(unit_name).run_action("get-password")
    action = await action.wait()
    return action.results["operator-password"]


def is_replica(ops_test: OpsTest, unit_name: str) -> bool:
    """Returns whether the unit a replica in the cluster."""
    unit_ip = get_unit_address(ops_test, unit_name)
    member_name = unit_name.replace("/", "-")

    try:
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with attempt:
                cluster_info = requests.get(f"http://{unit_ip}:8008/cluster")

                # The unit may take some time to be listed on Patroni REST API cluster endpoint.
                if member_name not in {
                    member["name"] for member in cluster_info.json()["members"]
                }:
                    raise MemberNotListedOnClusterError()

                for member in cluster_info.json()["members"]:
                    if member["name"] == member_name:
                        role = member["role"]

                # A member that restarted has the DB process stopped may
                # take some time to know that a new primary was elected.
                if role == "replica":
                    return True
                else:
                    raise MemberNotUpdatedOnClusterError()
    except RetryError:
        return False


async def get_primary(ops_test: OpsTest, app) -> str:
    """Use the charm action to retrieve the primary from provided application.

    Returns:
        primary unit name.
    """
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        # Can retrieve from any unit running unit, so we pick the first.
        unit_name = ops_test.model.applications[app].units[0].name
        action = await ops_test.model.units.get(unit_name).run_action("get-primary")
        action = await action.wait()
        assert action.results["primary"] is not None
        return action.results["primary"]


async def list_wal_files(ops_test: OpsTest, app: str):
    units = [unit.name for unit in ops_test.model.applications[app].units]
    command = "ls -1 /var/lib/postgresql/data/pgdata/pg_wal/"
    files = {}
    for unit in units:
        complete_command = f"run --unit {unit} -- {command}"
        return_code, stdout, stderr = await ops_test.juju(*complete_command.split())
        files[unit] = stdout.splitlines()
        files[unit] = [
            i for i in files[unit] if ".history" not in i and i != "" and i != "archive_status"
        ]
        files[unit].append("archive_status/*")
    return files


async def send_signal_to_process(
    ops_test: OpsTest, unit_name: str, process: str, kill_code: str
) -> None:
    """Kills process on the unit according to the provided kill code."""
    # Killing the only instance can be disastrous.
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1000)

    kill_cmd = f"run --unit {unit_name} -- pkill --signal {kill_code} -f {process}"
    return_code, _, _ = await ops_test.juju(*kill_cmd.split())

    if return_code != 0:
        raise ProcessError(
            "Expected kill command %s to succeed instead it failed: %s", kill_cmd, return_code
        )


async def postgresql_ready(ops_test, unit_name: str) -> bool:
    """Verifies a PostgreSQL instance is running and available."""
    unit_ip = get_unit_address(ops_test, unit_name)
    try:
        for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3)):
            with attempt:
                instance_health_info = requests.get(f"http://{unit_ip}:8008/health")
                assert instance_health_info.status_code == 200
    except RetryError:
        return False

    return True


async def secondary_up_to_date(ops_test: OpsTest, unit_name: str, expected_writes: int) -> bool:
    """Checks if secondary is up-to-date with the cluster.

    Retries over the period of one minute to give secondary adequate time to copy over data.
    """
    app = await app_name(ops_test)
    password = await get_password(ops_test, app)
    host = [
        unit.public_address
        for unit in ops_test.model.applications[app].units
        if unit.name == unit_name
    ][0]
    connection_string = (
        f"dbname='application' user='operator'"
        f" host='{host}' password='{password}' connect_timeout=10"
    )

    try:
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with attempt:
                with psycopg2.connect(
                    connection_string
                ) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(number) FROM continuous_writes;")
                    secondary_writes = cursor.fetchone()[0]
                    assert secondary_writes == expected_writes
    except RetryError:
        return False
    finally:
        connection.close()

    return True


async def start_continuous_writes(ops_test: OpsTest, app: str) -> None:
    """Start continuous writes to PostgreSQL."""
    # Start the process by relating the application to the database or
    # by calling the action if the relation already exists.
    relations = [
        relation
        for relation in ops_test.model.applications[app].relations
        if not relation.is_peer
        and f"{relation.requires.application_name}:{relation.requires.name}"
        == "application:database"
    ]
    if not relations:
        await ops_test.model.relate(app, "application")
        await ops_test.model.wait_for_idle(status="active", timeout=1000)
    else:
        action = (
            await ops_test.model.applications["application"]
            .units[0]
            .run_action("start-continuous-writes")
        )
        await action.wait()


async def stop_continuous_writes(ops_test: OpsTest) -> int:
    """Stops continuous writes to PostgreSQL and returns the last written value."""
    action = (
        await ops_test.model.applications["application"]
        .units[0]
        .run_action("stop-continuous-writes")
    )
    action = await action.wait()
    return int(action.results["writes"])


async def update_restart_delay(ops_test: OpsTest, unit, delay: int):
    """Updates the restart delay in the DB service file.

    When the DB service fails it will now wait for `delay` number of seconds.
    """
    # Load the service file from the unit and update it with the new delay.
    await unit.scp_from(source=PATRONI_SERVICE_DEFAULT_PATH, destination=TMP_SERVICE_PATH)
    with open(TMP_SERVICE_PATH, "r") as patroni_service_file:
        patroni_service = patroni_service_file.readlines()

    for index, line in enumerate(patroni_service):
        if "RestartSec" in line:
            patroni_service[index] = f"RestartSec={delay}s\n"

    with open(TMP_SERVICE_PATH, "w") as service_file:
        service_file.writelines(patroni_service)

    # Upload the changed file back to the unit, we cannot scp this file directly to
    # PATRONI_SERVICE_DEFAULT_PATH since this directory has strict permissions, instead we scp it
    # elsewhere and then move it to PATRONI_SERVICE_DEFAULT_PATH.
    await unit.scp_to(source=TMP_SERVICE_PATH, destination="patroni.service")
    mv_cmd = (
        f"run --unit {unit.name} mv /home/ubuntu/patroni.service {PATRONI_SERVICE_DEFAULT_PATH}"
    )
    return_code, _, _ = await ops_test.juju(*mv_cmd.split())
    if return_code != 0:
        raise ProcessError("Command: %s failed on unit: %s.", mv_cmd, unit.name)

    # Remove temporary file from machine.
    subprocess.call(["rm", TMP_SERVICE_PATH])

    # Reload the daemon for systemd otherwise changes are not saved.
    reload_cmd = f"run --unit {unit.name} systemctl daemon-reload"
    return_code, _, _ = await ops_test.juju(*reload_cmd.split())
    if return_code != 0:
        raise ProcessError("Command: %s failed on unit: %s.", reload_cmd, unit.name)
