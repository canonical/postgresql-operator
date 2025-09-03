# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import uuid

import boto3
import jubilant
import pytest
from pytest_operator.plugin import OpsTest

from . import architecture
from .helpers import construct_endpoint
from .jubilant_helpers import RoleAttributeValue

AWS = "AWS"
GCP = "GCP"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def charm():
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./postgresql_ubuntu@24.04-{architecture.architecture}.charm"


def get_cloud_config(cloud: str) -> tuple[dict[str, str], dict[str, str]] | None:
    # Define some configurations and credentials.
    if cloud == AWS:
        return {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid4()}",
            "region": "us-east-1",
        }, {
            "access-key": os.environ["AWS_ACCESS_KEY"],
            "secret-key": os.environ["AWS_SECRET_KEY"],
        }
    elif cloud == GCP:
        return {
            "endpoint": "https://storage.googleapis.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid4()}",
            "region": "",
        }, {
            "access-key": os.environ["GCP_ACCESS_KEY"],
            "secret-key": os.environ["GCP_SECRET_KEY"],
        }
    return None


def cleanup_cloud(config: dict[str, str], credentials: dict[str, str]) -> None:
    # Delete the previously created objects.
    logger.info("deleting the previously created backups")
    session = boto3.session.Session(
        aws_access_key_id=credentials["access-key"],
        aws_secret_access_key=credentials["secret-key"],
        region_name=config["region"],
    )
    s3 = session.resource(
        "s3", endpoint_url=construct_endpoint(config["endpoint"], config["region"])
    )
    bucket = s3.Bucket(config["bucket"])
    # GCS doesn't support batch delete operation, so delete the objects one by one.
    for bucket_object in bucket.objects.filter(Prefix=config["path"].lstrip("/")):
        bucket_object.delete()


@pytest.fixture(scope="module")
async def aws_cloud_configs(ops_test: OpsTest) -> None:
    if (
        not os.environ.get("AWS_ACCESS_KEY", "").strip()
        or not os.environ.get("AWS_SECRET_KEY", "").strip()
    ):
        pytest.skip("AWS configs not set")
        return

    config, credentials = get_cloud_config(AWS)
    yield config, credentials

    cleanup_cloud(config, credentials)


@pytest.fixture(scope="module")
async def gcp_cloud_configs(ops_test: OpsTest) -> None:
    if (
        not os.environ.get("GCP_ACCESS_KEY", "").strip()
        or not os.environ.get("GCP_SECRET_KEY", "").strip()
    ):
        pytest.skip("GCP configs not set")
        return

    config, credentials = get_cloud_config(GCP)
    yield config, credentials

    cleanup_cloud(config, credentials)


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    """Pytest fixture that wraps :meth:`jubilant.with_model`.

    This adds command line parameter ``--keep-models`` (see help for details).
    """
    controller = request.config.getoption("--controller")
    model = request.config.getoption("--model")
    controller_and_model = None
    if controller and model:
        controller_and_model = f"{controller}:{model}"
    elif controller:
        controller_and_model = controller
    elif model:
        controller_and_model = model
    keep_models = bool(request.config.getoption("--keep-models"))

    if controller_and_model:
        juju = jubilant.Juju(model=controller_and_model)  # type: ignore
        yield juju
        log = juju.debug_log(limit=1000)
    else:
        with jubilant.temp_model(keep=keep_models) as juju:
            yield juju
            log = juju.debug_log(limit=1000)

    if request.session.testsfailed:
        print(log, end="")


@pytest.fixture(scope="module")
def predefined_roles() -> dict:
    """Return a list of predefined roles with their expected permissions."""
    return {
        "": {
            "auto-escalate-to-database-owner": RoleAttributeValue.REQUESTED_DATABASE,
            "permissions": {
                "connect": RoleAttributeValue.REQUESTED_DATABASE,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.REQUESTED_DATABASE,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_stats": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_read": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_dml": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.ALL_DATABASES,
            },
        },
        "charmed_backup": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.YES,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
        "charmed_dba": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.YES,
                "write-data": RoleAttributeValue.ALL_DATABASES,
            },
        },
        "charmed_admin": {
            "auto-escalate-to-database-owner": RoleAttributeValue.ALL_DATABASES,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.NO,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.ALL_DATABASES,
                "read-data": RoleAttributeValue.ALL_DATABASES,
                "read-stats": RoleAttributeValue.ALL_DATABASES,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.NO,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.ALL_DATABASES,
            },
        },
        "CREATEDB": {
            "auto-escalate-to-database-owner": RoleAttributeValue.NO,
            "permissions": {
                "connect": RoleAttributeValue.ALL_DATABASES,
                "create-databases": RoleAttributeValue.YES,
                "create-objects": RoleAttributeValue.NO,
                "escalate-to-database-owner": RoleAttributeValue.NO,
                "read-data": RoleAttributeValue.NO,
                "read-stats": RoleAttributeValue.NO,
                "run-backup-commands": RoleAttributeValue.NO,
                "set-up-predefined-catalog-roles": RoleAttributeValue.YES,
                "set-user": RoleAttributeValue.NO,
                "write-data": RoleAttributeValue.NO,
            },
        },
    }


@pytest.fixture(scope="module")
def predefined_roles_combinations() -> list:
    """Return a list of valid combinations of predefined roles."""
    return [
        ("",),
        ("charmed_stats",),
        ("charmed_read",),
        ("charmed_dml",),
        ("charmed_admin",),
        ("charmed_admin", "CREATEDB"),
    ]
