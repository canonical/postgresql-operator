#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the PostgreSQL charm."""

import logging
from typing import Literal

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

from locales import SNAP_LOCALES

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    durability_synchronous_commit: str | None
    instance_default_text_search_config: str | None
    instance_max_locks_per_transaction: int | None
    instance_password_encryption: str | None
    logging_log_connections: bool | None
    logging_log_disconnections: bool | None
    logging_log_lock_waits: bool | None
    logging_log_min_duration_statement: int | None
    memory_maintenance_work_mem: int | None
    memory_max_prepared_transactions: int | None
    memory_shared_buffers: int | None
    memory_temp_buffers: int | None
    memory_work_mem: int | None
    optimizer_constraint_exclusion: str | None
    optimizer_default_statistics_target: int | None
    optimizer_from_collapse_limit: int | None
    optimizer_join_collapse_limit: int | None
    profile: str
    profile_limit_memory: int | None
    plugin_audit_enable: bool
    plugin_citext_enable: bool
    plugin_debversion_enable: bool
    plugin_hstore_enable: bool
    plugin_pg_trgm_enable: bool
    plugin_plpython3u_enable: bool
    plugin_unaccent_enable: bool
    plugin_bloom_enable: bool
    plugin_btree_gin_enable: bool
    plugin_btree_gist_enable: bool
    plugin_cube_enable: bool
    plugin_dict_int_enable: bool
    plugin_dict_xsyn_enable: bool
    plugin_earthdistance_enable: bool
    plugin_fuzzystrmatch_enable: bool
    plugin_intarray_enable: bool
    plugin_isn_enable: bool
    plugin_lo_enable: bool
    plugin_ltree_enable: bool
    plugin_old_snapshot_enable: bool
    plugin_pg_freespacemap_enable: bool
    plugin_pgrowlocks_enable: bool
    plugin_pgstattuple_enable: bool
    plugin_pg_visibility_enable: bool
    plugin_seg_enable: bool
    plugin_tablefunc_enable: bool
    plugin_tcn_enable: bool
    plugin_tsm_system_rows_enable: bool
    plugin_tsm_system_time_enable: bool
    plugin_uuid_ossp_enable: bool
    plugin_spi_enable: bool
    plugin_bool_plperl_enable: bool
    plugin_hll_enable: bool
    plugin_hypopg_enable: bool
    plugin_ip4r_enable: bool
    plugin_plperl_enable: bool
    plugin_jsonb_plperl_enable: bool
    plugin_orafce_enable: bool
    plugin_pg_similarity_enable: bool
    plugin_prefix_enable: bool
    plugin_rdkit_enable: bool
    plugin_tds_fdw_enable: bool
    plugin_icu_ext_enable: bool
    plugin_pltcl_enable: bool
    plugin_postgis_enable: bool
    plugin_timescaledb_enable: bool
    plugin_address_standardizer_enable: bool
    plugin_address_standardizer_data_us_enable: bool
    plugin_postgis_tiger_geocoder_enable: bool
    plugin_postgis_topology_enable: bool
    plugin_postgis_raster_enable: bool
    plugin_vector_enable: bool
    request_date_style: str | None
    request_standard_conforming_strings: bool | None
    request_time_zone: str | None
    response_bytea_output: str | None
    response_lc_monetary: Literal[tuple(SNAP_LOCALES)] | None
    response_lc_numeric: Literal[tuple(SNAP_LOCALES)] | None
    response_lc_time: Literal[tuple(SNAP_LOCALES)] | None
    vacuum_autovacuum_analyze_scale_factor: float | None
    vacuum_autovacuum_analyze_threshold: int | None
    vacuum_autovacuum_freeze_max_age: int | None
    vacuum_autovacuum_vacuum_cost_delay: float | None
    vacuum_autovacuum_vacuum_scale_factor: float | None
    vacuum_vacuum_freeze_table_age: int | None
    experimental_max_connections: int | None

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
    def durability_synchronous_commit_values(cls, value: str) -> str | None:
        """Check durability_synchronous_commit config option is one of `on`, `remote_apply` or `remote_write`."""
        if value not in ["on", "remote_apply", "remote_write"]:
            raise ValueError("Value not one of 'on', 'remote_apply' or 'remote_write'")

        return value

    @validator("instance_password_encryption")
    @classmethod
    def instance_password_encryption_values(cls, value: str) -> str | None:
        """Check instance_password_encryption config option is one of `md5` or `scram-sha-256`."""
        if value not in ["md5", "scram-sha-256"]:
            raise ValueError("Value not one of 'md5' or 'scram-sha-256'")

        return value

    @validator("instance_max_locks_per_transaction")
    @classmethod
    def instance_max_locks_per_transaction_values(cls, value: int) -> int | None:
        """Check instance_max_locks_per_transaction config option is between 64 and 2147483647."""
        if value < 64 or value > 2147483647:
            raise ValueError("Value is not between 64 and 2147483647")

        return value

    @validator("logging_log_min_duration_statement")
    @classmethod
    def logging_log_min_duration_statement_values(cls, value: int) -> int | None:
        """Check logging_log_min_duration_statement config option is between -1 and 2147483647."""
        if value < -1 or value > 2147483647:
            raise ValueError("Value is not between -1 and 2147483647")

        return value

    @validator("memory_maintenance_work_mem")
    @classmethod
    def memory_maintenance_work_mem_values(cls, value: int) -> int | None:
        """Check memory_maintenance_work_mem config option is between 1024 and 2147483647."""
        if value < 1024 or value > 2147483647:
            raise ValueError("Value is not between 1024 and 2147483647")

        return value

    @validator("memory_max_prepared_transactions")
    @classmethod
    def memory_max_prepared_transactions_values(cls, value: int) -> int | None:
        """Check memory_max_prepared_transactions config option is between 0 and 262143."""
        if value < 0 or value > 262143:
            raise ValueError("Value is not between 0 and 262143")

        return value

    @validator("memory_shared_buffers")
    @classmethod
    def memory_shared_buffers_values(cls, value: int) -> int | None:
        """Check memory_shared_buffers config option is greater or equal than 16."""
        if value < 16 or value > 1073741823:
            raise ValueError("Shared buffers config option should be at least 16")

        return value

    @validator("memory_temp_buffers")
    @classmethod
    def memory_temp_buffers_values(cls, value: int) -> int | None:
        """Check memory_temp_buffers config option is between 100 and 1073741823."""
        if value < 100 or value > 1073741823:
            raise ValueError("Value is not between 100 and 1073741823")

        return value

    @validator("memory_work_mem")
    @classmethod
    def memory_work_mem_values(cls, value: int) -> int | None:
        """Check memory_work_mem config option is between 64 and 2147483647."""
        if value < 64 or value > 2147483647:
            raise ValueError("Value is not between 64 and 2147483647")

        return value

    @validator("optimizer_constraint_exclusion")
    @classmethod
    def optimizer_constraint_exclusion_values(cls, value: str) -> str | None:
        """Check optimizer_constraint_exclusion config option is one of `on`, `off` or `partition`."""
        if value not in ["on", "off", "partition"]:
            raise ValueError("Value not one of 'on', 'off' or 'partition'")

        return value

    @validator("optimizer_default_statistics_target")
    @classmethod
    def optimizer_default_statistics_target_values(cls, value: int) -> int | None:
        """Check optimizer_default_statistics_target config option is between 1 and 10000."""
        if value < 1 or value > 10000:
            raise ValueError("Value is not between 1 and 10000")

        return value

    @validator("optimizer_from_collapse_limit", "optimizer_join_collapse_limit")
    @classmethod
    def optimizer_collapse_limit_values(cls, value: int) -> int | None:
        """Check optimizer collapse_limit config option is between 1 and 2147483647."""
        if value < 1 or value > 2147483647:
            raise ValueError("Value is not between 1 and 2147483647")

        return value

    @validator("profile")
    @classmethod
    def profile_values(cls, value: str) -> str | None:
        """Check profile config option is one of `testing` or `production`."""
        if value not in ["testing", "production"]:
            raise ValueError("Value not one of 'testing' or 'production'")

        return value

    @validator("profile_limit_memory")
    @classmethod
    def profile_limit_memory_validator(cls, value: int) -> int | None:
        """Check profile limit memory."""
        if value < 128:
            raise ValueError("PostgreSQL Charm requires at least 128MB")
        if value > 9999999:
            raise ValueError("`profile_limit_memory` limited to 7 digits (9999999MB)")

        return value

    @validator("response_bytea_output")
    @classmethod
    def response_bytea_output_values(cls, value: str) -> str | None:
        """Check response_bytea_output config option is one of `escape` or `hex`."""
        if value not in ["escape", "hex"]:
            raise ValueError("Value not one of 'escape' or 'hex'")

        return value

    @validator("vacuum_autovacuum_analyze_scale_factor", "vacuum_autovacuum_vacuum_scale_factor")
    @classmethod
    def vacuum_autovacuum_vacuum_scale_factor_values(cls, value: float) -> float | None:
        """Check autovacuum scale_factor config option is between 0 and 100."""
        if value < 0 or value > 100:
            raise ValueError("Value is not between 0 and 100")

        return value

    @validator("vacuum_autovacuum_analyze_threshold")
    @classmethod
    def vacuum_autovacuum_analyze_threshold_values(cls, value: int) -> int | None:
        """Check vacuum_autovacuum_analyze_threshold config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("vacuum_autovacuum_freeze_max_age")
    @classmethod
    def vacuum_autovacuum_freeze_max_age_values(cls, value: int) -> int | None:
        """Check vacuum_autovacuum_freeze_max_age config option is between 100000 and 2000000000."""
        if value < 100000 or value > 2000000000:
            raise ValueError("Value is not between 100000 and 2000000000")

        return value

    @validator("vacuum_autovacuum_vacuum_cost_delay")
    @classmethod
    def vacuum_autovacuum_vacuum_cost_delay_values(cls, value: float) -> float | None:
        """Check vacuum_autovacuum_vacuum_cost_delay config option is between -1 and 100."""
        if value < -1 or value > 100:
            raise ValueError("Value is not between -1 and 100")

        return value

    @validator("vacuum_vacuum_freeze_table_age")
    @classmethod
    def vacuum_vacuum_freeze_table_age_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_freeze_table_age config option is between 0 and 2000000000."""
        if value < 0 or value > 2000000000:
            raise ValueError("Value is not between 0 and 2000000000")

        return value
