import json
import subprocess

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
