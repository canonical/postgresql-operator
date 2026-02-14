#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .backup_helpers import pitr_backup_operations
from .conftest import AWS

S3_INTEGRATOR_APP_NAME = "s3-integrator"
TLS_CERTIFICATES_APP_NAME = "self-signed-certificates"
TLS_CHANNEL = "1/stable"

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
        TLS_CHANNEL,
        credentials,
        AWS,
        config,
        charm,
    )
