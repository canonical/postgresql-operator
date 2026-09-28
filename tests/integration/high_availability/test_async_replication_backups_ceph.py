#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from collections.abc import Generator

import jubilant
import pytest
from jubilant import CLIError, Juju, TaskError
from tenacity import Retrying, retry_if_exception_type, stop_after_delay, wait_fixed

from .. import architecture
from ..conftest import ConnectionInformation
from .high_availability_helpers_new import (
    get_app_leader,
    get_app_units,
    get_db_primary_unit,
    wait_for_apps_status,
)

DB_APP_1 = "db1"
DB_APP_2 = "db2"
PRIMARY_S3_APP = "s3-primary"
STANDBY_S3_APP = "s3-standby"
MINUTE_SECS = 60
EXPECTED_STANDBY_BACKUP_MESSAGE = (
    "Backups are not supported on a standby cluster. "
    "Run create-backup on the primary cluster instead."
)

logger = logging.getLogger(__name__)
logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.fixture(scope="module")
def first_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Return the first model."""
    yield juju.model


@pytest.fixture(scope="module")
def second_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Create and return the second model."""
    model_name = f"{juju.model}-other"

    logger.info("Creating model: %s", model_name)
    juju.add_model(model_name)
    model_2 = Juju(model=model_name)
    model_2.cli("set-model-constraints", f"arch={architecture.architecture}")

    yield model_name
    if request.config.getoption("--keep-models"):
        return

    logger.info("Destroying model: %s", model_name)
    juju.destroy_model(model_name, destroy_storage=True, force=True)


def _configure_s3_integrator(
    model: Juju,
    app_name: str,
    database_app_name: str,
    microceph: ConnectionInformation,
) -> None:
    """Deploy and configure one s3-integrator app against microceph RGW."""
    if app_name not in model.status().apps:
        model.deploy(
            "s3-integrator",
            app=app_name,
            channel="1/stable",
            config={
                "endpoint": f"https://{microceph.host}",
                "bucket": f"{app_name}-bucket",
                "path": "/pg",
                "region": "",
                "s3-uri-style": "path",
                "tls-ca-chain": microceph.cert,
            },
        )

    # Wait until Juju has finished unit setup and registered charm actions.
    model.wait(
        ready=lambda status: (
            app_name in status.apps
            and bool(status.apps[app_name].units)
            and jubilant.all_agents_idle(status, app_name)
            and wait_for_apps_status(jubilant.all_blocked, app_name)
        ),
        timeout=10 * MINUTE_SECS,
    )

    model.run(
        unit=f"{app_name}/0",
        action="sync-s3-credentials",
        params={
            "access-key": microceph.access_key_id,
            "secret-key": microceph.secret_access_key,
        },
        wait=5 * MINUTE_SECS,
    ).raise_on_failure()

    model.integrate(database_app_name, app_name)


@pytest.mark.abort_on_fail
def test_standby_backup_rejected_with_clear_message(
    first_model: str,
    second_model: str,
    charm: str,
    microceph: ConnectionInformation,
) -> None:
    """Validate backup behavior with async replication and Ceph-backed S3."""
    constraints = {"arch": architecture.architecture}
    configuration = {"profile": "testing"}

    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    logger.info("Deploying PostgreSQL clusters")
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

    logger.info("Deploying and configuring S3 integrators")
    _configure_s3_integrator(model_1, PRIMARY_S3_APP, DB_APP_1, microceph)
    _configure_s3_integrator(model_2, STANDBY_S3_APP, DB_APP_2, microceph)

    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1, PRIMARY_S3_APP),
        timeout=30 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2, STANDBY_S3_APP),
        timeout=30 * MINUTE_SECS,
    )

    logger.info("Wiring cross-model async replication")
    model_1.offer(f"{first_model}.{DB_APP_1}", endpoint="replication-offer")
    model_2.consume(f"{first_model}.{DB_APP_1}")
    for attempt in Retrying(
        stop=stop_after_delay(3 * MINUTE_SECS),
        wait=wait_fixed(5),
        retry=retry_if_exception_type(CLIError),
        reraise=True,
    ):
        with attempt:
            model_2.integrate(f"{DB_APP_1}", f"{DB_APP_2}:replication")

    model_1.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_1),
        timeout=10 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_2),
        timeout=10 * MINUTE_SECS,
    )

    logger.info("Running create-replication")
    model_1.run(
        unit=get_app_leader(model_1, DB_APP_1),
        action="create-replication",
        wait=5 * MINUTE_SECS,
    ).raise_on_failure()

    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1),
        timeout=20 * MINUTE_SECS,
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=20 * MINUTE_SECS,
    )

    logger.info("Creating backup on primary cluster")
    primary_unit = get_db_primary_unit(model_1, DB_APP_1)
    replica_unit = next(unit for unit in get_app_units(model_1, DB_APP_1) if unit != primary_unit)
    model_1.run(unit=replica_unit, action="create-backup", wait=5 * MINUTE_SECS).raise_on_failure()

    logger.info("Ensuring backup is rejected on standby cluster")
    with pytest.raises(TaskError) as exc_info:
        model_2.run(
            unit=get_app_leader(model_2, DB_APP_2),
            action="create-backup",
            wait=5 * MINUTE_SECS,
        )

    assert exc_info.value.task.status == "failed"
    assert EXPECTED_STANDBY_BACKUP_MESSAGE in exc_info.value.task.message
