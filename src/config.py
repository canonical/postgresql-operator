#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the PostgreSQL charm."""
import logging
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
