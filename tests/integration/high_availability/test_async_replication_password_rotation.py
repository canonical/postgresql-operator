#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Test that system-user password rotations propagate to the standby cluster's Juju secrets.

Covers DPE-11134: rotating the `operator` password on the primary cluster (Rome) through a
Juju user secret must also update the standby cluster's (Lisbon) Juju app secret, since the
app secret is the recovery credential for a lost datacenter.
"""

import json
import logging
from collections.abc import Generator

import jubilant
import pytest
from jubilant import Juju
from single_kernel_postgresql.config.literals import PEER_RELATION
from tenacity import Retrying, stop_after_attempt, wait_fixed

from .. import architecture
from ..jubilant_helpers import db_connect, get_unit_address
from .high_availability_helpers_new import (
    get_app_leader,
    get_db_primary_unit,
    get_db_standby_leader_unit,
    wait_for_apps_status,
)

DB_APP_1 = "db1"
DB_APP_2 = "db2"
MINUTE_SECS = 60
OPERATOR_USER = "operator"

# Juju app secret holding the system users' passwords, per cluster.
APP_SECRET_LABEL_1 = f"{PEER_RELATION}.{DB_APP_1}.app"
APP_SECRET_LABEL_2 = f"{PEER_RELATION}.{DB_APP_2}.app"


SYSTEM_USERS_SECRET_NAME = "system-users-secret"
OLD_PASSWORD = "dpe-11134-old-password"
NEW_PASSWORD = "dpe-11134-new-password"


@pytest.fixture(scope="module")
def first_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Creates and return the first model."""
    yield juju.model


@pytest.fixture(scope="module")
def second_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Creates and returns the second model."""
    model_name = f"{juju.model}-other"

    logging.info(f"Creating model: {model_name}")
    juju.add_model(model_name)

    yield model_name
    if request.config.getoption("--keep-models"):
        return

    logging.info("Destroying model: {model_name}")
    juju.destroy_model(model_name, destroy_storage=True, force=True)


@pytest.fixture()
def fast_forward_both(first_model: str, second_model: str) -> Generator:
    """Speed up the update-status hook on both models."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)
    model_1.model_config({"update-status-hook-interval": "10s"})
    model_2.model_config({"update-status-hook-interval": "10s"})
    yield
    model_1.model_config({"update-status-hook-interval": "5m"})
    model_2.model_config({"update-status-hook-interval": "5m"})


def test_deploy(first_model: str, second_model: str, charm: str) -> None:
    """Deploy both PostgreSQL clusters."""
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}

    logging.info("Deploying postgresql clusters")
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)
    model_1.deploy(
        charm=charm,
        app=DB_APP_1,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=3,
    )
    model_2.deploy(
        charm=charm,
        app=DB_APP_2,
        base="ubuntu@24.04",
        config=configuration,
        constraints=constraints,
        num_units=3,
    )

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1),
        timeout=20 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=20 * MINUTE_SECS,
    )


def test_async_relate(first_model: str, second_model: str) -> None:
    """Relate the two PostgreSQL clusters."""
    logging.info("Creating offers in first model")
    model_1 = Juju(model=first_model)
    model_1.offer(f"{first_model}.{DB_APP_1}", endpoint="replication-offer")

    logging.info("Consuming offer in second model")
    model_2 = Juju(model=second_model)
    model_2.consume(f"{first_model}.{DB_APP_1}")

    logging.info("Relating the two postgresql clusters")
    model_2.integrate(f"{DB_APP_1}", f"{DB_APP_2}:replication")

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=10 * MINUTE_SECS,
    )


def test_create_replication(first_model: str, second_model: str) -> None:
    """Run the create-replication action and wait for the applications to settle."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logging.info("Running create replication action")
    model_1.run(
        unit=get_app_leader(model_1, DB_APP_1), action="create-replication", wait=5 * MINUTE_SECS
    ).raise_on_failure()

    logging.info("Waiting for the applications to settle")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1),
        timeout=20 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=20 * MINUTE_SECS,
    )


def test_configure_system_users_secret(first_model: str, second_model: str) -> None:
    """Configure a Juju user secret holding the operator password in the primary cluster."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)
    secret_id = model_1.cli(
        "add-secret", SYSTEM_USERS_SECRET_NAME, f"{OPERATOR_USER}={OLD_PASSWORD}"
    ).strip()
    model_1.cli("grant-secret", SYSTEM_USERS_SECRET_NAME, DB_APP_1)
    model_1.config(DB_APP_1, {"system-users": secret_id})

    logging.info("Waiting for the secret to be processed by the primary cluster")
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_agents_idle, DB_APP_1),
        timeout=10 * MINUTE_SECS,
    )

    # The primary cluster must now store the configured password in its app secret.
    app_secret = get_app_secret_content(model_1, APP_SECRET_LABEL_1)
    assert app_secret.get(f"{OPERATOR_USER}-password") == OLD_PASSWORD, (
        f"Primary cluster app secret was not updated with the configured password: {app_secret}"
    )

    # The configured password must be usable on both clusters (the standby cluster
    # receives it through data replication).
    assert_credentials_accepted(model_1, DB_APP_1, OLD_PASSWORD)
    assert_credentials_accepted(model_2, DB_APP_2, OLD_PASSWORD)


def test_rotate_system_users_secret(
    first_model: str, second_model: str, fast_forward_both: None
) -> None:
    """Rotate the system users secret and check both clusters converge to the new password."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    # Wait for both clusters to settle before rotating.
    model_1.wait(ready=wait_for_apps_status(jubilant.all_agents_idle, DB_APP_1), timeout=600)
    model_2.wait(ready=wait_for_apps_status(jubilant.all_agents_idle, DB_APP_2), timeout=600)

    logging.info("Rotating the operator password through the user secret")
    model_1.cli("update-secret", SYSTEM_USERS_SECRET_NAME, f"{OPERATOR_USER}={NEW_PASSWORD}")

    # Give both clusters time to process the rotation (secret-changed hooks and
    # cross-cluster secret synchronisation).
    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=10 * MINUTE_SECS,
    )

    # The new password must be accepted by both clusters.
    assert_credentials_accepted(model_1, DB_APP_1, NEW_PASSWORD)
    assert_credentials_accepted(model_2, DB_APP_2, NEW_PASSWORD)

    # The password rotation must be reflected in the Juju app secrets of both clusters.
    # https://warthogs.atlassian.net/browse/DPE-11134
    app_secret_1 = get_app_secret_content(model_1, APP_SECRET_LABEL_1)
    app_secret_2 = get_app_secret_content(model_2, APP_SECRET_LABEL_2)
    assert app_secret_1.get("operator-password") == NEW_PASSWORD, (
        f"Primary cluster app secret holds the wrong password: {app_secret_1}"
    )
    assert app_secret_2.get("operator-password") == NEW_PASSWORD, (
        f"Standby cluster app secret holds the wrong password: {app_secret_2}"
    )


def assert_credentials_accepted(model: Juju, app: str, password: str) -> None:
    """Assert that the operator user can connect to the database with the given password."""
    for attempt in Retrying(stop=stop_after_attempt(10), wait=wait_fixed(3), reraise=True):
        with attempt:
            if app == DB_APP_1:
                unit = get_db_primary_unit(model, app)
            else:
                unit = get_db_standby_leader_unit(model, app)
            address = get_unit_address(model, unit)
            with db_connect(address, password) as connection:
                cursor = connection.cursor()
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
                cursor.close()


def get_app_secret_content(model: Juju, label: str) -> dict[str, str]:
    """Return the content of the Juju secret with the given label in the given model."""
    for secret_uri in list_secret_ids(model):
        output = model.cli("show-secret", "--reveal", "--format", "json", secret_uri)
        secret_data = json.loads(output)
        if secret_data[secret_uri].get("label") == label:
            return secret_data[secret_uri]["content"]["Data"]
    raise AssertionError(f"Secret with label {label} not found in model {model.model}")


def list_secret_ids(model: Juju) -> list[str]:
    """List the Juju secret ids in the given model."""
    return [line.split()[0] for line in model.cli("list-secrets").splitlines()[1:] if line]
