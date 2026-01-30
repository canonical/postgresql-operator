#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .conftest import ConnectionInformation
from .helpers import pitr_backup_operations

S3_INTEGRATOR_APP_NAME = "s3-integrator"
TLS_CERTIFICATES_APP_NAME = "self-signed-certificates"
TLS_CHANNEL = "1/stable"
TLS_CONFIG = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def cloud_credentials(microceph: ConnectionInformation) -> dict[str, str]:
    """Read cloud credentials."""
    return {
        "access-key": microceph.access_key_id,
        "secret-key": microceph.secret_access_key,
    }


@pytest.fixture(scope="session")
def cloud_configs(microceph: ConnectionInformation):
    return {
        "endpoint": f"https://{microceph.host}",
        "bucket": microceph.bucket,
        "path": "/pg",
        "region": "",
        "s3-uri-style": "path",
        "tls-ca-chain": microceph.cert,
    }


@pytest.mark.abort_on_fail
async def test_pitr_backup_ceph(
    ops_test: OpsTest, cloud_configs, cloud_credentials, charm
) -> None:
    """Build, deploy two units of PostgreSQL and do backup in AWS. Then, write new data into DB, switch WAL file and test point-in-time-recovery restore action."""
    await pitr_backup_operations(
        ops_test,
        S3_INTEGRATOR_APP_NAME,
        None,
        None,
        None,
        cloud_credentials,
        "ceph",
        cloud_configs,
        charm,
    )
