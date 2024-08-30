#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import uuid
from typing import Dict, Tuple

import boto3
import pytest as pytest
from pytest_operator.plugin import OpsTest

from . import architecture
from .helpers import (
    construct_endpoint,
    pitr_backup_operations,
)
from .juju_ import juju_major_version

S3_INTEGRATOR_APP_NAME = "s3-integrator"
if juju_major_version < 3:
    TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"
    if architecture.architecture == "arm64":
        TLS_CHANNEL = "legacy/edge"
    else:
        TLS_CHANNEL = "legacy/stable"
    TLS_CONFIG = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    TLS_CERTIFICATES_APP_NAME = "self-signed-certificates"
    if architecture.architecture == "arm64":
        TLS_CHANNEL = "latest/edge"
    else:
        TLS_CHANNEL = "latest/stable"
    TLS_CONFIG = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)

AWS = "AWS"
GCP = "GCP"


@pytest.fixture(scope="module")
async def cloud_configs(github_secrets) -> None:
    # Define some configurations and credentials.
    configs = {
        AWS: {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-vm/{uuid.uuid1()}",
            "region": "us-east-1",
        },
        GCP: {
            "endpoint": "https://storage.googleapis.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-vm/{uuid.uuid1()}",
            "region": "",
        },
    }
    credentials = {
        AWS: {
            "access-key": github_secrets["AWS_ACCESS_KEY"],
            "secret-key": github_secrets["AWS_SECRET_KEY"],
        },
        GCP: {
            "access-key": github_secrets["GCP_ACCESS_KEY"],
            "secret-key": github_secrets["GCP_SECRET_KEY"],
        },
    }
    yield configs, credentials
    # Delete the previously created objects.
    logger.info("deleting the previously created backups")
    for cloud, config in configs.items():
        session = boto3.session.Session(
            aws_access_key_id=credentials[cloud]["access-key"],
            aws_secret_access_key=credentials[cloud]["secret-key"],
            region_name=config["region"],
        )
        s3 = session.resource(
            "s3", endpoint_url=construct_endpoint(config["endpoint"], config["region"])
        )
        bucket = s3.Bucket(config["bucket"])
        # GCS doesn't support batch delete operation, so delete the objects one by one.
        for bucket_object in bucket.objects.filter(Prefix=config["path"].lstrip("/")):
            bucket_object.delete()


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_pitr_backup_aws(ops_test: OpsTest, cloud_configs: Tuple[Dict, Dict], charm) -> None:
    """Build, deploy two units of PostgreSQL and do backup in AWS. Then, write new data into DB, switch WAL file and test point-in-time-recovery restore action."""
    config = cloud_configs[0][AWS]
    credentials = cloud_configs[1][AWS]

    await pitr_backup_operations(
        ops_test,
        S3_INTEGRATOR_APP_NAME,
        TLS_CERTIFICATES_APP_NAME,
        TLS_CONFIG,
        TLS_CHANNEL,
        credentials,
        AWS,
        config,
        charm,
    )


@pytest.mark.group(2)
@pytest.mark.abort_on_fail
async def test_pitr_backup_gcp(ops_test: OpsTest, cloud_configs: Tuple[Dict, Dict], charm) -> None:
    """Build, deploy two units of PostgreSQL and do backup in GCP. Then, write new data into DB, switch WAL file and test point-in-time-recovery restore action."""
    config = cloud_configs[0][GCP]
    credentials = cloud_configs[1][GCP]

    await pitr_backup_operations(
        ops_test,
        S3_INTEGRATOR_APP_NAME,
        TLS_CERTIFICATES_APP_NAME,
        TLS_CONFIG,
        TLS_CHANNEL,
        credentials,
        GCP,
        config,
        charm,
    )
