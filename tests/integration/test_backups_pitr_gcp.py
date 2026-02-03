#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from pytest_operator.plugin import OpsTest

from .backup_helpers import pitr_backup_operations
from .conftest import GCP
from .juju_ import juju_major_version

CANNOT_RESTORE_PITR = "cannot restore PITR, juju debug-log for details"
S3_INTEGRATOR_APP_NAME = "s3-integrator"
if juju_major_version < 3:
    TLS_CERTIFICATES_APP_NAME = "tls-certificates-operator"
    TLS_CHANNEL = "legacy/stable"
    TLS_CONFIG = {"generate-self-signed-certificates": "true", "ca-common-name": "Test CA"}
else:
    TLS_CERTIFICATES_APP_NAME = "self-signed-certificates"
    TLS_CHANNEL = "1/stable"
    TLS_CONFIG = {"ca-common-name": "Test CA"}

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_pitr_backup_gcp(
    ops_test: OpsTest, gcp_cloud_configs: tuple[dict, dict], charm
) -> None:
    """Build, deploy two units of PostgreSQL and do backup in GCP. Then, write new data into DB, switch WAL file and test point-in-time-recovery restore action."""
    config, credentials = gcp_cloud_configs

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
