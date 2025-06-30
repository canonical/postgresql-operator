import json
import subprocess
from typing import Dict

import jubilant

from constants import PEER

from .helpers import DATABASE_APP_NAME, SecretNotFoundError


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


def roles_attributes(predefined_roles: Dict, combination: str) -> Dict:
    auto_escalate_to_database_owner = False
    connect = False
    create_databases = False
    create_objects = False
    escalate_to_database_owner = False
    read_data = False
    read_stats = False
    set_up_predefined_catalog_roles = False
    set_user = False
    write_data = False
    for role in combination.split(","):
        if role not in predefined_roles:
            raise ValueError(f"Role {role} not found in predefined roles.")

        # Whether the relation user is auto-escalated to the database owner user at login
        # (True or False).
        if not auto_escalate_to_database_owner:
            auto_escalate_to_database_owner = predefined_roles[role]["auto-escalate-to-database-owner"]

        role_permissions = predefined_roles[role]["permissions"]

        # Permission to connect to the requested database (True value) or to all databases
        # ("*" value).
        role_can_connect = role_permissions["connect"]
        if not connect or role_can_connect == "*":
            connect = role_can_connect

        # Permission to create databases (True or False).
        if not create_databases:
            create_databases = role_permissions["create-databases"]

        # Permission to create objects in the requested database (True value) or in all databases
        # ("*" value).
        role_can_create_objects = role_permissions["create-objects"]
        if not create_objects or role_can_create_objects == "*":
            create_objects = role_can_create_objects

        # Permission to escalate to the database owner user in the requested database (True value)
        # or in all databases ("*" value).
        role_can_escalate_to_database_owner = role_permissions["escalate-to-database-owner"]
        if not escalate_to_database_owner or role_can_escalate_to_database_owner == "*":
            escalate_to_database_owner = role_can_escalate_to_database_owner

        # Permission to read data in the requested database (True value) or in all databases
        # ("*" value).
        role_can_read_data = role_permissions["read-data"]
        if not read_data or role_can_read_data == "*":
            read_data = role_can_read_data

        read_stats = role_permissions["read-stats"]

        # Permission to set up predefined catalog roles ("*" for all databases or False for not being
        # able to do it).
        role_can_set_up_predefined_catalog_roles = role_permissions["set-up-predefined-catalog-roles"]
        if not set_up_predefined_catalog_roles or role_can_set_up_predefined_catalog_roles == "*":
            set_up_predefined_catalog_roles = role_can_set_up_predefined_catalog_roles

        # Permission to call the set_user function (True or False).
        if not set_user:
            set_user = role_permissions["set-user"]

        # Permission to write data in the requested database (True value) or in all databases
        # ("*" value).
        role_can_write_data = role_permissions["write-data"]
        if not write_data or role_can_write_data == "*":
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
            "set-up-predefined-catalog-roles": set_up_predefined_catalog_roles,
            "set-user": set_user,
            "write-data": write_data,
        },
    }
