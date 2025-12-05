# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import subprocess
import uuid

import boto3
import pytest
from pytest_operator.plugin import OpsTest

from . import architecture
from .helpers import construct_endpoint
from .juju_ import juju_major_version

AWS = "AWS"
GCP = "GCP"

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def charm():
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./postgresql_ubuntu@22.04-{architecture.architecture}.charm"


@pytest.fixture(scope="session", autouse=True)
def idle_connection_timeout():
    """Set controller idle-connection-timeout to prevent premature disconnections.

    Requires Juju 3+. Skipped on Juju 2.
    """
    if juju_major_version < 3:
        return

    logger.info("Setting controller idle-connection-timeout to 90s")

    # Set the controller config
    result = subprocess.run(
        ["juju", "controller-config", "idle-connection-timeout=90s"],
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    assert result.returncode == 0, f"Failed to set controller config: {result.stderr}"

    # Verify the setting was applied correctly
    result = subprocess.run(
        ["juju", "controller-config", "idle-connection-timeout"],
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    assert result.returncode == 0, f"Failed to verify controller config: {result.stderr}"

    # Check that the correct value was set
    actual_value = result.stdout.strip()
    expected_value = "90s"
    assert actual_value == expected_value, (
        f"Controller config not set correctly. Expected: {expected_value}, Got: {actual_value}"
    )
    logger.info(f"Controller idle-connection-timeout verified: {actual_value}")


def get_cloud_config(cloud: str) -> tuple[dict[str, str], dict[str, str]]:
    # Define some configurations and credentials.
    if cloud == AWS:
        return {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid1()}",
            "region": "us-east-1",
        }, {
            "access-key": os.environ["AWS_ACCESS_KEY"],
            "secret-key": os.environ["AWS_SECRET_KEY"],
        }
    elif cloud == GCP:
        return {
            "endpoint": "https://storage.googleapis.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid1()}",
            "region": "us-east-1",
        }, {
            "access-key": os.environ["GCP_ACCESS_KEY"],
            "secret-key": os.environ["GCP_SECRET_KEY"],
        }


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
