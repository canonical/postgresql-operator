#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the PostgreSQL charm."""
import logging
import subprocess
from typing import Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    connection_ssl: Optional[bool]
    durability_synchronous_commit: Optional[str]
    instance_default_text_search_config: Optional[str]
    instance_password_encryption: Optional[str]
    logging_log_connections: Optional[bool]
    logging_log_disconnections: Optional[bool]
    logging_log_lock_waits: Optional[bool]
    logging_log_min_duration_statement: Optional[int]
    memory_maintenance_work_mem: Optional[int]
    memory_max_prepared_transactions: Optional[int]
    memory_shared_buffers: Optional[int]
    memory_temp_buffers: Optional[int]
    memory_work_mem: Optional[int]
    optimizer_constraint_exclusion: Optional[str]
    optimizer_default_statistics_target: Optional[int]
    optimizer_from_collapse_limit: Optional[int]
    optimizer_join_collapse_limit: Optional[int]
    profile: str
    profile_limit_memory: Optional[int]
    plugin_citext_enable: bool
    plugin_debversion_enable: bool
    plugin_hstore_enable: bool
    plugin_pg_trgm_enable: bool
    plugin_plpython3u_enable: bool
    plugin_unaccent_enable: bool
    request_date_style: Optional[str]
    request_standard_conforming_strings: Optional[bool]
    request_time_zone: Optional[str]
    response_bytea_output: Optional[str]
    response_lc_monetary: Optional[str]
    response_lc_numeric: Optional[str]
    response_lc_time: Optional[str]
    vacuum_autovacuum_analyze_scale_factor: Optional[float]
    vacuum_autovacuum_analyze_threshold: Optional[int]
    vacuum_autovacuum_freeze_max_age: Optional[int]
    vacuum_autovacuum_vacuum_cost_delay: Optional[float]
    vacuum_autovacuum_vacuum_scale_factor: Optional[float]
    vacuum_vacuum_freeze_table_age: Optional[int]

    @classmethod
    def keys(cls) -> list[str]:
        """Return config as list items."""
        return list(cls.__fields__.keys())

    @classmethod
    def plugin_keys(cls) -> filter:
        """Return plugin config names in a iterable."""
        return filter(lambda x: x.startswith("plugin_"), cls.keys())

    @validator("durability_synchronous_commit")
    @classmethod
    def durability_synchronous_commit_values(cls, value: str) -> Optional[str]:
        """Check durability_synchronous_commit config option is one of `on`, `remote_apply` or `remote_write`."""
        if value not in ["on", "remote_apply", "remote_write"]:
            raise ValueError("Value not one of 'on', 'remote_apply' or 'remote_write'")

        return value

    @validator("instance_password_encryption")
    @classmethod
    def instance_password_encryption_values(cls, value: str) -> Optional[str]:
        """Check instance_password_encryption config option is one of `md5` or `scram-sha-256`."""
        if value not in ["md5", "scram-sha-256"]:
            raise ValueError("Value not one of 'md5' or 'scram-sha-256'")

        return value

    @validator("logging_log_min_duration_statement")
    @classmethod
    def logging_log_min_duration_statement_values(cls, value: int) -> Optional[int]:
        """Check logging_log_min_duration_statement config option is between -1 and 2147483647."""
        if value < -1 or value > 2147483647:
            raise ValueError("Value is not between -1 and 2147483647")

        return value

    @validator("memory_maintenance_work_mem")
    @classmethod
    def memory_maintenance_work_mem_values(cls, value: int) -> Optional[int]:
        """Check memory_maintenance_work_mem config option is between 1024 and 2147483647."""
        if value < 1024 or value > 2147483647:
            raise ValueError("Value is not between 1024 and 2147483647")

        return value

    @validator("memory_max_prepared_transactions")
    @classmethod
    def memory_max_prepared_transactions_values(cls, value: int) -> Optional[int]:
        """Check memory_max_prepared_transactions config option is between 0 and 262143."""
        if value < 0 or value > 262143:
            raise ValueError("Value is not between 0 and 262143")

        return value

    @validator("memory_shared_buffers")
    @classmethod
    def memory_shared_buffers_values(cls, value: int) -> Optional[int]:
        """Check memory_shared_buffers config option is greater or equal than 16."""
        if value < 16:
            raise ValueError("Shared buffers config option should be at least 16")

        return value

    @validator("memory_temp_buffers")
    @classmethod
    def memory_temp_buffers_values(cls, value: int) -> Optional[int]:
        """Check memory_temp_buffers config option is between 100 and 1073741823."""
        if value < 100 or value > 1073741823:
            raise ValueError("Value is not between 100 and 1073741823")

        return value

    @validator("memory_work_mem")
    @classmethod
    def memory_work_mem_values(cls, value: int) -> Optional[int]:
        """Check memory_work_mem config option is between 64 and 2147483647."""
        if value < 64 or value > 2147483647:
            raise ValueError("Value is not between 64 and 2147483647")

        return value

    @validator("optimizer_constraint_exclusion")
    @classmethod
    def optimizer_constraint_exclusion_values(cls, value: str) -> Optional[str]:
        """Check optimizer_constraint_exclusion config option is one of `on`, `off` or `partition`."""
        if value not in ["on", "off", "partition"]:
            raise ValueError("Value not one of 'on', 'off' or 'partition'")

        return value

    @validator("optimizer_default_statistics_target")
    @classmethod
    def optimizer_default_statistics_target_values(cls, value: int) -> Optional[int]:
        """Check optimizer_default_statistics_target config option is between 1 and 10000."""
        if value < 1 or value > 10000:
            raise ValueError("Value is not between 1 and 10000")

        return value

    @validator("optimizer_from_collapse_limit", allow_reuse=True)
    @validator("optimizer_join_collapse_limit", allow_reuse=True)
    @classmethod
    def optimizer_collapse_limit_values(cls, value: int) -> Optional[int]:
        """Check optimizer collapse_limit config option is between 1 and 2147483647."""
        if value < 1 or value > 2147483647:
            raise ValueError("Value is not between 1 and 2147483647")

        return value

    @validator("profile")
    @classmethod
    def profile_values(cls, value: str) -> Optional[str]:
        """Check profile config option is one of `testing` or `production`."""
        if value not in ["testing", "production"]:
            raise ValueError("Value not one of 'testing' or 'production'")

        return value

    @validator("profile_limit_memory")
    @classmethod
    def profile_limit_memory_validator(cls, value: int) -> Optional[int]:
        """Check profile limit memory."""
        if value < 128:
            raise ValueError("PostgreSQL Charm requires at least 128MB")
        if value > 9999999:
            raise ValueError("`profile-limit-memory` limited to 7 digits (9999999MB)")

        return value

    @validator("response_bytea_output")
    @classmethod
    def response_bytea_output_values(cls, value: str) -> Optional[str]:
        """Check response_bytea_output config option is one of `escape` or `hex`."""
        if value not in ["escape", "hex"]:
            raise ValueError("Value not one of 'escape' or 'hex'")

        return value

    @validator("response_lc_monetary", allow_reuse=True)
    @validator("response_lc_numeric", allow_reuse=True)
    @validator("response_lc_time", allow_reuse=True)
    @classmethod
    def response_lc_values(cls, value: str) -> Optional[str]:
        """Check if the requested locale is available in the system."""
        output = subprocess.check_output(["locale", "-a"])
        locales = [locale.decode() for locale in output.splitlines()]
        if value not in locales:
            raise ValueError("Value not one of the locales available in the system")

        return value

    @validator("vacuum_autovacuum_analyze_scale_factor", allow_reuse=True)
    @validator("vacuum_autovacuum_vacuum_scale_factor", allow_reuse=True)
    @classmethod
    def vacuum_autovacuum_vacuum_scale_factor_values(cls, value: float) -> Optional[float]:
        """Check autovacuum scale_factor config option is between 0 and 100."""
        if value < 0 or value > 100:
            raise ValueError("Value is not between 0 and 100")

        return value

    @validator("vacuum_autovacuum_analyze_threshold")
    @classmethod
    def vacuum_autovacuum_analyze_threshold_values(cls, value: int) -> Optional[int]:
        """Check vacuum_autovacuum_analyze_threshold config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("vacuum_autovacuum_freeze_max_age")
    @classmethod
    def vacuum_autovacuum_freeze_max_age_values(cls, value: int) -> Optional[int]:
        """Check vacuum_autovacuum_freeze_max_age config option is between 100000 and 2000000000."""
        if value < 100000 or value > 2000000000:
            raise ValueError("Value is not between 100000 and 2000000000")

        return value

    @validator("vacuum_autovacuum_vacuum_cost_delay")
    @classmethod
    def vacuum_autovacuum_vacuum_cost_delay_values(cls, value: float) -> Optional[float]:
        """Check vacuum_autovacuum_vacuum_cost_delay config option is between -1 and 100."""
        if value < -1 or value > 100:
            raise ValueError("Value is not between -1 and 100")

        return value

    @validator("vacuum_vacuum_freeze_table_age")
    @classmethod
    def vacuum_vacuum_freeze_table_age_values(cls, value: int) -> Optional[int]:
        """Check vacuum_vacuum_freeze_table_age config option is between 0 and 2000000000."""
        if value < 0 or value > 2000000000:
            raise ValueError("Value is not between 0 and 2000000000")

        return value
