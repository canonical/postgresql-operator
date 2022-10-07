# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
from pathlib import Path
from typing import Optional

import psycopg2
import requests
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import (
    RetryError,
    Retrying,
    stop_after_attempt,
    stop_after_delay,
    wait_fixed,
)

from tests.integration.helpers import get_unit_address

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
PORT = 5432
APP_NAME = METADATA["name"]


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


async def count_writes(ops_test: OpsTest) -> int:
    """Count the number of writes in the database."""
    app = await app_name(ops_test)
    print(11)
    password = await get_password(ops_test, app)
    print(12)
    try:
        for attempt in Retrying(
            stop=stop_after_attempt(len(ops_test.model.applications[app].units))
        ):
            with attempt:
                host = (
                    ops_test.model.applications[app]
                    .units[attempt.retry_state.attempt_number - 1]
                    .public_address
                )
                print(13)
                connection_string = (
                    f"dbname='application' user='operator'"
                    f" host='{host}' password='{password}' connect_timeout=10"
                )
                print(14)
                with psycopg2.connect(
                    connection_string
                ) as connection, connection.cursor() as cursor:
                    print(15)
                    cursor.execute("SELECT COUNT(number) FROM continuous_writes;")
                    print(16)
                    count = cursor.fetchone()[0]
                    print(17)
                connection.close()
                print(18)
    except RetryError:
        return -1
    except Exception as e:
        print(str(e))
        return -1
    return count


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


async def get_password(ops_test: OpsTest, app) -> str:
    """Use the charm action to retrieve the password from provided application.

    Returns:
        string with the password stored on the peer relation databag.
    """
    # Can retrieve from any unit running unit, so we pick the first.
    for attempt in Retrying(stop=stop_after_attempt(len(ops_test.model.applications[app].units))):
        with attempt:
            unit_name = (
                ops_test.model.applications[app].units[attempt.retry_state.attempt_number - 1].name
            )
            print(f"unit_name: {unit_name}")
            action = await ops_test.model.units.get(unit_name).run_action("get-password")
            action = await asyncio.wait_for(action.wait(), 10)
            return action.results["operator-password"]


async def get_primary(ops_test: OpsTest, app) -> str:
    """Use the charm action to retrieve the primary from provided application.

    Returns:
        string with the password stored on the peer relation databag.
    """
    # Can retrieve from any unit running unit, so we pick the first.
    unit_name = ops_test.model.applications[app].units[0].name
    action = await ops_test.model.units.get(unit_name).run_action("get-primary")
    action = await action.wait()
    return action.results["primary"]


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
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
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
