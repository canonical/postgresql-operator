#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import os
import pathlib
import uuid

import boto3
import pytest
import pytest_operator.plugin
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import construct_endpoint

AWS = "AWS"
GCP = "GCP"


@pytest.fixture(scope="module")
async def cloud_configs(ops_test: OpsTest) -> None:
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
            "access-key": os.environ.get("AWS_ACCESS_KEY"),
            "secret-key": os.environ.get("AWS_SECRET_KEY"),
        },
        GCP: {
            "access-key": os.environ.get("GCP_ACCESS_KEY"),
            "secret-key": os.environ.get("GCP_SECRET_KEY"),
        },
    }
    yield configs, credentials
    # Delete the previously created objects.
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


@pytest.fixture(scope="module")
def ops_test(
    ops_test: pytest_operator.plugin.OpsTest, pytestconfig
) -> pytest_operator.plugin.OpsTest:
    _build_charm = ops_test.build_charm

    async def build_charm(charm_path) -> pathlib.Path:
        if pathlib.Path(charm_path) == pathlib.Path("."):
            # Building mysql charm
            return await _build_charm(
                charm_path,
                bases_index=pytestconfig.option.mysql_charm_bases_index,
            )
        else:
            return await _build_charm(charm_path)

    ops_test.build_charm = build_charm
    return ops_test


@pytest.fixture(scope="module")
async def charm(ops_test: OpsTest):
    """Build the charm-under-test."""
    # Build charm from local source folder.
    yield await ops_test.build_charm(".")
