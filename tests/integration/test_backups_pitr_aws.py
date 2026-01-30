#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .conftest import AWS
from .helpers import pitr_backup_operations

S3_INTEGRATOR_APP_NAME = "s3-integrator"
TLS_CERTIFICATES_APP_NAME = "self-signed-certificates"
TLS_CHANNEL = "1/stable"
TLS_CONFIG = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_pitr_backup_aws(
    ops_test: OpsTest, aws_cloud_configs: tuple[dict, dict], charm
) -> None:
    """Build, deploy two units of PostgreSQL and do backup in AWS. Then, write new data into DB, switch WAL file and test point-in-time-recovery restore action."""
    config, credentials = aws_cloud_configs

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
