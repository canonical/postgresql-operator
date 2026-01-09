# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import contextlib
import json
import logging
import os
import random
import subprocess
from pathlib import Path
from tempfile import mkstemp

import psycopg2
import requests
import yaml
from juju.model import Model
from pytest_operator.plugin import OpsTest
from tenacity import (
    RetryError,
    Retrying,
    retry,
    stop_after_attempt,
    stop_after_delay,
    wait_fixed,
)

from ..helpers import (
    APPLICATION_NAME,
    db_connect,
    execute_query_on_unit,
    get_patroni_cluster,
    get_unit_address,
    run_command_on_unit,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
PORT = 5432
APP_NAME = METADATA["name"]
SERVICE_NAME = "snap.charmed-postgresql.patroni.service"
PATRONI_SERVICE_DEFAULT_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
RESTART_CONDITION = "no"
ORIGINAL_RESTART_CONDITION = "always"


class MemberNotListedOnClusterError(Exception):
    """Raised when a member is not listed in the cluster."""


class MemberNotUpdatedOnClusterError(Exception):
    """Raised when a member is not yet updated in the cluster."""


class ProcessError(Exception):
    """Raised when a process fails."""


class ProcessRunningError(Exception):
    """Raised when a process is running when it is not expected to be."""


async def are_all_db_processes_down(ops_test: OpsTest, process: str, signal: str) -> bool:
    """Verifies that all units of the charm do not have the DB process running."""
    app = await app_name(ops_test)
    pgrep_cmd = ("pgrep", "-f", process) if "/" in process else ("pgrep", "-x", process)

    try:
        for attempt in Retrying(stop=stop_after_delay(400), wait=wait_fixed(3)):
            with attempt:
                for unit in ops_test.model.applications[app].units:
                    _, processes, _ = await ops_test.juju("ssh", unit.name, *pgrep_cmd)

                    # Splitting processes by "\n" results in one or more empty lines, hence we
                    # need to process these lines accordingly.
                    processes = [proc for proc in processes.split("\n") if len(proc) > 0]

                    # If something was returned, there is a running process.
                    if len(processes) > 0:
                        logger.info(f"Unit {unit.name} not yet down")
                        # Try to rekill the unit
                        await send_signal_to_process(ops_test, unit.name, process, signal)
                        raise ProcessRunningError
    except RetryError:
        return False

    return True


async def are_writes_increasing(
    ops_test,
    down_unit: str | None = None,
    use_ip_from_inside: bool = False,
    extra_model: Model = None,
) -> None:
    """Verify new writes are continuing by counting the number of writes."""
    down_units = [down_unit] if isinstance(down_unit, str) or not down_unit else down_unit
    writes, _ = await count_writes(
        ops_test,
        down_unit=down_units[0],
        use_ip_from_inside=use_ip_from_inside,
        extra_model=extra_model,
    )
    logger.info(f"Initial writes {writes}")

    for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3), reraise=True):
        with attempt:
            more_writes, _ = await count_writes(
                ops_test,
                down_unit=down_units[0],
                use_ip_from_inside=use_ip_from_inside,
                extra_model=extra_model,
            )
            logger.info(f"Retry writes {more_writes}")
            for member, count in writes.items():
                if "/".join(member.split(".", 1)[-1].rsplit("-", 1)) in down_units:
                    continue
                assert more_writes[member] > count, (
                    f"{member}: writes not continuing to DB (current writes: {more_writes[member]} - previous writes: {count})"
                )


async def app_name(
    ops_test: OpsTest, application_name: str = "postgresql", model: Model = None
) -> str | None:
    """Returns the name of the cluster running PostgreSQL.

    This is important since not all deployments of the PostgreSQL charm have the application name
    "postgresql".

    Note: if multiple clusters are running PostgreSQL this will return the one first found.
    """
    if model is None:
        model = ops_test.model
    status = await model.get_status()
    for app in model.applications:
        if (
            application_name in status["applications"][app]["charm"]
            and APPLICATION_NAME not in status["applications"][app]["charm"]
        ):
            return app

    return None


async def change_patroni_setting(
    ops_test: OpsTest,
    setting: str,
    value: int | bool,
    password: str,
    use_random_unit: bool = False,
    tls: bool = False,
) -> None:
    """Change the value of one of the Patroni settings.

    Args:
        ops_test: ops_test instance.
        setting: the name of the setting.
        value: the value to assign to the setting.
        password: Patroni password.
        use_random_unit: whether to use a random unit (default is False,
            so it uses the primary).
        tls: if Patroni is serving using tls.
    """
    schema = "https" if tls else "http"
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            app = await app_name(ops_test)
            if use_random_unit:
                unit = get_random_unit(ops_test, app)
                unit_ip = get_unit_address(ops_test, unit)
            else:
                primary_name = await get_primary(ops_test, app)
                unit_ip = get_unit_address(ops_test, primary_name)
            requests.patch(
                f"{schema}://{unit_ip}:8008/config",
                json={setting: value},
                verify=not tls,
                auth=requests.auth.HTTPBasicAuth("patroni", password),
            )


async def change_wal_settings(
    ops_test: OpsTest,
    unit_name: str,
    max_wal_size: int,
    min_wal_size,
    wal_keep_segments,
    password: str,
) -> None:
    """Change WAL settings in the unit.

    Args:
        ops_test: ops_test instance.
        unit_name: name of the unit to change the WAL settings.
        max_wal_size: maximum amount of WAL to keep (MB).
        min_wal_size: minimum amount of WAL to keep (MB).
        wal_keep_segments: number of WAL segments to keep.
        password: Patroni password.
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
                auth=requests.auth.HTTPBasicAuth("patroni", password),
            )


async def is_cluster_updated(
    ops_test: OpsTest, primary_name: str, use_ip_from_inside: bool = False
) -> None:
    # Verify that the old primary is now a replica.
    logger.info("checking that the former primary is now a replica")
    assert await is_replica(ops_test, primary_name, use_ip_from_inside), (
        "there are more than one primary in the cluster."
    )

    # Verify that all units are part of the same cluster.
    logger.info("checking that all units are part of the same cluster")
    member_ips = await fetch_cluster_members(ops_test, use_ip_from_inside)
    app = primary_name.split("/")[0]
    ip_addresses = [
        await (
            get_ip_from_inside_the_unit(ops_test, unit.name)
            if use_ip_from_inside
            else get_unit_ip(ops_test, unit.name)
        )
        for unit in ops_test.model.applications[app].units
    ]
    assert set(member_ips) == set(ip_addresses), "not all units are part of the same cluster."

    # Verify that no writes to the database were missed after stopping the writes.
    logger.info("checking that no writes to the database were missed after stopping the writes")
    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            total_expected_writes = await check_writes(ops_test, use_ip_from_inside)

    # Verify that old primary is up-to-date.
    logger.info("checking that the former primary is up to date with the cluster after restarting")
    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True):
        with attempt:
            assert await is_secondary_up_to_date(
                ops_test, primary_name, total_expected_writes, use_ip_from_inside
            ), "secondary not up to date with the cluster after restarting."


async def check_writes(
    ops_test, use_ip_from_inside: bool = False, extra_model: Model = None
) -> int:
    """Gets the total writes from the test charm and compares to the writes from db."""
    total_expected_writes = await stop_continuous_writes(ops_test)
    actual_writes, max_number_written = await count_writes(
        ops_test, use_ip_from_inside=use_ip_from_inside, extra_model=extra_model
    )
    for member, count in actual_writes.items():
        print(
            f"member: {member}, count: {count}, max_number_written: {max_number_written[member]}, total_expected_writes: {total_expected_writes}"
        )
        assert count == max_number_written[member], (
            f"{member}: writes to the db were missed: count of actual writes different from the max number written."
        )
        assert total_expected_writes == count, f"{member}: writes to the db were missed."
    return total_expected_writes


async def count_writes(
    ops_test: OpsTest,
    down_unit: str | None = None,
    use_ip_from_inside: bool = False,
    extra_model: Model = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Count the number of writes in the database."""
    app = await app_name(ops_test)
    password = await get_password(ops_test, app, down_unit)
    members = []
    for model in [ops_test.model, extra_model]:
        if model is None:
            continue
        for unit in model.applications[app].units:
            if unit.name != down_unit:
                members_data = get_patroni_cluster(
                    await (
                        get_ip_from_inside_the_unit(ops_test, unit.name)
                        if use_ip_from_inside
                        else get_unit_ip(ops_test, unit.name)
                    )
                )["members"]
                for member_data in members_data:
                    member_data["model"] = model.info.name
                members.extend(members_data)
                break
    down_ips = []
    if down_unit:
        for unit in ops_test.model.applications[app].units:
            if unit.name == down_unit:
                down_ips.append(unit.public_address)
                down_ips.append(await get_unit_ip(ops_test, unit.name))
    return count_writes_on_members(members, password, down_ips)


def count_writes_on_members(members, password, down_ips) -> tuple[dict[str, int], dict[str, int]]:
    count = {}
    maximum = {}
    for member in members:
        if member["role"] != "replica" and member["host"] not in down_ips:
            host = member["host"]

            connection_string = (
                f"dbname='{APPLICATION_NAME.replace('-', '_')}_database' user='operator'"
                f" host='{host}' password='{password}' connect_timeout=10"
            )

            member_name = f"{member['model']}.{member['name']}"
            connection = None
            try:
                with (
                    psycopg2.connect(connection_string) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute("SELECT COUNT(number), MAX(number) FROM continuous_writes;")
                    results = cursor.fetchone()
                    count[member_name] = results[0]
                    maximum[member_name] = results[1]
            except psycopg2.Error:
                # Error raised when the connection is not possible.
                count[member_name] = -1
                maximum[member_name] = -1
            finally:
                if connection is not None:
                    connection.close()
    return count, maximum


def cut_network_from_unit(machine_name: str) -> None:
    """Cut network from a lxc container.

    Args:
        machine_name: lxc container hostname
    """
    # apply a mask (device type `none`)
    cut_network_command = f"lxc config device add {machine_name} eth0 none"
    subprocess.check_call(cut_network_command.split())


def cut_network_from_unit_without_ip_change(machine_name: str) -> None:
    """Cut network from a lxc container (without causing the change of the unit IP address).

    Args:
        machine_name: lxc container hostname
    """
    override_command = f"lxc config device override {machine_name} eth0"
    # Ignore if the interface was already overridden.
    with contextlib.suppress(subprocess.CalledProcessError):
        subprocess.check_call(override_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.egress=0kbit"
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.ingress=1kbit"
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.priority=10"
    subprocess.check_call(limit_set_command.split())


async def fetch_cluster_members(ops_test: OpsTest, use_ip_from_inside: bool = False):
    """Fetches the IPs listed by Patroni as cluster members.

    Args:
        ops_test: OpsTest instance.
        use_ip_from_inside: whether to use the IP from inside the unit.
    """
    app = await app_name(ops_test)
    member_ips = {}
    for unit in ops_test.model.applications[app].units:
        unit_ip = await (
            get_ip_from_inside_the_unit(ops_test, unit.name)
            if use_ip_from_inside
            else get_unit_ip(ops_test, unit.name)
        )
        cluster_info = requests.get(f"http://{unit_ip}:8008/cluster")
        if len(member_ips) > 0:
            # If the list of members IPs was already fetched, also compare the
            # list provided by other members.
            assert member_ips == {member["host"] for member in cluster_info.json()["members"]}, (
                "members report different lists of cluster members."
            )
        else:
            member_ips = {member["host"] for member in cluster_info.json()["members"]}
    return member_ips


async def get_controller_machine(ops_test: OpsTest) -> str:
    """Return controller machine hostname.

    Args:
        ops_test: The ops test framework instance

    Returns:
        Controller hostname (str)
    """
    _, raw_controller, _ = await ops_test.juju("show-controller")

    controller = yaml.safe_load(raw_controller.strip())

    return next(
        machine.get("instance-id")
        for machine in controller[ops_test.controller_name]["controller-machines"].values()
    )


async def get_ip_from_inside_the_unit(ops_test: OpsTest, unit_name: str) -> str:
    command = f"exec --unit {unit_name} -- hostname -I"
    return_code, stdout, stderr = await ops_test.juju(*command.split())
    if return_code != 0:
        raise ProcessError(
            "Expected command %s to succeed instead it failed: %s %s", command, return_code, stderr
        )
    return stdout.splitlines()[0].strip()


async def get_patroni_setting(ops_test: OpsTest, setting: str, tls: bool = False) -> int | None:
    """Get the value of one of the integer Patroni settings.

    Args:
        ops_test: ops_test instance.
        setting: the name of the setting.
        tls: if Patroni is serving using tls.

    Returns:
        the value of the configuration or None if it's using the default value.
    """
    schema = "https" if tls else "http"
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            app = await app_name(ops_test)
            primary_name = await get_primary(ops_test, app)
            unit_ip = get_unit_address(ops_test, primary_name)
            configuration_info = requests.get(f"{schema}://{unit_ip}:8008/config", verify=not tls)
            value = configuration_info.json().get(setting)
            return int(value) if value is not None else None


async def get_postgresql_parameter(ops_test: OpsTest, parameter_name: str) -> int | None:
    """Get the value of a PostgreSQL parameter from Patroni API.

    Args:
        ops_test: ops_test instance.
        parameter_name: the name of the parameter to get the value for.

    Returns:
        the value of the requested PostgreSQL parameter.
    """
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
        with attempt:
            app = await app_name(ops_test)
            primary_name = await get_primary(ops_test, app)
            unit_ip = get_unit_address(ops_test, primary_name)
            configuration_info = requests.get(f"http://{unit_ip}:8008/config")
            postgresql_dict = configuration_info.json().get("postgresql")
            if postgresql_dict is None:
                return None
            parameters = postgresql_dict.get("parameters")
            if parameters is None:
                return None
            parameter_value = parameters.get(parameter_name)
            return parameter_value


def get_random_unit(ops_test: OpsTest, app: str) -> str:
    """Returns a random unit name."""
    return random.choice(ops_test.model.applications[app].units).name


async def get_standby_leader(model: Model, application_name: str) -> str:
    """Get the standby leader name.

    Args:
        model: the model instance.
        application_name: the name of the application to get the value for.

    Returns:
        the name of the standby leader.
    """
    first_unit_ip = model.applications[application_name].units[0].public_address
    cluster = get_patroni_cluster(first_unit_ip)
    for member in cluster["members"]:
        if member["role"] == "standby_leader":
            return member["name"]


async def get_sync_standby(ops_test: OpsTest, model: Model, application_name: str) -> str:
    """Get the sync_standby name.

    Args:
        ops_test: the ops test instance.
        model: the model instance.
        application_name: the name of the application to get the value for.

    Returns:
        the name of the sync standby.
    """
    any_unit = model.applications[application_name].units[0].name
    first_unit_ip = await get_unit_ip(ops_test, any_unit, model)
    cluster = get_patroni_cluster(first_unit_ip)
    for member in cluster["members"]:
        if member["role"] == "sync_standby":
            return member["name"]


async def get_password(ops_test: OpsTest, app: str, down_unit: str | None = None) -> str:
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
    return action.results["password"]


async def get_unit_ip(ops_test: OpsTest, unit_name: str, model: Model = None) -> str:
    """Wrapper for getting unit ip.

    Args:
        ops_test: The ops test object passed into every test case
        unit_name: The name of the unit to get the address
        model: Optional model instance to use
    Returns:
        The (str) ip of the unit
    """
    if model is None:
        application = unit_name.split("/")[0]
        for unit in ops_test.model.applications[application].units:
            if unit.name == unit_name:
                break
        return await instance_ip(ops_test, unit.machine.hostname)
    else:
        return get_unit_address(ops_test, unit_name)


@retry(stop=stop_after_attempt(8), wait=wait_fixed(15), reraise=True)
async def is_connection_possible(
    ops_test: OpsTest, unit_name: str, use_ip_from_inside: bool = False
) -> bool:
    """Test a connection to a PostgreSQL server."""
    app = unit_name.split("/")[0]
    password = await get_password(ops_test, app, unit_name)
    address = await (
        get_ip_from_inside_the_unit(ops_test, unit_name)
        if use_ip_from_inside
        else get_unit_ip(ops_test, unit_name)
    )
    try:
        for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3)):
            with attempt:
                with (
                    db_connect(host=address, password=password) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute("SELECT 1;")
                    success = cursor.fetchone()[0] == 1
                connection.close()
                return success
    except (psycopg2.Error, RetryError):
        # Error raised when the connection is not possible.
        return False


def is_machine_reachable_from(origin_machine: str, target_machine: str) -> bool:
    """Test network reachability between hosts.

    Args:
        origin_machine: hostname of the machine to test connection from
        target_machine: hostname of the machine to test connection to
    """
    try:
        subprocess.check_call(f"lxc exec {origin_machine} -- ping -c 3 {target_machine}".split())
        return True
    except subprocess.CalledProcessError:
        return False


async def is_replica(ops_test: OpsTest, unit_name: str, use_ip_from_inside: bool = False) -> bool:
    """Returns whether the unit a replica in the cluster."""
    unit_ip = await (
        get_ip_from_inside_the_unit(ops_test, unit_name)
        if use_ip_from_inside
        else get_unit_ip(ops_test, unit_name)
    )
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
                if role != "leader":
                    return True
                else:
                    raise MemberNotUpdatedOnClusterError()
    except RetryError:
        return False


async def get_cluster_roles(
    ops_test: OpsTest, unit_name: str, use_ip_from_inside: bool = False
) -> dict[str, str | list[str] | None]:
    """Returns whether the unit a replica in the cluster."""
    unit_ip = await (
        get_ip_from_inside_the_unit(ops_test, unit_name)
        if use_ip_from_inside
        else get_unit_ip(ops_test, unit_name)
    )

    members = {"replicas": [], "primaries": [], "sync_standbys": []}
    cluster_info = requests.get(f"http://{unit_ip}:8008/cluster")
    member_list = cluster_info.json()["members"]
    logger.info(f"Cluster members are: {member_list}")
    for member in member_list:
        role = member["role"]
        name = "/".join(member["name"].rsplit("-", 1))
        if role == "leader":
            members["primaries"].append(name)
        elif role == "sync_standby":
            members["sync_standbys"].append(name)
        else:
            members["replicas"].append(name)

    return members


async def instance_ip(ops_test: OpsTest, instance: str) -> str:
    """Translate juju instance name to IP.

    Args:
        ops_test: pytest ops test helper
        instance: The name of the instance

    Returns:
        The (str) IP address of the instance
    """
    _, output, _ = await ops_test.juju("machines")

    for line in output.splitlines():
        if instance in line:
            return line.split()[2]


async def get_primary(ops_test: OpsTest, app, down_unit: str | None = None) -> str:
    """Use the charm action to retrieve the primary from provided application.

    Args:
        ops_test: OpsTest instance.
        app: database application name.
        down_unit: unit that is offline and the action won't run on.

    Returns:
        primary unit name.
    """
    for unit in ops_test.model.applications[app].units:
        if unit.name != down_unit:
            action = await unit.run_action("get-primary")
            action = await action.wait()
            primary = action.results.get("primary", "None")
            if primary == "None":
                continue
            return primary
    return None


async def list_wal_files(ops_test: OpsTest, app: str) -> set:
    """Returns the list of WAL segment files in each unit."""
    units = [unit.name for unit in ops_test.model.applications[app].units]
    command = "ls -1 /var/snap/charmed-postgresql/common/var/lib/postgresql/pg_wal/"
    files = {}
    for unit in units:
        stdout = await run_command_on_unit(ops_test, unit, command)
        files[unit] = stdout.splitlines()
        files[unit] = {
            i for i in files[unit] if ".history" not in i and i != "" and i != "archive_status"
        }
    return files


async def send_signal_to_process(
    ops_test: OpsTest, unit_name: str, process: str, signal: str
) -> None:
    """Kills process on the unit according to the provided kill code."""
    # Killing the only instance can be disastrous.
    app = await app_name(ops_test)
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=1000)

    opt = "-f" if "/" in process else "-x"

    command = f"exec --unit {unit_name} -- pkill --signal {signal} {opt} {process}"

    # Send the signal.
    return_code, _, _ = await ops_test.juju(*command.split())
    if signal != "SIGCONT" and return_code != 0:
        raise ProcessError(
            "Expected command %s to succeed instead it failed: %s",
            command,
            return_code,
        )


async def is_postgresql_ready(ops_test, unit_name: str, use_ip_from_inside: bool = False) -> bool:
    """Verifies a PostgreSQL instance is running and available."""
    unit_ip = (
        (await get_ip_from_inside_the_unit(ops_test, unit_name))
        if use_ip_from_inside
        else get_unit_address(ops_test, unit_name)
    )
    try:
        for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3)):
            with attempt:
                instance_health_info = requests.get(f"http://{unit_ip}:8008/health")
                assert instance_health_info.status_code == 200
    except RetryError:
        return False

    return True


def restore_network_for_unit(machine_name: str) -> None:
    """Restore network from a lxc container.

    Args:
        machine_name: lxc container hostname
    """
    # remove mask from eth0
    restore_network_command = f"lxc config device remove {machine_name} eth0"
    subprocess.check_call(restore_network_command.split())


def restore_network_for_unit_without_ip_change(machine_name: str) -> None:
    """Restore network from a lxc container (without causing the change of the unit IP address).

    Args:
        machine_name: lxc container hostname
    """
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.egress="
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.ingress="
    subprocess.check_call(limit_set_command.split())
    limit_set_command = f"lxc config device set {machine_name} eth0 limits.priority="
    subprocess.check_call(limit_set_command.split())


async def is_secondary_up_to_date(
    ops_test: OpsTest, unit_name: str, expected_writes: int, use_ip_from_inside: bool = False
) -> bool:
    """Checks if secondary is up-to-date with the cluster.

    Retries over the period of one minute to give secondary adequate time to copy over data.
    """
    app = await app_name(ops_test)
    password = await get_password(ops_test, app)
    host = await next(
        (
            get_ip_from_inside_the_unit(ops_test, unit.name)
            if use_ip_from_inside
            else get_unit_ip(ops_test, unit.name)
        )
        for unit in ops_test.model.applications[app].units
        if unit.name == unit_name
    )
    connection_string = (
        f"dbname='{APPLICATION_NAME.replace('-', '_')}_database' user='operator'"
        f" host='{host}' password='{password}' connect_timeout=10"
    )

    try:
        for attempt in Retrying(stop=stop_after_delay(60 * 3), wait=wait_fixed(3)):
            with (
                attempt,
                psycopg2.connect(connection_string) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT COUNT(number), MAX(number) FROM continuous_writes;")
                results = cursor.fetchone()
                assert results[0] == expected_writes and results[1] == expected_writes
    except RetryError:
        return False
    finally:
        connection.close()

    return True


async def start_continuous_writes(ops_test: OpsTest, app: str, model: Model = None) -> None:
    """Start continuous writes to PostgreSQL."""
    # Start the process by relating the application to the database or
    # by calling the action if the relation already exists.
    if model is None:
        model = ops_test.model
    relations = [
        relation
        for relation in model.applications[app].relations
        if not relation.is_peer
        and f"{relation.requires.application_name}:{relation.requires.name}"
        == f"{APPLICATION_NAME}:database"
    ]
    if not relations:
        await model.relate(app, f"{APPLICATION_NAME}:database")
        await model.wait_for_idle(status="active", timeout=1000)
    for attempt in Retrying(stop=stop_after_delay(60 * 5), wait=wait_fixed(3), reraise=True):
        with attempt:
            action = (
                await model
                .applications[APPLICATION_NAME]
                .units[0]
                .run_action("start-continuous-writes")
            )
            await action.wait()
            assert action.results["result"] == "True", "Unable to create continuous_writes table"


async def stop_continuous_writes(ops_test: OpsTest) -> int:
    """Stops continuous writes to PostgreSQL and returns the last written value."""
    action = (
        await ops_test.model
        .applications[APPLICATION_NAME]
        .units[0]
        .run_action("stop-continuous-writes")
    )
    action = await action.wait()
    return int(action.results["writes"])


async def update_restart_condition(ops_test: OpsTest, unit, condition: str):
    """Updates the restart condition in the DB service file.

    When the DB service fails it will now wait for `delay` number of seconds.
    """
    # Load the service file from the unit and update it with the new delay.
    _, temp_path = mkstemp()
    await unit.scp_from(source=PATRONI_SERVICE_DEFAULT_PATH, destination=temp_path)
    with open(temp_path) as patroni_service_file:
        patroni_service = patroni_service_file.readlines()

    for index, line in enumerate(patroni_service):
        if "Restart=" in line:
            patroni_service[index] = f"Restart={condition}\n"

    with open(temp_path, "w") as service_file:
        service_file.writelines(patroni_service)

    # Upload the changed file back to the unit, we cannot scp this file directly to
    # PATRONI_SERVICE_DEFAULT_PATH since this directory has strict permissions, instead we scp it
    # elsewhere and then move it to PATRONI_SERVICE_DEFAULT_PATH.
    await unit.scp_to(source=temp_path, destination="patroni.service")
    mv_cmd = f"mv /home/ubuntu/patroni.service {PATRONI_SERVICE_DEFAULT_PATH}"
    await run_command_on_unit(ops_test, unit.name, mv_cmd)

    # Remove temporary file from machine.
    os.remove(temp_path)

    # Reload the daemon for systemd otherwise changes are not saved.
    reload_cmd = "systemctl daemon-reload"
    await run_command_on_unit(ops_test, unit.name, reload_cmd)
    start_cmd = f"systemctl start {SERVICE_NAME}"
    await run_command_on_unit(ops_test, unit.name, start_cmd)

    await is_postgresql_ready(ops_test, unit.name)


@retry(stop=stop_after_attempt(20), wait=wait_fixed(30))
async def wait_network_restore(ops_test: OpsTest, unit_name: str, old_ip: str) -> None:
    """Wait until network is restored.

    Args:
        ops_test: pytest plugin helper
        unit_name: name of the unit
        old_ip: old registered IP address
    """
    # Retrieve the unit IP from inside the unit because it may not be updated in the
    # Juju status too quickly.
    if (await get_ip_from_inside_the_unit(ops_test, unit_name)) == old_ip:
        raise Exception


def storage_type(ops_test, app):
    """Retrieves type of storage associated with an application.

    Note: this function exists as a temporary solution until this issue is ported to libjuju 2:
    https://github.com/juju/python-libjuju/issues/694
    """
    model_name = ops_test.model.info.name
    proc = subprocess.check_output(f"juju storage --model={model_name}".split())
    proc = proc.decode("utf-8")
    for line in proc.splitlines():
        if "Storage" in line:
            continue

        if len(line) == 0:
            continue

        if "detached" in line:
            continue

        unit_name = line.split()[0]
        app_name = unit_name.split("/")[0]
        if app_name == app:
            return line.split()[3]


def storage_id(ops_test, unit_name):
    """Retrieves  storage id associated with provided unit.

    Note: this function exists as a temporary solution until this issue is ported to libjuju 2:
    https://github.com/juju/python-libjuju/issues/694
    """
    model_name = ops_test.model.info.name
    proc = subprocess.check_output(f"juju storage --model={model_name}".split())
    proc = proc.decode("utf-8")
    for line in proc.splitlines():
        if "Storage" in line:
            continue

        if len(line) == 0:
            continue

        if "detached" in line:
            continue

        if line.split()[0] == unit_name:
            return line.split()[1]


async def add_unit_with_storage(ops_test, app, storage):
    """Adds unit with storage.

    Note: this function exists as a temporary solution until this issue is resolved:
    https://github.com/juju/python-libjuju/issues/695
    """
    original_units = {unit.name for unit in ops_test.model.applications[app].units}
    model_name = ops_test.model.info.name
    add_unit_cmd = f"add-unit {app} --model={model_name} --attach-storage={storage}".split()
    return_code, _, _ = await ops_test.juju(*add_unit_cmd)
    assert return_code == 0, "Failed to add unit with storage"
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(apps=[app], status="active", timeout=2000)

    # When removing all units sometimes the last unit remain in the list
    current_units = {unit.name for unit in ops_test.model.applications[app].units}
    original_units.intersection_update(current_units)
    assert original_units.issubset(current_units), "New unit not added to model"

    # verify storage attached
    new_unit = (current_units - original_units).pop()
    assert storage_id(ops_test, new_unit) == storage, "unit added with incorrect storage"

    # return a reference to newly added unit
    for unit in ops_test.model.applications[app].units:
        if unit.name == new_unit:
            return unit


async def reused_replica_storage(ops_test: OpsTest, unit_name) -> bool:
    """Returns True if storage provided to Postgresql has been reused.

    Checks Patroni logs for when the database was in archive mode.
    """
    await run_command_on_unit(
        ops_test,
        unit_name,
        "grep 'Database cluster state: in archive recovery' "
        "/var/snap/charmed-postgresql/common/var/log/patroni/patroni.log*",
    )
    return True


async def reused_full_cluster_recovery_storage(ops_test: OpsTest, unit_name) -> bool:
    """Returns True if storage provided to Postgresql has been reused.

    Checks Patroni logs for when the database was in archive mode or shut down.
    """
    await run_command_on_unit(
        ops_test,
        unit_name,
        "grep -E 'Database cluster state: in archive recovery|Database cluster state: shut down' "
        "/var/snap/charmed-postgresql/common/var/log/patroni/patroni.log*",
    )
    return True


async def is_storage_exists(ops_test: OpsTest, storage_id: str) -> bool:
    """Returns True if storage exists by provided storage ID."""
    complete_command = [
        "show-storage",
        "-m",
        f"{ops_test.controller_name}:{ops_test.model.info.name}",
        storage_id,
        "--format=json",
    ]
    return_code, stdout, _ = await ops_test.juju(*complete_command)
    if return_code != 0:
        if return_code == 1:
            return storage_id in stdout
        raise Exception(
            "Expected command %s to succeed instead it failed: %s with code: ",
            complete_command,
            stdout,
            return_code,
        )
    return storage_id in str(stdout)


async def create_db(ops_test: OpsTest, app: str, db: str) -> None:
    """Creates database with specified name."""
    unit = ops_test.model.applications[app].units[0]
    unit_address = await unit.get_public_address()
    password = await get_password(ops_test, app)

    conn = db_connect(unit_address, password)
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute(f"CREATE DATABASE {db};")
    cursor.close()
    conn.close()


async def check_db(ops_test: OpsTest, app: str, db: str) -> bool:
    """Returns True if database with specified name already exists."""
    unit = ops_test.model.applications[app].units[0]
    unit_address = await unit.get_public_address()
    password = await get_password(ops_test, app)

    assert password is not None

    query = await execute_query_on_unit(
        unit_address,
        password,
        f"select datname from pg_catalog.pg_database where datname = '{db}';",
    )

    if "ERROR" in query:
        raise Exception(f"Database check is failed with postgresql err: {query}")

    return db in query


async def get_any_deatached_storage(ops_test: OpsTest) -> str:
    """Returns any of the current available deatached storage."""
    return_code, storages_list, stderr = await ops_test.juju(
        "storage", "-m", f"{ops_test.controller_name}:{ops_test.model.info.name}", "--format=json"
    )
    if return_code != 0:
        raise Exception(f"failed to get storages info with error: {stderr}")

    parsed_storages_list = json.loads(storages_list)
    for storage_name, storage in parsed_storages_list["storage"].items():
        if (str(storage["status"]["current"]) == "detached") and (str(storage["life"] == "alive")):
            return storage_name

    raise Exception("failed to get deatached storage")


async def check_password_auth(ops_test: OpsTest, unit_name: str) -> bool:
    """Checks if "operator" password is valid for current postgresql db."""
    stdout = await run_command_on_unit(
        ops_test,
        unit_name,
        """grep -E 'password authentication failed for user' /var/snap/charmed-postgresql/common/var/log/postgresql/postgresql*""",
    )
    return 'password authentication failed for user "operator"' not in stdout


async def remove_unit_force(ops_test: OpsTest, unit_name: str):
    """Removes unit with --force --no-wait."""
    app_name = unit_name.split("/")[0]
    complete_command = ["remove-unit", f"{unit_name}", "--force", "--no-wait", "--no-prompt"]
    return_code, stdout, _ = await ops_test.juju(*complete_command)
    if return_code != 0:
        raise Exception(
            "Expected command %s to succeed instead it failed: %s with code: ",
            complete_command,
            stdout,
            return_code,
        )

    for unit in ops_test.model.applications[app_name].units:
        assert unit != unit_name
