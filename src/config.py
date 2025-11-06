#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the PostgreSQL charm."""

import logging
from typing import Literal

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import PositiveInt, validator

from locales import SNAP_LOCALES

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    synchronous_node_count: Literal["all", "majority"] | PositiveInt
    connection_authentication_timeout: int | None
    connection_statement_timeout: int | None
    cpu_parallel_leader_participation: bool | None
    durability_synchronous_commit: str | None
    durability_wal_keep_size: int | None
    experimental_max_connections: int | None
    wal_compression: str | None
    instance_default_text_search_config: str | None
    instance_max_locks_per_transaction: int | None
    instance_password_encryption: str | None
    instance_synchronize_seqscans: bool | None
    ldap_map: str | None
    ldap_search_filter: str | None
    logging_client_min_messages: str | None
    logging_log_connections: bool | None
    logging_log_disconnections: bool | None
    logging_log_lock_waits: bool | None
    logging_log_min_duration_statement: int | None
    logging_track_functions: str | None
    memory_maintenance_work_mem: int | None
    memory_max_prepared_transactions: int | None
    memory_shared_buffers: int | None
    memory_temp_buffers: int | None
    memory_work_mem: int | None
    max_worker_processes: str | None
    max_parallel_workers: str | None
    max_parallel_maintenance_workers: str | None
    max_logical_replication_workers: str | None
    max_sync_workers_per_subscription: str | None
    max_parallel_apply_workers_per_subscription: str | None
    optimizer_constraint_exclusion: str | None
    optimizer_cpu_index_tuple_cost: float | None
    optimizer_cpu_operator_cost: float | None
    optimizer_cpu_tuple_cost: float | None
    optimizer_cursor_tuple_fraction: float | None
    optimizer_default_statistics_target: int | None
    optimizer_enable_async_append: bool | None
    optimizer_enable_bitmapscan: bool | None
    optimizer_enable_gathermerge: bool | None
    optimizer_enable_hashagg: bool | None
    optimizer_enable_hashjoin: bool | None
    optimizer_enable_incremental_sort: bool | None
    optimizer_enable_indexonlyscan: bool | None
    optimizer_enable_indexscan: bool | None
    optimizer_enable_material: bool | None
    optimizer_enable_memoize: bool | None
    optimizer_enable_mergejoin: bool | None
    optimizer_enable_nestloop: bool | None
    optimizer_enable_parallel_append: bool | None
    optimizer_enable_parallel_hash: bool | None
    optimizer_enable_partition_pruning: bool | None
    optimizer_enable_partitionwise_aggregate: bool | None
    optimizer_enable_partitionwise_join: bool | None
    optimizer_enable_seqscan: bool | None
    optimizer_enable_sort: bool | None
    optimizer_enable_tidscan: bool | None
    optimizer_from_collapse_limit: int | None
    optimizer_geqo: bool | None
    optimizer_geqo_effort: int | None
    optimizer_geqo_generations: int | None
    optimizer_geqo_pool_size: int | None
    optimizer_geqo_seed: float | None
    optimizer_geqo_selection_bias: float | None
    optimizer_geqo_threshold: int | None
    optimizer_jit: bool | None
    optimizer_jit_above_cost: float | None
    optimizer_jit_inline_above_cost: float | None
    optimizer_jit_optimize_above_cost: float | None
    optimizer_join_collapse_limit: int | None
    optimizer_min_parallel_index_scan_size: int | None
    optimizer_min_parallel_table_scan_size: int | None
    optimizer_parallel_setup_cost: float | None
    optimizer_parallel_tuple_cost: float | None
    profile: str
    profile_limit_memory: int | None
    plugin_address_standardizer_data_us_enable: bool
    plugin_address_standardizer_enable: bool
    plugin_audit_enable: bool
    plugin_bloom_enable: bool
    plugin_bool_plperl_enable: bool
    plugin_btree_gin_enable: bool
    plugin_btree_gist_enable: bool
    plugin_citext_enable: bool
    plugin_cube_enable: bool
    plugin_debversion_enable: bool
    plugin_dict_int_enable: bool
    plugin_dict_xsyn_enable: bool
    plugin_earthdistance_enable: bool
    plugin_fuzzystrmatch_enable: bool
    plugin_hll_enable: bool
    plugin_hstore_enable: bool
    plugin_hypopg_enable: bool
    plugin_icu_ext_enable: bool
    plugin_intarray_enable: bool
    plugin_ip4r_enable: bool
    plugin_isn_enable: bool
    plugin_jsonb_plperl_enable: bool
    plugin_lo_enable: bool
    plugin_ltree_enable: bool
    plugin_old_snapshot_enable: bool
    plugin_orafce_enable: bool
    plugin_pg_freespacemap_enable: bool
    plugin_pg_similarity_enable: bool
    plugin_pg_trgm_enable: bool
    plugin_pg_visibility_enable: bool
    plugin_pgrowlocks_enable: bool
    plugin_pgstattuple_enable: bool
    plugin_plperl_enable: bool
    plugin_plpython3u_enable: bool
    plugin_pltcl_enable: bool
    plugin_postgis_enable: bool
    plugin_postgis_raster_enable: bool
    plugin_postgis_tiger_geocoder_enable: bool
    plugin_postgis_topology_enable: bool
    plugin_prefix_enable: bool
    plugin_rdkit_enable: bool
    plugin_seg_enable: bool
    plugin_spi_enable: bool
    plugin_tablefunc_enable: bool
    plugin_tcn_enable: bool
    plugin_tds_fdw_enable: bool
    plugin_timescaledb_enable: bool
    plugin_tsm_system_rows_enable: bool
    plugin_tsm_system_time_enable: bool
    plugin_unaccent_enable: bool
    plugin_uuid_ossp_enable: bool
    plugin_vector_enable: bool
    request_array_nulls: bool | None
    request_backslash_quote: str | None
    request_date_style: str | None
    request_deadlock_timeout: int | None
    request_default_transaction_deferrable: bool | None
    request_default_transaction_isolation: str | None
    request_default_transaction_read_only: bool | None
    request_escape_string_warning: bool | None
    request_lock_timeout: int | None
    request_standard_conforming_strings: bool | None
    request_time_zone: str | None
    request_track_activity_query_size: int | None
    request_transform_null_equals: bool | None
    request_xmlbinary: str | None
    request_xmloption: str | None
    response_bytea_output: str | None
    response_exit_on_error: bool | None
    response_extra_float_digits: float | None
    response_gin_fuzzy_search_limit: int | None
    response_lc_monetary: Literal[tuple(SNAP_LOCALES)] | None
    response_lc_numeric: Literal[tuple(SNAP_LOCALES)] | None
    response_lc_time: Literal[tuple(SNAP_LOCALES)] | None
    session_idle_in_transaction_session_timeout: int | None
    storage_bgwriter_lru_maxpages: int | None
    storage_bgwriter_lru_multiplier: float | None
    storage_default_table_access_method: str | None
    storage_gin_pending_list_limit: int | None
    storage_old_snapshot_threshold: int | None
    vacuum_autovacuum_analyze_scale_factor: float | None
    vacuum_autovacuum_analyze_threshold: int | None
    vacuum_autovacuum_freeze_max_age: int | None
    vacuum_autovacuum_naptime: int | None
    vacuum_autovacuum_vacuum_cost_delay: float | None
    vacuum_autovacuum_vacuum_cost_limit: int | None
    vacuum_autovacuum_vacuum_insert_scale_factor: float | None
    vacuum_autovacuum_vacuum_insert_threshold: int | None
    vacuum_autovacuum_vacuum_scale_factor: float | None
    vacuum_autovacuum_vacuum_threshold: int | None
    vacuum_vacuum_cost_delay: float | None
    vacuum_vacuum_cost_limit: int | None
    vacuum_vacuum_cost_page_dirty: int | None
    vacuum_vacuum_cost_page_hit: int | None
    vacuum_vacuum_cost_page_miss: int | None
    vacuum_vacuum_failsafe_age: int | None
    vacuum_vacuum_freeze_min_age: int | None
    vacuum_vacuum_freeze_table_age: int | None
    vacuum_vacuum_multixact_failsafe_age: int | None
    vacuum_vacuum_multixact_freeze_min_age: int | None
    vacuum_vacuum_multixact_freeze_table_age: int | None

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

    @validator("durability_wal_keep_size")
    @classmethod
    def durability_wal_keep_size_values(cls, value: int) -> int | None:
        """Check durability_wal_keep_size config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

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

    @validator("wal_compression")
    @classmethod
    def wal_compression_values(cls, value: str) -> str | None:
        """Check wal_compression config option is one of `on` or `off`."""
        if value not in ["on", "off"]:
            raise ValueError("Value not one of 'on' or 'off'")

        return value

    @validator(
        "max_worker_processes",
        "max_parallel_workers",
        "max_parallel_maintenance_workers",
        "max_logical_replication_workers",
        "max_sync_workers_per_subscription",
        "max_parallel_apply_workers_per_subscription",
    )
    @classmethod
    def worker_values(cls, value: str) -> str | None:
        """Check worker config options are 'auto' or a valid number.

        Note: The actual cap validation (10 * vCores) is performed at runtime
        in the charm layer where CPU information is available.
        """
        if value == "auto":
            return value

        # Validate that if not "auto", it's a valid positive integer
        try:
            num_value = int(value)
            if num_value < 0:
                raise ValueError("Value must be 'auto' or a positive number")
        except ValueError as e:
            raise ValueError("Value must be 'auto' or a valid number") from e

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

    @validator("connection_authentication_timeout")
    @classmethod
    def connection_authentication_timeout_values(cls, value: int) -> int | None:
        """Check connection_authentication_timeout config option is between 1 and 600."""
        if value < 1 or value > 600:
            raise ValueError("Value is not between 1 and 600")

        return value

    @validator("vacuum_autovacuum_naptime")
    @classmethod
    def vacuum_autovacuum_naptime_values(cls, value: int) -> int | None:
        """Check vacuum_autovacuum_naptime config option is between 1 and 2147483."""
        if value < 1 or value > 2147483:
            raise ValueError("Value is not between 1 and 2147483")

        return value

    @validator("vacuum_autovacuum_vacuum_cost_limit")
    @classmethod
    def vacuum_autovacuum_vacuum_cost_limit_values(cls, value: int) -> int | None:
        """Check vacuum_autovacuum_vacuum_cost_limit config option is between -1 and 10000."""
        if value < -1 or value > 10000:
            raise ValueError("Value is not between -1 and 10000")

        return value

    @validator("vacuum_autovacuum_vacuum_insert_scale_factor")
    @classmethod
    def vacuum_autovacuum_vacuum_insert_scale_factor_values(cls, value: float) -> float | None:
        """Check vacuum_autovacuum_vacuum_insert_scale_factor config option is between 0 and 100."""
        if value < 0 or value > 100:
            raise ValueError("Value is not between 0 and 100")

        return value

    @validator("vacuum_autovacuum_vacuum_insert_threshold")
    @classmethod
    def vacuum_autovacuum_vacuum_insert_threshold_values(cls, value: int) -> int | None:
        """Check vacuum_autovacuum_vacuum_insert_threshold config option is between -1 and 2147483647."""
        if value < -1 or value > 2147483647:
            raise ValueError("Value is not between -1 and 2147483647")

        return value

    @validator("vacuum_autovacuum_vacuum_threshold")
    @classmethod
    def vacuum_autovacuum_vacuum_threshold_values(cls, value: int) -> int | None:
        """Check vacuum_autovacuum_vacuum_threshold config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("request_backslash_quote")
    @classmethod
    def request_backslash_quote_values(cls, value: str) -> str | None:
        """Check request_backslash_quote config option is one of `safe_encoding`, `on` or 'off'."""
        if value not in ["safe_encoding", "on", "off"]:
            raise ValueError("Value not one of `safe_encoding` or `on` or 'off'")

        return value

    @validator("storage_bgwriter_lru_maxpages")
    @classmethod
    def storage_bgwriter_lru_maxpages_values(cls, value: int) -> int | None:
        """Check storage_bgwriter_lru_maxpages config option is between 0 and 1073741823."""
        if value < 0 or value > 1073741823:
            raise ValueError("Value is not between 0 and 1073741823")

        return value

    @validator("storage_bgwriter_lru_multiplier")
    @classmethod
    def storage_bgwriter_lru_multiplier_values(cls, value: float) -> float | None:
        """Check storage_bgwriter_lru_multiplier config option is between 0 and 10."""
        if value < 0 or value > 10:
            raise ValueError("Value is not between 0 and 10")

        return value

    @validator("response_bytea_output")
    @classmethod
    def response_bytea_output_values(cls, value: str) -> str | None:
        """Check response_bytea_output config option is one of `escape` or `hex`."""
        if value not in ["escape", "hex"]:
            raise ValueError("Value not one of 'escape' or 'hex'")

        return value

    @validator("logging_client_min_messages")
    @classmethod
    def logging_client_min_messages_values(cls, value: str) -> str | None:
        """Check logging_client_min_messages config option is one of 'debug5', 'debug4', 'debug3', 'debug2', 'debug1', 'log', 'notice', 'warning' or 'error'."""
        if value not in [
            "debug5",
            "debug4",
            "debug3",
            "debug2",
            "debug1",
            "log",
            "notice",
            "warning",
            "error",
        ]:
            raise ValueError(
                "Value not one of 'debug5', 'debug4', 'debug3', 'debug2', 'debug1', 'log', 'notice', 'warning' or 'error'."
            )

        return value

    @validator("optimizer_cpu_index_tuple_cost")
    @classmethod
    def optimizer_cpu_index_tuple_cost_values(cls, value: float) -> float | None:
        """Check optimizer_cpu_index_tuple_cost config option is between 0 and 1.80E+308."""
        if value < 0 or value > 1.80e308:
            raise ValueError("Value is not between 0 and 1.80E+308")

        return value

    @validator("optimizer_cpu_operator_cost")
    @classmethod
    def optimizer_cpu_operator_cost_values(cls, value: float) -> float | None:
        """Check optimizer_cpu_operator_cost config option is between 0 and 1.80E+308."""
        if value < 0 or value > 1.80e308:
            raise ValueError("Value is not between 0 and 1.80E+308")

        return value

    @validator("optimizer_cpu_tuple_cost")
    @classmethod
    def optimizer_cpu_tuple_cost_values(cls, value: float) -> float | None:
        """Check optimizer_cpu_tuple_cost config option is between 0 and 1.80E+308."""
        if value < 0 or value > 1.80e308:
            raise ValueError("Value is not between 0 and 1.80E+308")

        return value

    @validator("optimizer_cursor_tuple_fraction")
    @classmethod
    def optimizer_cursor_tuple_fraction_values(cls, value: float) -> float | None:
        """Check optimizer_cursor_tuple_fraction config option is between 0 and 1."""
        if value < 0 or value > 1:
            raise ValueError("Value is not between 0 and 1")

        return value

    @validator("request_deadlock_timeout")
    @classmethod
    def request_deadlock_timeout_values(cls, value: int) -> int | None:
        """Check request_deadlock_timeout config option is between 1 and 2147483647."""
        if value < 1 or value > 2147483647:
            raise ValueError("Value is not between 1 and 2147483647")

        return value

    # storage_default_table_access_method: str | None
    @validator("request_default_transaction_isolation")
    @classmethod
    def request_default_transaction_isolation_values(cls, value: str) -> str | None:
        """Check request_default_transaction_isolation config option is one of 'serializable', 'repeatable read', 'read committed', 'read uncommitted'."""
        if value not in ["serializable", "repeatable read", "read committed", "read uncommitted"]:
            raise ValueError(
                "Value not one of 'serializable', 'repeatable read', 'read committed', 'read uncommitted'."
            )

        return value

    @validator("response_extra_float_digits")
    @classmethod
    def response_extra_float_digits_values(cls, value: int) -> int | None:
        """Check response_extra_float_digits config option is between -15 and 3."""
        if value < -15 or value > 3:
            raise ValueError("Value is not between -15 and 3")

        return value

    @validator("optimizer_geqo_effort")
    @classmethod
    def optimizer_geqo_effort_values(cls, value: int) -> int | None:
        """Check optimizer_geqo_effort config option is between 1 and 10."""
        if value < 1 or value > 10:
            raise ValueError("Value is not between 1 and 10")

        return value

    @validator("optimizer_geqo_generations")
    @classmethod
    def optimizer_geqo_generations_values(cls, value: int) -> int | None:
        """Check optimizer_geqo_generations config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("optimizer_geqo_pool_size")
    @classmethod
    def optimizer_geqo_pool_size_values(cls, value: int) -> int | None:
        """Check optimizer_geqo_pool_size config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("optimizer_geqo_seed")
    @classmethod
    def optimizer_geqo_seed_values(cls, value: float) -> float | None:
        """Check optimizer_geqo_seed config option is between 0 and 1."""
        if value < 0 or value > 1:
            raise ValueError("Value is not between 0 and 1")

        return value

    @validator("optimizer_geqo_selection_bias")
    @classmethod
    def optimizer_geqo_selection_bias_values(cls, value: float) -> float | None:
        """Check optimizer_geqo_selection_bias config option is between 1.5 and 2."""
        if value < 1.5 or value > 2:
            raise ValueError("Value is not between 1.5 and 2")

        return value

    @validator("optimizer_geqo_threshold")
    @classmethod
    def optimizer_geqo_threshold_values(cls, value: int) -> int | None:
        """Check optimizer_geqo_threshold config option is between 2 and 2147483647."""
        if value < 2 or value > 2147483647:
            raise ValueError("Value is not between 2 and 2147483647")

        return value

    @validator("response_gin_fuzzy_search_limit")
    @classmethod
    def response_gin_fuzzy_search_limit_values(cls, value: int) -> int | None:
        """Check response_gin_fuzzy_search_limit config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("storage_gin_pending_list_limit")
    @classmethod
    def storage_gin_pending_list_limit_values(cls, value: int) -> int | None:
        """Check storage_gin_pending_list_limit config option is between 64 and 2147483647."""
        if value < 64 or value > 2147483647:
            raise ValueError("Value is not between 64 and 2147483647")

        return value

    @validator("session_idle_in_transaction_session_timeout")
    @classmethod
    def session_idle_in_transaction_session_timeout_values(cls, value: int) -> int | None:
        """Check session_idle_in_transaction_session_timeout config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("optimizer_jit_above_cost")
    @classmethod
    def optimizer_jit_above_cost_values(cls, value: float) -> float | None:
        """Check optimizer_jit_above_cost config option is between -1 and 1.80E+308."""
        if value < -1 or value > 1.80e308:
            raise ValueError("Value is not between -1 and 1.80E+308")

        return value

    @validator("optimizer_jit_inline_above_cost")
    @classmethod
    def optimizer_jit_inline_above_cost_values(cls, value: float) -> float | None:
        """Check optimizer_jit_inline_above_cost config option is between -1 and 1.80E+308."""
        if value < -1 or value > 1.80e308:
            raise ValueError("Value is not between -1 and 1.80E+308")

        return value

    @validator("optimizer_jit_optimize_above_cost")
    @classmethod
    def optimizer_jit_optimize_above_cost_values(cls, value: float) -> float | None:
        """Check optimizer_jit_optimize_above_cost config option is between -1 and 1.80E+308."""
        if value < -1 or value > 1.80e308:
            raise ValueError("Value is not between -1 and 1.80E+308")

        return value

    @validator("request_lock_timeout")
    @classmethod
    def request_lock_timeout_values(cls, value: int) -> int | None:
        """Check request_lock_timeout config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("optimizer_min_parallel_index_scan_size")
    @classmethod
    def optimizer_min_parallel_index_scan_size_values(cls, value: int) -> int | None:
        """Check optimizer_min_parallel_index_scan_size config option is between 0 and 715827882."""
        if value < 0 or value > 715827882:
            raise ValueError("Value is not between 0 and 715827882")

        return value

    @validator("optimizer_min_parallel_table_scan_size")
    @classmethod
    def optimizer_min_parallel_table_scan_size_values(cls, value: int) -> int | None:
        """Check optimizer_min_parallel_table_scan_size config option is between 0 and 715827882."""
        if value < 0 or value > 715827882:
            raise ValueError("Value is not between 0 and 715827882")

        return value

    @validator("storage_old_snapshot_threshold")
    @classmethod
    def storage_old_snapshot_threshold_values(cls, value: int) -> int | None:
        """Check storage_old_snapshot_threshold config option is between -1 and 86400."""
        if value < -1 or value > 86400:
            raise ValueError("Value is not between -1 and 86400")

        return value

    @validator("optimizer_parallel_setup_cost")
    @classmethod
    def optimizer_parallel_setup_cost_values(cls, value: float) -> float | None:
        """Check optimizer_parallel_setup_cost config option is between 0 and 1.80E+308."""
        if value < 0 or value > 1.80e308:
            raise ValueError("Value is not between 0 and 1.80E+308")

        return value

    @validator("optimizer_parallel_tuple_cost")
    @classmethod
    def optimizer_parallel_tuple_cost_values(cls, value: float) -> float | None:
        """Check optimizer_parallel_tuple_cost config option is between 0 and 1.80E+308."""
        if value < 0 or value > 1.80e308:
            raise ValueError("Value is not between 0 and 1.80E+308")

        return value

    @validator("connection_statement_timeout")
    @classmethod
    def connection_statement_timeout_values(cls, value: int) -> int | None:
        """Check connection_statement_timeout config option is between 0 and 2147483647."""
        if value < 0 or value > 2147483647:
            raise ValueError("Value is not between 0 and 2147483647")

        return value

    @validator("request_track_activity_query_size")
    @classmethod
    def request_track_activity_query_size_values(cls, value: int) -> int | None:
        """Check request_track_activity_query_size config option is between 100 and 1048576."""
        if value < 100 or value > 1048576:
            raise ValueError("Value is not between 100 and 1048576")

        return value

    @validator("logging_track_functions")
    @classmethod
    def logging_track_functions_values(cls, value: str) -> str | None:
        """Check logging_track_functions config option is one of 'none', 'pl', 'all'."""
        if value not in ["none", "pl", "all"]:
            raise ValueError("Value not one of 'none', 'pl', 'all'.")

        return value

    @validator("vacuum_vacuum_cost_delay")
    @classmethod
    def vacuum_vacuum_cost_delay_values(cls, value: float) -> float | None:
        """Check vacuum_vacuum_cost_delay config option is between 0 and 100."""
        if value < 0 or value > 100:
            raise ValueError("Value is not between 0 and 100")

        return value

    @validator("vacuum_vacuum_cost_limit")
    @classmethod
    def vacuum_vacuum_cost_limit_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_cost_limit config option is between 1 and 10000."""
        if value < 1 or value > 10000:
            raise ValueError("Value is not between 1 and 10000")

        return value

    @validator("vacuum_vacuum_cost_page_dirty")
    @classmethod
    def vacuum_vacuum_cost_page_dirty_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_cost_page_dirty config option is between 0 and 10000."""
        if value < 0 or value > 10000:
            raise ValueError("Value is not between 0 and 10000")

        return value

    @validator("vacuum_vacuum_cost_page_hit")
    @classmethod
    def vacuum_vacuum_cost_page_hit_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_cost_page_hit config option is between 0 and 10000."""
        if value < 0 or value > 10000:
            raise ValueError("Value is not between 0 and 10000")

        return value

    @validator("vacuum_vacuum_cost_page_miss")
    @classmethod
    def vacuum_vacuum_cost_page_miss_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_cost_page_miss config option is between 0 and 10000."""
        if value < 0 or value > 10000:
            raise ValueError("Value is not between 0 and 10000")

        return value

    @validator("vacuum_vacuum_failsafe_age")
    @classmethod
    def vacuum_vacuum_failsafe_age_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_failsafe_age config option is between 0 and 2100000000."""
        if value < 0 or value > 2100000000:
            raise ValueError("Value is not between 0 and 2100000000")

        return value

    @validator("vacuum_vacuum_freeze_min_age")
    @classmethod
    def vacuum_vacuum_freeze_min_age_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_freeze_min_age config option is between 0 and 1000000000."""
        if value < 0 or value > 1000000000:
            raise ValueError("Value is not between 0 and 1000000000")

        return value

    @validator("vacuum_vacuum_multixact_failsafe_age")
    @classmethod
    def vacuum_vacuum_multixact_failsafe_age_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_multixact_failsafe_age config option is between 0 and 2100000000."""
        if value < 0 or value > 2100000000:
            raise ValueError("Value is not between 0 and 2100000000")

        return value

    @validator("vacuum_vacuum_multixact_freeze_min_age")
    @classmethod
    def vacuum_vacuum_multixact_freeze_min_age_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_multixact_freeze_min_age config option is between 0 and 1000000000."""
        if value < 0 or value > 1000000000:
            raise ValueError("Value is not between 0 and 1000000000")

        return value

    @validator("vacuum_vacuum_multixact_freeze_table_age")
    @classmethod
    def vacuum_vacuum_multixact_freeze_table_age_values(cls, value: int) -> int | None:
        """Check vacuum_vacuum_multixact_freeze_table_age config option is between 0 and 2000000000."""
        if value < 0 or value > 2000000000:
            raise ValueError("Value is not between 0 and 2000000000")

        return value

    @validator("request_xmlbinary")
    @classmethod
    def request_xmlbinary_values(cls, value: str) -> str | None:
        """Check request_xmlbinary config option is 'base64' or 'hex'."""
        if value not in ["base64", "hex"]:
            raise ValueError("Value not 'base64' or 'hex'.")

        return value

    @validator("request_xmloption")
    @classmethod
    def request_xmloption_values(cls, value: str) -> str | None:
        """Check request_xmloption config option is 'content' or 'document'."""
        if value not in ["content", "document"]:
            raise ValueError("Value not 'content' or 'document'.")

        return value
