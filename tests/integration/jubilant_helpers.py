# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import itertools
import json
import logging
import os
import random
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from enum import Enum
from pathlib import Path

import botocore
import jubilant
import psycopg2
import pytest
import requests
import yaml
from tenacity import (
    RetryError,
    Retrying,
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
)

from constants import DATABASE_DEFAULT_NAME, PEER, SYSTEM_USERS_PASSWORD_CONFIG

from .adapters import JujuFixture, ModelAdapter, UnitAdapter
from .ha_tests.helpers import ProcessError
from .helpers import DATABASE_APP_NAME, SecretNotFoundError

logger = logging.getLogger(__name__)

RELATION_ENDPOINT = "postgresql"
DATA_INTEGRATOR_APP_NAME = "data-integrator"


class RoleAttributeValue(Enum):
    NO = 0
    YES = 1
    REQUESTED_DATABASE = 2
    ALL_DATABASES = 3


def get_credentials(
    juju: jubilant.Juju,
    unit_name: str,
) -> dict:
    """Get the data integrator credentials.

    Args:
        juju: the jubilant.Juju instance.
        unit_name: the name of the unit.

    Returns:
        the data integrator credentials.
    """
    action = juju.run(unit_name, "get-credentials")
    return action.results


def get_password(
    username: str = "operator",
    database_app_name: str = DATABASE_APP_NAME,
) -> str:
    """Retrieve a user password from the secret.

    Args:
        username: the user to get the password.
        database_app_name: the app for getting the secret

    Returns:
        the user password.
    """
    secret = get_secret_by_label(label=f"{PEER}.{database_app_name}.app")
    password = secret.get(f"{username}-password")
    print(f"Retrieved password for {username}: {password}")

    return password


def get_primary(juju: jubilant.Juju, unit_name: str) -> str:
    """Get the primary unit.

    Args:
        juju: the jubilant.Juju instance.
        unit_name: the name of the unit.

    Returns:
        the current primary unit.
    """
    action = juju.run(unit_name, "get-primary")
    if "primary" not in action.results or action.results["primary"] not in juju.status().get_units(
        unit_name.split("/")[0]
    ):
        assert False, "Primary unit not found"
    return action.results["primary"]


def get_secret_by_label(label: str) -> dict[str, str]:
    # Subprocess calls are used because some Juju commands are still missing in jubilant:
    # https://github.com/canonical/jubilant/issues/117.
    secrets_raw = subprocess.run(["juju", "list-secrets"], capture_output=True).stdout.decode(
        "utf-8"
    )
    secret_ids = [
        secret_line.split()[0] for secret_line in secrets_raw.split("\n")[1:] if secret_line
    ]

    for secret_id in secret_ids:
        secret_data_raw = subprocess.run(
            ["juju", "show-secret", "--format", "json", "--reveal", secret_id], capture_output=True
        ).stdout
        secret_data = json.loads(secret_data_raw)

        if label == secret_data[secret_id].get("label"):
            return secret_data[secret_id]["content"]["Data"]

    raise SecretNotFoundError(f"Secret with label {label} not found")


def get_unit_address(juju: jubilant.Juju, unit_name: str) -> str:
    """Get the unit IP address.

    Args:
        juju: the jubilant.Juju instance.
        unit_name: The name of the unit

    Returns:
        IP address of the unit
    """
    return juju.status().get_units(unit_name.split("/")[0]).get(unit_name).public_address


def relations(juju: jubilant.Juju, provider_app: str, requirer_app: str) -> list:
    return [
        relation
        for relation in juju.status().apps.get(provider_app).relations.values()
        if any(
            True for relation_instance in relation if relation_instance.related_app == requirer_app
        )
    ]


def roles_attributes(predefined_roles: dict, combination: str) -> dict:
    auto_escalate_to_database_owner = RoleAttributeValue.NO
    connect = RoleAttributeValue.NO
    create_databases = RoleAttributeValue.NO
    create_objects = RoleAttributeValue.NO
    escalate_to_database_owner = RoleAttributeValue.NO
    read_data = RoleAttributeValue.NO
    read_stats = RoleAttributeValue.NO
    run_backup_commands = RoleAttributeValue.NO
    set_up_predefined_catalog_roles = RoleAttributeValue.NO
    set_user = RoleAttributeValue.NO
    write_data = RoleAttributeValue.NO
    for role in combination.split(","):
        # Whether the relation user is auto-escalated to the database owner user at login
        # in the requested database (True value) or in all databases ("*" value).
        will_auto_escalate_to_database_owner = predefined_roles[role][
            "auto-escalate-to-database-owner"
        ]
        if (
            auto_escalate_to_database_owner == RoleAttributeValue.NO
            or will_auto_escalate_to_database_owner == "*"
        ):
            auto_escalate_to_database_owner = will_auto_escalate_to_database_owner

        role_permissions = predefined_roles[role]["permissions"]

        # Permission to connect to the requested database (True value) or to all databases
        # ("*" value).
        role_can_connect = role_permissions["connect"]
        if connect == RoleAttributeValue.NO or role_can_connect == "*":
            connect = role_can_connect

        # Permission to create databases (True or RoleAttributeValue.NO).
        create_databases = (
            role_permissions["create-databases"]
            if create_databases == RoleAttributeValue.NO
            else create_databases
        )

        # Permission to create objects in the requested database (True value) or in all databases
        # ("*" value).
        role_can_create_objects = role_permissions["create-objects"]
        if create_objects == RoleAttributeValue.NO or role_can_create_objects == "*":
            create_objects = role_can_create_objects

        # Permission to escalate to the database owner user in the requested database (True value)
        # or in all databases ("*" value).
        role_can_escalate_to_database_owner = role_permissions["escalate-to-database-owner"]
        if (
            escalate_to_database_owner == RoleAttributeValue.NO
            or role_can_escalate_to_database_owner == "*"
        ):
            escalate_to_database_owner = role_can_escalate_to_database_owner

        # Permission to read data in the requested database (True value) or in all databases
        # ("*" value).
        role_can_read_data = role_permissions["read-data"]
        if read_data == RoleAttributeValue.NO or role_can_read_data == "*":
            read_data = role_can_read_data

        read_stats = (
            role_permissions["read-stats"]
            if role_permissions["read-stats"] != RoleAttributeValue.NO
            else read_stats
        )

        run_backup_commands = (
            role_permissions["run-backup-commands"]
            if role_permissions["run-backup-commands"] != RoleAttributeValue.NO
            else run_backup_commands
        )

        # Permission to set up predefined catalog roles ("*" for all databases or RoleAttributeValue.NO for not being
        # able to do it).
        role_can_set_up_predefined_catalog_roles = role_permissions[
            "set-up-predefined-catalog-roles"
        ]
        if (
            set_up_predefined_catalog_roles == RoleAttributeValue.NO
            or role_can_set_up_predefined_catalog_roles == "*"
        ):
            set_up_predefined_catalog_roles = role_can_set_up_predefined_catalog_roles

        # Permission to call the set_user function (True or RoleAttributeValue.NO).
        set_user = role_permissions["set-user"] if set_user == RoleAttributeValue.NO else set_user

        # Permission to write data in the requested database (True value) or in all databases
        # ("*" value).
        role_can_write_data = role_permissions["write-data"]
        if write_data == RoleAttributeValue.NO or role_can_write_data == "*":
            write_data = role_can_write_data
    return {
        "auto-escalate-to-database-owner": auto_escalate_to_database_owner,
        "permissions": {
            "connect": connect,
            "create-databases": create_databases,
            "create-objects": create_objects,
            "escalate-to-database-owner": escalate_to_database_owner,
            "read-data": read_data,
            "read-stats": read_stats,
            "run-backup-commands": run_backup_commands,
            "set-up-predefined-catalog-roles": set_up_predefined_catalog_roles,
            "set-user": set_user,
            "write-data": write_data,
        },
    }


def get_lxd_machine_name(status, unit_name: str) -> str:
    """Get the LXD machine/container name for a given unit.

    Args:
        status: Juju status object
        unit_name: Full unit name (e.g., "postgresql/0")

    Returns:
        LXD machine/container name (instance_id)
    """
    unit_info = status.get_units(DATABASE_APP_NAME).get(unit_name)
    if not unit_info:
        raise RuntimeError(f"Unable to find unit {unit_name} in status")

    machine_id = getattr(unit_info, "machine", None)
    if not machine_id:
        raise RuntimeError(f"Unable to find machine ID for unit {unit_name}")

    machine_obj = (
        getattr(status, "machines", {}).get(machine_id) if hasattr(status, "machines") else None
    )
    if not machine_obj:
        raise RuntimeError(f"Unable to find machine object for machine {machine_id}")

    machine_name = getattr(machine_obj, "instance_id", None)
    if not machine_name:
        raise RuntimeError(f"Unable to find instance_id for machine {machine_id}")

    return machine_name


def verify_leader_active(status, unit_name: str) -> None:
    """Verify that a unit is the active leader.

    Args:
        status: Juju status object
        unit_name: Full unit name to verify

    Raises:
        AssertionError if unit is not active leader
    """
    unit = status.get_units(DATABASE_APP_NAME).get(unit_name)
    assert unit is not None, f"Unit {unit_name} not found in status"
    assert unit.leader, f"Unit {unit_name} is not the leader"
    assert unit.workload_status.current == "active", (
        f"Unit {unit_name} is not active. "
        f"Status: {unit.workload_status.current}, "
        f"Message: {unit.workload_status.message}"
    )


def verify_temp_table_creation(juju: jubilant.Juju) -> None:
    """Test that temporary tables can be created successfully."""
    creds = get_credentials(juju, f"{DATA_INTEGRATOR_APP_NAME}/0")
    uri = creds[RELATION_ENDPOINT]["uris"]

    connection = None
    try:
        connection = psycopg2.connect(uri)
        connection.autocommit = True
        with connection.cursor() as cur:
            cur.execute("CREATE TEMPORARY TABLE test (lines TEXT);")
        logger.info("Successfully created temporary table")
    finally:
        if connection is not None:
            connection.close()


def force_leader_election(juju: jubilant.Juju, original_leader: str) -> str:
    """Force a leader election by stopping the current leader's juju agent.

    Args:
        juju: Juju client
        original_leader: Current leader unit name

    Returns:
        Name of the newly elected leader unit

    Raises:
        RuntimeError: If no new leader is elected within timeout
    """
    logger.info(f"Stopping juju agent on {original_leader} to force leader election")
    status = juju.status()
    machine_name = get_lxd_machine_name(status, original_leader)

    # Get the machine ID from the status
    unit_info = status.get_units(DATABASE_APP_NAME).get(original_leader)
    machine_id = getattr(unit_info, "machine", None)
    if not machine_id:
        raise RuntimeError(f"Unable to find machine ID for unit {original_leader}")

    jujud_service = f"jujud-machine-{machine_id}"
    logger.info(f"Stopping {jujud_service} service")

    subprocess.check_call([
        "lxc",
        "exec",
        machine_name,
        "--",
        "systemctl",
        "stop",
        jujud_service,
    ])

    # Allow time for agent shutdown to propagate before polling
    logger.info("Waiting for agent shutdown to propagate")
    time.sleep(5)

    # Wait for a new leader to be elected
    logger.info("Waiting for new leader election")
    new_leader = None
    for _ in range(60):  # Wait up to 60 seconds
        time.sleep(1)
        try:
            status = juju.status()
            for unit_name, unit_status in status.get_units(DATABASE_APP_NAME).items():
                if unit_status.leader and unit_name != original_leader:
                    new_leader = unit_name
                    break
            if new_leader:
                break
        except Exception as e:
            logger.debug(f"Error checking leader status: {e}")
            continue

    if new_leader is None:
        # Restart the original leader's agent in case election failed
        logger.info(f"Restarting {jujud_service} service after failed election")
        subprocess.check_call([
            "lxc",
            "exec",
            machine_name,
            "--",
            "systemctl",
            "start",
            jujud_service,
        ])
        raise RuntimeError("No new leader elected within timeout")

    logger.info(f"New leader elected: {new_leader}")

    # Restart the original leader's agent so the cluster is healthy
    logger.info(f"Restarting {jujud_service} service on {original_leader}")
    subprocess.check_call([
        "lxc",
        "exec",
        machine_name,
        "--",
        "systemctl",
        "start",
        jujud_service,
    ])

    return new_leader


def check_for_fix_log_message(juju: jubilant.Juju, unit_name: str) -> bool:
    """Check if the library fix log message appears in the unit's logs.

    Args:
        juju: Juju client
        unit_name: Unit name to check logs for

    Returns:
        True if the log message was found, False otherwise
    """
    logger.info("Checking debug logs for the library fix log message")
    result = subprocess.run(
        [
            "juju",
            "debug-log",
            "--replay",
            "--no-tail",
            "-m",
            juju.model,
            "--include",
            unit_name,
        ],
        capture_output=True,
        text=True,
    )

    expected_message = (
        "Fixed permissions on temp tablespace directory at /var/snap/charmed-postgresql/common/data/temp "
        "(persistent storage), existing tablespace remains valid"
    )

    if expected_message in result.stdout:
        logger.info(f"✓ Found expected log message in {unit_name} logs")
        return True

    logger.warning(
        f"Expected log message not found in {unit_name} logs. "
        "This may indicate the code path was not triggered or permissions were already correct."
    )
    return False


#################################################
#                                               #
#              Adapted Helpers                  #
#                                               #
#################################################


CHARM_BASE = "ubuntu@22.04"
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
DATABASE_APP_NAME = METADATA["name"]
STORAGE_PATH = METADATA["storage"]["data"]["location"]
APPLICATION_NAME = "postgresql-test-app"
DATA_INTEGRATOR_APP_NAME = "data-integrator"


logger = logging.getLogger(__name__)


def build_connection_string(
    juju: JujuFixture,
    application_name: str,
    relation_name: str,
    read_only_endpoint: bool = False,
    remote_unit_name: str | None = None,
) -> str | None:
    """Returns a PostgreSQL connection string.

    Args:
        juju: The Juju fixture
        application_name: The name of the application
        relation_name: name of the relation to get connection data from
        read_only_endpoint: whether to choose the read-only endpoint
            instead of the read/write endpoint
        remote_unit_name: Optional remote unit name used to retrieve
            unit data instead of application data

    Returns:
        a PostgreSQL connection string
    """
    unit_name = f"{application_name}/0"
    raw_data = juju.cli("show-unit", unit_name)
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [
        v for v in data[unit_name]["relation-info"] if v["related-endpoint"] == relation_name
    ]
    if len(relation_data) == 0:
        raise ValueError(
            f"no relation data could be grabbed on relation with endpoint {relation_name}"
        )
    if remote_unit_name:
        data = relation_data[0]["related-units"][remote_unit_name]["data"]
    else:
        data = relation_data[0]["application-data"]
    if read_only_endpoint:
        if data.get("standbys") is None:
            return None
        return data.get("standbys").split(",")[0]
    else:
        return data.get("master")


def change_primary_start_timeout(
    juju: JujuFixture, unit_name: str, seconds: int | None, password: str
) -> None:
    """Change primary start timeout configuration.

    Args:
        juju: juju instance.
        unit_name: the unit used to set the configuration.
        seconds: number of seconds to set in primary_start_timeout configuration.
        password: Patroni password.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            unit_ip = get_unit_address(juju, unit_name)
            requests.patch(
                f"https://{unit_ip}:8008/config",
                json={"primary_start_timeout": seconds},
                verify=False,
                auth=requests.auth.HTTPBasicAuth("patroni", password),
            )


def get_patroni_cluster(unit_ip: str) -> dict[str, str]:
    resp = requests.get(f"https://{unit_ip}:8008/cluster", verify=False)
    return resp.json()


def assert_sync_standbys(unit_ip: str, standbys: int) -> None:
    for attempt in Retrying(stop=stop_after_delay(60), wait=wait_fixed(3), reraise=True):
        with attempt:
            cluster = get_patroni_cluster(unit_ip)
            cluster_standbys = 0
            for member in cluster["members"]:
                if member["role"] == "sync_standby":
                    cluster_standbys += 1
            assert cluster_standbys >= standbys, "Less than expected standbys"


def check_database_users_existence(
    juju: JujuFixture,
    users_that_should_exist: list[str],
    users_that_should_not_exist: list[str],
) -> None:
    """Checks that applications users exist in the database.

    Args:
        juju: The Juju fixture
        users_that_should_exist: List of users that should exist in the database
        users_that_should_not_exist: List of users that should not exist in the database
    """
    unit = juju.ext.model.applications[DATABASE_APP_NAME].units[0]
    unit_address = unit.get_public_address()
    password = get_password()

    # Retrieve all users in the database.
    users_in_db = execute_query_on_unit(
        unit_address,
        password,
        "SELECT usename FROM pg_catalog.pg_user;",
    )

    # Assert users that should exist.
    for user in users_that_should_exist:
        assert user in users_in_db

    # Assert users that should not exist.
    for user in users_that_should_not_exist:
        assert user not in users_in_db


def check_databases_creation(juju: JujuFixture, databases: list[str]) -> None:
    """Checks that database and tables are successfully created for the application.

    Args:
        juju: The Juju fixture
        databases: List of database names that should have been created
    """
    password = get_password()

    for unit in juju.ext.model.applications[DATABASE_APP_NAME].units:
        unit_address = unit.public_address

        for database in databases:
            # Ensure database exists in PostgreSQL.
            output = execute_query_on_unit(
                unit_address,
                password,
                "SELECT datname FROM pg_database;",
            )
            assert database in output

            # Ensure that application tables exist in the database
            output = execute_query_on_unit(
                unit_address,
                password,
                "SELECT table_name FROM information_schema.tables;",
                database=database,
            )
            assert len(output)


@retry(
    retry=retry_if_result(lambda x: not x),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
def check_patroni(juju: JujuFixture, unit_name: str, restart_time: float) -> bool:
    """Check if Patroni is running correctly on a specific unit.

    Args:
        juju: The Juju fixture instance
        unit_name: The name of the unit
        restart_time: Point in time before the unit was restarted.

    Returns:
        whether Patroni is running correctly.
    """
    unit_ip = get_unit_address(juju, unit_name)
    health_info = requests.get(f"https://{unit_ip}:8008/health", verify=False).json()
    postmaster_start_time = datetime.strptime(
        health_info["postmaster_start_time"], "%Y-%m-%d %H:%M:%S.%f%z"
    ).timestamp()
    return postmaster_start_time > restart_time and health_info["state"] == "running"


def check_cluster_members(juju: JujuFixture, application_name: str) -> None:
    """Check that the correct members are part of the cluster.

    Args:
        juju: The Juju fixture instance
        application_name: The name of the application
    """
    for attempt in Retrying(
        stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
    ):
        with attempt:
            any_unit_name = juju.ext.model.applications[application_name].units[0].name
            primary = get_primary(juju, any_unit_name)
            address = get_unit_address(juju, primary)

            expected_members = get_application_units(juju, application_name)
            expected_members_ips = get_application_units_ips(juju, application_name)

            r = requests.get(f"https://{address}:8008/cluster", verify=False)
            assert [member["name"] for member in r.json()["members"]] == expected_members
            assert [member["host"] for member in r.json()["members"]] == expected_members_ips


def construct_endpoint(endpoint: str, region: str) -> str:
    """Construct the S3 service endpoint using the region.

    This is needed when the provided endpoint is from AWS, and it doesn't contain the region.
    """
    # Load endpoints data.
    loader = botocore.loaders.create_loader()
    data = loader.load_data("endpoints")

    # Construct the endpoint using the region.
    resolver = botocore.regions.EndpointResolver(data)
    endpoint_data = resolver.construct_endpoint("s3", region)

    # Use the built endpoint if it is an AWS endpoint.
    if endpoint_data and endpoint.endswith(endpoint_data["dnsSuffix"]):
        endpoint = f"{endpoint.split('://')[0]}://{endpoint_data['hostname']}"

    return endpoint


def convert_records_to_dict(records: list[tuple]) -> dict:
    """Converts psycopg2 records list to a dict."""
    records_dict = {}
    for record in records:
        # Add record tuple data to dict.
        records_dict[record[0]] = record[1]
    return records_dict


def count_switchovers(juju: JujuFixture, unit_name: str) -> int:
    """Return the number of performed switchovers."""
    unit_address = get_unit_address(juju, unit_name)
    switchover_history_info = requests.get(f"https://{unit_address}:8008/history", verify=False)
    return len(switchover_history_info.json())


def db_connect(
    host: str, password: str, username: str = "operator", database: str = "postgres"
) -> psycopg2.extensions.connection:
    """Returns psycopg2 connection object linked to postgres db in the given host.

    Args:
        host: the IP of the postgres host
        password: user password
        username: username to connect with
        database: database to connect to

    Returns:
        psycopg2 connection object linked to postgres db, under "operator" user.
    """
    return psycopg2.connect(
        f"dbname='{database}' user='{username}' host='{host}' password='{password}' connect_timeout=10"
    )


def deploy_and_relate_application_with_postgresql(
    juju: JujuFixture,
    charm: str,
    application_name: str,
    number_of_units: int,
    config: dict | None = None,
    channel: str = "stable",
    relation: str = "db",
    series: str | None = None,
) -> int:
    """Helper function to deploy and relate application with PostgreSQL.

    Args:
        juju: The Juju fixture.
        charm: Charm identifier.
        application_name: The name of the application to deploy.
        number_of_units: The number of units to deploy.
        config: Extra config options for the application.
        channel: The channel to use for the charm.
        relation: Name of the PostgreSQL relation to relate
            the application to.
        series: Series of the charm to deploy.

    Returns:
        the id of the created relation.
    """
    # Deploy application.
    juju.ext.model.deploy(
        charm,
        channel=channel,
        application_name=application_name,
        num_units=number_of_units,
        config=config,
        series=series,
    )
    juju.ext.model.wait_for_idle(
        apps=[application_name],
        status="active",
        raise_on_blocked=False,
        timeout=1500,
    )

    # Relate application to PostgreSQL.
    relation = juju.ext.model.relate(f"{application_name}", f"{DATABASE_APP_NAME}:{relation}")
    juju.ext.model.wait_for_idle(
        apps=[application_name],
        status="active",
        raise_on_blocked=False,  # Application that needs a relation is blocked initially.
        timeout=1500,
    )

    return relation.id


def deploy_and_relate_bundle_with_postgresql(
    juju: JujuFixture,
    bundle_name: str,
    main_application_name: str,
    main_application_num_units: int | None = None,
    relation_name: str = "db",
    status: str = "active",
    status_message: str | None = None,
    overlay: dict | None = None,
    timeout: int = 2000,
) -> str:
    """Helper function to deploy and relate a bundle with PostgreSQL.

    Args:
        juju: The Juju fixture.
        bundle_name: The name of the bundle to deploy.
        main_application_name: The name of the application that should be
            related to PostgreSQL.
        main_application_num_units: Optional number of units for the main
            application.
        relation_name: The name of the relation to use in PostgreSQL
            (db or db-admin).
        status: Status to wait for in the application after relating
            it to PostgreSQL.
        status_message: Status message to wait for in the application after
            relating it to PostgreSQL.
        overlay: Optional overlay to be used when deploying the bundle.
        timeout: Timeout to wait for the deployment to idle.
    """
    # Deploy the bundle.
    with tempfile.NamedTemporaryFile(dir=os.getcwd()) as original:
        # Download the original bundle.
        juju.cli("download", bundle_name, "--filepath", original.name, include_model=False)

        # Open the bundle compressed file and update the contents
        # of the bundle.yaml file to deploy it.
        with zipfile.ZipFile(original.name, "r") as archive:
            bundle_yaml = archive.read("bundle.yaml")
            data = yaml.load(bundle_yaml, Loader=yaml.FullLoader)

            if main_application_num_units is not None:
                data["applications"][main_application_name]["num_units"] = (
                    main_application_num_units
                )

            # Save the list of relations other than `db` and `db-admin`,
            # so we can add them back later.
            other_relations = [
                relation for relation in data["relations"] if "postgresql" in relation
            ]

            # Remove PostgreSQL and relations with it from the bundle.yaml file.
            config = data["applications"]["postgresql"]["options"]
            if config.get("experimental_max_connections", 0) > 200:
                config["experimental_max_connections"] = 200
            for key, val in config.items():
                config[key] = str(val)
            logger.info(f"Bundle {bundle_name} needs configuration {config}")
            juju.ext.model.applications[DATABASE_APP_NAME].set_config(config)
            del data["applications"]["postgresql"]
            data["relations"] = [
                relation
                for relation in data["relations"]
                if "postgresql" not in relation
                and "postgresql:db" not in relation
                and "postgresql:db-admin" not in relation
            ]

            # Write the new bundle content to a temporary file and deploy it.
            with tempfile.NamedTemporaryFile(dir=os.getcwd()) as patched:
                patched.write(yaml.dump(data).encode("utf_8"))
                patched.seek(0)
                if overlay is not None:
                    with tempfile.NamedTemporaryFile() as overlay_file:
                        overlay_file.write(yaml.dump(overlay).encode("utf_8"))
                        overlay_file.seek(0)
                        juju.deploy(patched.name, overlays=[overlay_file.name])
                else:
                    juju.deploy(patched.name)

    with juju.ext.fast_forward(fast_interval="60s"):
        # Relate application to PostgreSQL.
        relation = juju.ext.model.relate(
            main_application_name, f"{DATABASE_APP_NAME}:{relation_name}"
        )

        # Restore previous existing relations.
        for other_relation in other_relations:
            juju.ext.model.relate(other_relation[0], other_relation[1])

        # Wait for the deployment to complete.
        unit = juju.ext.model.units.get(f"{main_application_name}/0")
        juju.ext.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            timeout=timeout,
        )
        juju.ext.model.wait_for_idle(
            apps=[main_application_name],
            raise_on_blocked=False,
            status=status,
            timeout=timeout,
        )
        if status_message:
            juju.ext.model.block_until(
                lambda: unit.workload_status_message == status_message,
                timeout=timeout,
            )

    return relation.id  # FIXME


def ensure_correct_relation_data(
    juju: JujuFixture, database_units: int, app_name: str, relation_name: str
) -> None:
    """Asserts that the correct database relation data is shared from the right unit to the app."""
    primary = get_primary(juju, f"{DATABASE_APP_NAME}/0")
    for unit_number in range(database_units):
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                unit_name = f"{DATABASE_APP_NAME}/{unit_number}"
                primary_connection_string = build_connection_string(
                    juju, app_name, relation_name, remote_unit_name=unit_name
                )
                replica_connection_string = build_connection_string(
                    juju,
                    app_name,
                    relation_name,
                    read_only_endpoint=True,
                    remote_unit_name=unit_name,
                )
                unit_ip = get_unit_address(juju, unit_name)
                host_parameter = f"host={unit_ip} "
                if unit_name == primary:
                    logger.info(f"Expected primary: {unit_ip}")
                    logger.info(f"Primary conn string: {primary_connection_string}")
                    logger.info(f"Replica conn string: {replica_connection_string}")
                    assert host_parameter in primary_connection_string, (
                        f"{unit_name} is not the host of the primary connection string"
                    )
                    assert host_parameter not in replica_connection_string, (
                        f"{unit_name} is the host of the replica connection string"
                    )


def execute_query_on_unit(
    unit_address: str,
    password: str,
    query: str,
    database: str = DATABASE_DEFAULT_NAME,
    sslmode: str | None = None,
):
    """Execute given PostgreSQL query on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the query on.
        password: The PostgreSQL superuser password.
        query: Query to execute.
        database: Optional database to connect to (defaults to postgres database).
        sslmode: Optional ssl mode to use (defaults to None).

    Returns:
        A list of rows that were potentially returned from the query.
    """
    extra_connection_parameters = f"sslmode={sslmode}" if sslmode else ""
    with (
        psycopg2.connect(
            f"dbname='{database}' user='operator' host='{unit_address}'"
            f"password='{password}' connect_timeout=10 {extra_connection_parameters}"
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))
    return output


def find_unit(juju: JujuFixture, application: str, leader: bool) -> UnitAdapter:
    """Helper function that retrieves a unit, based on need for leader or non-leader.

    Args:
        juju: The Juju fixture.
        application: The name of the application.
        leader: Whether the unit is a leader or not.

    Returns:
        A unit instance.
    """
    ret_unit = None
    for unit in juju.ext.model.applications[application].units:
        if unit.is_leader_from_status() == leader:
            ret_unit = unit

    return ret_unit


def get_application_units(juju: JujuFixture, application_name: str) -> list[str]:
    """List the unit names of an application.

    Args:
        juju: The Juju fixture
        application_name: The name of the application

    Returns:
        list of current unit names of the application
    """
    return [
        unit.name.replace("/", "-") for unit in juju.ext.model.applications[application_name].units
    ]


def get_application_units_ips(juju: JujuFixture, application_name: str) -> list[str]:
    """List the unit IPs of an application.

    Args:
        juju: The Juju fixture
        application_name: The name of the application

    Returns:
        list of current unit IPs of the application
    """
    return [unit.public_address for unit in juju.ext.model.applications[application_name].units]


def get_landscape_api_credentials(juju: JujuFixture) -> list[str]:
    """Returns the key and secret to be used in the Landscape API.

    Args:
        juju: The Juju fixture
    """
    unit = juju.ext.model.applications[DATABASE_APP_NAME].units[0]
    password = get_password()
    unit_address = unit.get_public_address()

    output = execute_query_on_unit(
        unit_address,
        password,
        "SELECT encode(access_key_id,'escape'), encode(access_secret_key,'escape') FROM api_credentials;",
        database="landscape-standalone-main",
    )

    return output


def get_leader_unit(
    juju: JujuFixture, app: str, model: ModelAdapter | None = None
) -> UnitAdapter | None:
    if model is None:
        model = juju.ext.model

    leader_unit = None
    for unit in model.applications[app].units:
        if unit.is_leader_from_status():
            leader_unit = unit
            break

    return leader_unit


def get_machine_from_unit(juju: JujuFixture, unit_name: str) -> str:
    """Get the name of the machine from a specific unit.

    Args:
        juju: The Juju fixture
        unit_name: The name of the unit to get the machine

    Returns:
        The name of the machine.
    """
    raw_hostname = run_command_on_unit(juju, unit_name, "hostname")
    return raw_hostname.strip()


def get_tls_ca(juju: JujuFixture, unit_name: str, relation: str = "client") -> str:
    """Returns the TLS CA used by the unit.

    Args:
        juju: The Juju fixture
        unit_name: The name of the unit
        relation: TLS relation to get the CA from

    Returns:
        TLS CA or an empty string if there is no CA.
    """
    raw_data = juju.cli("show-unit", unit_name)
    endpoint = f"{relation}-certificates"
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [v for v in data[unit_name]["relation-info"] if v["endpoint"] == endpoint]
    if len(relation_data) == 0:
        return ""
    return json.loads(relation_data[0]["application-data"]["certificates"])[0].get("ca")


def check_connected_user(
    cursor, session_user: str, current_user: str, primary: bool = True
) -> None:
    cursor.execute("SELECT session_user,current_user;")
    result = cursor.fetchone()
    if result is not None:
        instance = "primary" if primary else "replica"
        assert result[0] == session_user, (
            f"The session user should be the {session_user} user in the {instance} (it's currently {result[0]})"
        )
        assert result[1] == current_user, (
            f"The current user should be the {current_user} user in the {instance} (it's currently {result[1]})"
        )
    else:
        assert False, "No result returned from the query"


def check_roles_and_their_permissions(
    juju: JujuFixture, relation_endpoint: str, database_name: str
) -> None:
    action = juju.run(f"{DATA_INTEGRATOR_APP_NAME}/0", "get-credentials")
    data_integrator_credentials = action.results
    username = data_integrator_credentials[relation_endpoint]["username"]
    uris = data_integrator_credentials[relation_endpoint]["uris"]
    connection = None
    try:
        connection = psycopg2.connect(uris)
        connection.autocommit = True
        with connection.cursor() as cursor:
            logger.info(
                "Checking that the relation user is automatically escalated to the database owner user"
            )
            check_connected_user(cursor, username, f"{database_name}_owner")
            logger.info("Creating a test table and inserting data")
            cursor.execute("CREATE TABLE test_table (id INTEGER);")
            logger.info("Inserting data into the test table")
            cursor.execute("INSERT INTO test_table(id) VALUES(1);")
            logger.info("Reading data from the test table")
            cursor.execute("SELECT * FROM test_table;")
            result = cursor.fetchall()
            assert len(result) == 1, "The database owner user should be able to read the data"

            logger.info("Checking that the database owner user can't create a database")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute(f"CREATE DATABASE {database_name}_2;")

            logger.info("Checking that the relation user can't create a table")
            cursor.execute("RESET ROLE;")
            check_connected_user(cursor, username, username)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("CREATE TABLE test_table_2 (id INTEGER);")

            logger.info(
                "Checking that the relation user can escalate back to the database owner user"
            )
            cursor.execute(f"SET ROLE {database_name}_owner;")
            check_connected_user(cursor, username, f"{database_name}_owner")
    finally:
        if connection is not None:
            connection.close()

    connection_string = f"host={data_integrator_credentials[relation_endpoint]['read-only-endpoints'].split(':')[0]} dbname={data_integrator_credentials[relation_endpoint]['database']} user={username} password={data_integrator_credentials[relation_endpoint]['password']}"
    connection = None
    try:
        connection = psycopg2.connect(connection_string)
        with connection.cursor() as cursor:
            logger.info("Checking that the relation user can read data from the database")
            check_connected_user(cursor, username, username, primary=False)
            logger.info("Reading data from the test table")
            cursor.execute("SELECT * FROM test_table;")
            result = cursor.fetchall()
            assert len(result) == 1, "The relation user should be able to read the data"
    finally:
        if connection is not None:
            connection.close()


def check_tls(juju: JujuFixture, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on the specific PostgreSQL instance.

    Args:
        juju: The Juju fixture.
        unit_name: The name of the unit of the PostgreSQL instance.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled.
    """
    unit_address = get_unit_address(juju, unit_name)
    password = get_password()
    # Get the IP addresses of the other units to check that they
    # are connecting to the primary unit (if unit_name is the
    # primary unit name) using encrypted connections.
    app_name = unit_name.split("/")[0]
    unit_addresses = [
        f"'{get_unit_address(juju, other_unit_name)}'"
        for other_unit_name in juju.ext.model.units
        if other_unit_name.split("/")[0] == app_name and other_unit_name != unit_name
    ]
    try:
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt:
                output = execute_query_on_unit(
                    unit_address,
                    password,
                    "SHOW ssl;",
                    sslmode="require" if enabled else "disable",
                )
                tls_enabled = "on" in output

                # Check for the number of bits in the encryption algorithm used
                # on each connection. If a connection is not encrypted, None
                # is returned instead of an integer.
                connections_encryption_info = execute_query_on_unit(
                    unit_address,
                    password,
                    "SELECT bits FROM pg_stat_ssl INNER JOIN pg_stat_activity"
                    " ON pg_stat_ssl.pid = pg_stat_activity.pid"
                    " WHERE pg_stat_ssl.pid = pg_backend_pid()"
                    f" OR client_addr IN ({','.join(unit_addresses)});",
                )

                # This flag indicates whether all the connections are encrypted
                # when checking for TLS enabled or all the connections are not
                # encrypted when checking for TLS disabled.
                connections_encrypted = (
                    all(connections_encryption_info)
                    if enabled
                    else any(connections_encryption_info)
                )

                if enabled != tls_enabled or tls_enabled != connections_encrypted:
                    raise ValueError(
                        f"TLS is{' not' if not tls_enabled else ''} enabled on {unit_name}"
                    )
                return True
    except RetryError:
        return False


def check_tls_replication(juju: JujuFixture, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on the replica PostgreSQL instance.

    Args:
        juju: The Juju fixture.
        unit_name: The name of the replica of the PostgreSQL instance.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled.
    """
    unit_address = get_unit_address(juju, unit_name)
    password = get_password()

    # Check for the all replicas using encrypted connection
    output = execute_query_on_unit(
        unit_address,
        password,
        "SELECT pg_ssl.ssl, pg_sa.client_addr FROM pg_stat_ssl pg_ssl"
        " JOIN pg_stat_activity pg_sa ON pg_ssl.pid = pg_sa.pid"
        " AND pg_sa.usename = 'replication';",
    )
    return all(output[i] == enabled for i in range(0, len(output), 2))


def check_tls_patroni_api(juju: JujuFixture, unit_name: str, enabled: bool) -> bool:
    """Returns whether TLS is enabled on Patroni REST API.

    Args:
        juju: The Juju fixture.
        unit_name: The name of the unit where Patroni is running.
        enabled: check if TLS is enabled/disabled

    Returns:
        Whether TLS is enabled/disabled on Patroni REST API.
    """
    unit_address = get_unit_address(juju, unit_name)
    tls_ca = get_tls_ca(juju, unit_name, "peer")

    # If there is no TLS CA in the relation, something is wrong in
    # the relation between the TLS Certificates Operator and PostgreSQL.
    if enabled and not tls_ca:
        return False

    try:
        for attempt in Retrying(
            stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30)
        ):
            with attempt, tempfile.NamedTemporaryFile() as temp_ca_file:
                # Write the TLS CA to a temporary file to use it in a request.
                temp_ca_file.write(tls_ca.encode("utf-8"))
                temp_ca_file.seek(0)

                # The CA bundle file is used to validate the server certificate when
                # peer TLS is enabled, otherwise don't validate the internal cert.
                health_info = requests.get(
                    f"https://{unit_address}:8008/health",
                    verify=temp_ca_file.name if enabled else False,
                )
                return health_info.status_code == 200
    except RetryError:
        return False
    return False


def has_relation_exited(
    juju: JujuFixture, endpoint_one: str, endpoint_two: str, model: ModelAdapter = None
) -> bool:
    """Returns true if the relation between endpoint_one and endpoint_two has been removed."""
    relations = model.relations if model is not None else juju.ext.model.relations
    for rel in relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint_one in endpoints and endpoint_two in endpoints:
            return False
    return True


@retry(
    retry=retry_if_result(lambda x: not x),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
def primary_changed(juju: JujuFixture, old_primary: str) -> bool:
    """Checks whether the primary unit has changed.

    Args:
        juju: The Juju fixture
        old_primary: The name of the unit that was the primary before.
    """
    other_unit = next(
        unit.name
        for unit in juju.ext.model.applications[DATABASE_APP_NAME].units
        if unit.name != old_primary
    )
    primary = get_primary(juju, other_unit)
    return primary != old_primary


def restart_machine(juju: JujuFixture, unit_name: str) -> None:
    """Restart the machine where a unit run on.

    Args:
        juju: The Juju fixture
        unit_name: The name of the unit to restart the machine
    """
    raw_hostname = get_machine_from_unit(juju, unit_name)
    restart_machine_command = f"lxc restart {raw_hostname}"
    subprocess.check_call(restart_machine_command.split())


def run_command_on_unit(juju: JujuFixture, unit_name: str, command: str) -> str:
    """Run a command on a specific unit.

    Args:
        juju: The Juju fixture
        unit_name: The name of the unit to run the command on
        command: The command to run

    Returns:
        the command output if it succeeds, otherwise raises an exception.
    """
    complete_command = ["juju", "exec", "--unit", unit_name, "--", *command.split()]
    try:
        stdout = subprocess.check_output(
            " ".join(complete_command), shell=True, universal_newlines=True, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"{e.stdout} {e.stderr}")
        raise Exception(f"Expected command '{command}' to succeed instead it failed")
    return stdout


def scale_application(
    juju: JujuFixture,
    application_name: str,
    count: int,
    model: ModelAdapter = None,
    timeout=2000,
    idle_period: int = 30,
) -> None:
    """Scale a given application to a specific unit count.

    Args:
        juju: The Juju fixture
        application_name: The name of the application
        count: The desired number of units to scale to
        model: The model to scale the application in
        timeout: timeout period
        idle_period: idle period
    """
    if model is None:
        model = juju.ext.model
    change = count - len(model.applications[application_name].units)
    if change > 0:
        model.applications[application_name].add_units(change)
    elif change < 0:
        units = [unit.name for unit in model.applications[application_name].units[0:-change]]
        model.applications[application_name].destroy_units(*units)
    model.wait_for_idle(
        apps=[application_name],
        status="active",
        timeout=timeout,
        idle_period=idle_period,
        wait_for_exact_units=count,
    )


def restart_patroni(juju: JujuFixture, unit_name: str, password: str) -> None:
    """Restart Patroni on a specific unit.

    Args:
        juju: The Juju fixture
        unit_name: The name of the unit
        password: patroni password
    """
    unit_ip = get_unit_address(juju, unit_name)
    requests.post(
        f"https://{unit_ip}:8008/restart",
        auth=requests.auth.HTTPBasicAuth("patroni", password),
        verify=False,
    )


def set_password(
    juju: JujuFixture,
    username: str = "operator",
    password: str | None = None,
    database_app_name: str = DATABASE_APP_NAME,
):
    """Set a user password via secret.

    Args:
        juju: juju instance.
        username: the user to set the password.
        password: optional password to use
            instead of auto-generating
        database_app_name: name of the app for the secret

    Returns:
        the results from the action.
    """
    secret_name = "system_users_secret"

    try:
        secret_id = juju.add_secret(secret_name, content={username: password})
        juju.grant_secret(secret_id, database_app_name)

        # update the application config to include the secret
        juju.ext.model.applications[database_app_name].set_config({
            SYSTEM_USERS_PASSWORD_CONFIG: secret_id
        })
    except Exception:
        juju.update_secret(secret_name, content={username: password})


def start_machine(juju: JujuFixture, machine_name: str) -> None:
    """Start the machine where a unit run on.

    Args:
        juju: The Juju fixture
        machine_name: The name of the machine to start
    """
    start_machine_command = f"lxc start {machine_name}"
    subprocess.check_call(start_machine_command.split())


def stop_machine(juju: JujuFixture, machine_name: str) -> None:
    """Stop the machine where a unit run on.

    Args:
        juju: The Juju fixture
        machine_name: The name of the machine to stop
    """
    stop_machine_command = f"lxc stop {machine_name}"
    subprocess.check_call(stop_machine_command.split())


def switchover(
    juju: JujuFixture, current_primary: str, password: str, candidate: str | None = None
) -> None:
    """Trigger a switchover.

    Args:
        juju: The Juju fixture.
        current_primary: The current primary unit.
        password: Patroni password.
        candidate: The unit that should be elected the new primary.
    """
    primary_ip = get_unit_address(juju, current_primary)
    response = requests.post(
        f"https://{primary_ip}:8008/switchover",
        json={
            "leader": current_primary.replace("/", "-"),
            "candidate": candidate.replace("/", "-") if candidate else None,
        },
        auth=requests.auth.HTTPBasicAuth("patroni", password),
        verify=False,
    )
    assert response.status_code == 200
    app_name = current_primary.split("/")[0]
    for attempt in Retrying(stop=stop_after_attempt(30), wait=wait_fixed(2), reraise=True):
        with attempt:
            response = requests.get(f"https://{primary_ip}:8008/cluster", verify=False)
            assert response.status_code == 200
            standbys = len([
                member for member in response.json()["members"] if member["role"] == "sync_standby"
            ])
            assert standbys == len(juju.ext.model.applications[app_name].units) - 1


def wait_for_idle_on_blocked(
    juju: JujuFixture,
    database_app_name: str,
    unit_number: int,
    other_app_name: str,
    status_message: str,
):
    """Wait for specific applications becoming idle and blocked together."""
    unit = f"{database_app_name}/{unit_number}"
    juju.ext.model.wait_for_idle(apps=[other_app_name], status="active")
    juju.wait(
        lambda status: (
            status.apps[database_app_name].units[unit].workload_status.current == "blocked"
            and status.apps[database_app_name].units[unit].workload_status.message
            == status_message
        )
    )


def wait_for_relation_removed_between(
    juju: JujuFixture, endpoint_one: str, endpoint_two: str, model: ModelAdapter = None
) -> None:
    """Wait for relation to be removed before checking if it's waiting or idle.

    Args:
        juju: running OpsTest instance
        endpoint_one: one endpoint of the relation. Doesn't matter if it's provider or requirer.
        endpoint_two: the other endpoint of the relation.
        model: optional model to check for the relation.
    """
    try:
        for attempt in Retrying(stop=stop_after_delay(3 * 60), wait=wait_fixed(3)):
            with attempt:
                if has_relation_exited(juju, endpoint_one, endpoint_two, model):
                    break
    except RetryError:
        assert False, "Relation failed to exit after 3 minutes."


### Ported Mysql jubilant helpers


def execute_queries_on_unit(
    unit_address: str, username: str, password: str, queries: list[str], database: str
) -> list:
    """Execute given PostgreSQL queries on a unit.

    Args:
        unit_address: The public IP address of the unit to execute the queries on
        username: The PostgreSQL username
        password: The PostgreSQL password
        queries: A list of queries to execute
        database: Database to execute in

    Returns:
        A list of rows that were potentially queried
    """
    with (
        psycopg2.connect(
            f"dbname='{database}' user='{username}' host='{unit_address}' password='{password}' connect_timeout=10"
        ) as connection,
        connection.cursor() as cursor,
    ):
        for query in queries:
            cursor.execute(query)
        output = list(itertools.chain(*cursor.fetchall()))

    return output


##########################################
#                                        #
#      Partially Ported HA Helpers       #
#                                        #
##########################################


def app_name(
    juju: JujuFixture, application_name: str = "postgresql", model: ModelAdapter | None = None
) -> str | None:
    """Returns the name of the cluster running PostgreSQL.

    This is important since not all deployments of the PostgreSQL charm have the application name
    "postgresql".

    Note: if multiple clusters are running PostgreSQL this will return the one first found.
    """
    if model is None:
        model = juju.ext.model
    status = juju.status()
    for app in status.apps:
        if (
            application_name in status.apps[app].charm
            and APPLICATION_NAME not in status.apps[app].charm
        ):
            return app

    return None


def change_patroni_setting(
    juju: JujuFixture,
    setting: str,
    value: int | bool,
    password: str,
    use_random_unit: bool = False,
    tls: bool = False,
) -> None:
    """Change the value of one of the Patroni settings.

    Args:
        juju: Juju fixture.
        setting: the name of the setting.
        value: the value to assign to the setting.
        password: Patroni password.
        use_random_unit: whether to use a random unit (default is False,
            so it uses the primary).
        tls: if Patroni is serving using tls.
    """
    for attempt in Retrying(stop=stop_after_delay(30 * 2), wait=wait_fixed(3)):
        with attempt:
            app = app_name(juju)
            if use_random_unit:
                unit = random.choice(juju.ext.model.applications[app].units).name
                unit_ip = get_unit_address(juju, unit)
            else:
                primary_name = get_primary(juju, app)
                unit_ip = get_unit_address(juju, primary_name)
            requests.patch(
                f"https://{unit_ip}:8008/config",
                json={setting: value},
                verify=False,
                auth=requests.auth.HTTPBasicAuth("patroni", password),
            )


def get_cluster_roles(
    juju: JujuFixture, unit_name: str, use_ip_from_inside: bool = False
) -> dict[str, str | list[str] | None]:
    """Returns whether the unit a replica in the cluster."""
    unit_ip = (
        get_ip_from_inside_the_unit(juju, unit_name)
        if use_ip_from_inside
        else get_unit_ip(juju, unit_name)
    )

    members = {"replicas": [], "primaries": [], "sync_standbys": []}
    cluster_info = requests.get(f"https://{unit_ip}:8008/cluster", verify=False)
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


def get_ip_from_inside_the_unit(juju: JujuFixture, unit_name: str) -> str:
    command = f"exec --unit {unit_name} -- hostname -I"
    try:
        stdout = juju.cli(*command.split())
    except jubilant.CLIError as e:
        raise ProcessError(
            "Expected command %s to succeed instead it failed: %s %s",
            command,
            e.returncode,
            e.stderr,
        )
    return stdout.splitlines()[0].strip()


def get_unit_ip(juju: JujuFixture, unit_name: str, model: ModelAdapter | None = None) -> str:
    """Wrapper for getting unit ip.

    Args:
        juju: Juju fixture.
        unit_name: The name of the unit to get the address
        model: Optional model instance to use
    Returns:
        The (str) ip of the unit
    """
    if model is None:
        application = unit_name.split("/")[0]
        for unit in juju.ext.model.applications[application].units:
            if unit.name == unit_name:
                break
        machine = next(
            iter(
                machine
                for id_, machine in juju.status().machines.items()
                if id_ == unit.status.machine
            )
        )
        return instance_ip(juju, machine.hostname)
    else:
        return get_unit_address(juju, unit_name)


def instance_ip(juju: JujuFixture, instance: str) -> str:
    """Translate juju instance name to IP.

    Args:
        juju: Juju fixture.
        instance: The name of the instance

    Returns:
        The (str) IP address of the instance
    """
    output = juju.cli("machines")

    for line in output.splitlines():
        if instance in line:
            return line.split()[2]
