#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    CHARM_BASE,
    DATABASE_APP_NAME,
    get_leader_unit,
)

logger = logging.getLogger(__name__)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_config_parameters(ops_test: OpsTest) -> None:
    """Build and deploy one unit of PostgreSQL and then test config with wrong parameters."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        charm = await ops_test.build_charm(".")
        await ops_test.model.deploy(
            charm,
            num_units=1,
            base=CHARM_BASE,
            config={"profile": "testing"},
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1500)

    leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
    test_string = "abcXYZ123"

    configs = [
        {
            "durability_synchronous_commit": [test_string, "on"]
        },  # config option is one of `on`, `remote_apply` or `remote_write`
        {
            "instance_max_locks_per_transaction": ["-1", "64"]
        },  # config option is between 64 and 2147483647
        {
            "instance_password_encryption": [test_string, "scram-sha-256"]
        },  # config option is one of `md5` or `scram-sha-256`
        {
            "logging_log_min_duration_statement": ["-2", "-1"]
        },  # config option is between -1 and 2147483647
        {
            "memory_maintenance_work_mem": ["1023", "65536"]
        },  # config option is between 1024 and 2147483647
        {"memory_max_prepared_transactions": ["-1", "0"]},  # config option is between 0 and 262143
        {"memory_shared_buffers": ["15", "1024"]},  # config option is greater or equal than 16
        {"memory_temp_buffers": ["99", "1024"]},  # config option is between 100 and 1073741823
        {"memory_work_mem": ["63", "4096"]},  # config option is between 64 and 2147483647
        {
            "optimizer_constraint_exclusion": [test_string, "partition"]
        },  # config option is one of `on`, `off` or `partition`
        {
            "optimizer_default_statistics_target": ["0", "100"]
        },  # config option is between 1 and 10000
        {"optimizer_from_collapse_limit": ["0", "8"]},  # config option is between 1 and 2147483647
        {"optimizer_join_collapse_limit": ["0", "8"]},  # config option is between 1 and 2147483647
        {"profile": [test_string, "testing"]},  # config option is one of `testing` or `production`
        # {"profile_limit_memory": {"127", "128"}},  # config option is between 128 and 9999999
        {
            "response_bytea_output": [test_string, "hex"]
        },  # config option is one of `escape` or `hex`
        {
            "vacuum_autovacuum_analyze_scale_factor": ["-1", "0.1"]
        },  # config option is between 0 and 100
        {
            "vacuum_autovacuum_vacuum_scale_factor": ["-1", "0.2"]
        },  # config option is between 0 and 100
        {
            "vacuum_autovacuum_analyze_threshold": ["-1", "50"]
        },  # config option is between 0 and 2147483647
        {
            "vacuum_autovacuum_freeze_max_age": ["99999", "200000000"]
        },  # config option is between 100000 and 2000000000
        {
            "vacuum_autovacuum_vacuum_cost_delay": ["-2", "2.0"]
        },  # config option is between -1 and 100
        {
            "vacuum_vacuum_freeze_table_age": ["-1", "150000000"]
        },  # config option is between 0 and 2000000000
        {
            "instance_default_text_search_config": [test_string, "pg_catalog.simple"]
        },  # config option is validated against the db
        {
            "request_date_style": [test_string, "ISO, MDY"]
        },  # config option is validated against the db
        {"request_time_zone": [test_string, "UTC"]},  # config option is validated against the db
    ]

    charm_config = {}
    for config in configs:
        for k, v in config.items():
            logger.info(k)
            charm_config[k] = v[0]
            await ops_test.model.applications[DATABASE_APP_NAME].set_config(charm_config)
            await ops_test.model.block_until(
                lambda: ops_test.model.units[f"{DATABASE_APP_NAME}/0"].workload_status
                == "blocked",
                timeout=100,
            )
            assert "Configuration Error" in leader_unit.workload_status_message
            charm_config[k] = v[1]

    await ops_test.model.applications[DATABASE_APP_NAME].set_config(charm_config)
    await ops_test.model.block_until(
        lambda: ops_test.model.units[f"{DATABASE_APP_NAME}/0"].workload_status == "active",
        timeout=100,
    )
