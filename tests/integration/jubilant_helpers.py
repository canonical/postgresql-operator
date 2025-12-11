import json
import logging
import subprocess
import time
from enum import Enum

import jubilant
import psycopg2

from constants import PEER

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
        "Fixed permissions on temp tablespace directory (persistent storage), "
        "existing tablespace remains valid"
    )

    if expected_message in result.stdout:
        logger.info(f"✓ Found expected log message in {unit_name} logs")
        return True

    logger.warning(
        f"Expected log message not found in {unit_name} logs. "
        "This may indicate the code path was not triggered or permissions were already correct."
    )
    return False
