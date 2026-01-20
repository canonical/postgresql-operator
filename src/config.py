#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the PostgreSQL charm."""

import logging
from typing import Annotated, Literal

from charms.data_platform_libs.v1.data_models import BaseConfigModel
from pydantic import Field, NonNegativeInt

from locales import SNAP_LOCALES

logger = logging.getLogger(__name__)

# Type for worker process parameters that must be >= 2
WorkerProcessInt = Annotated[int, Field(ge=2)]


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    synchronous_node_count: Literal["all", "majority"] | NonNegativeInt = Field(default="all")
    connection_authentication_timeout: int | None = Field(ge=1, le=600, default=None)
    connection_statement_timeout: int | None = Field(ge=0, le=2147483647, default=None)
    cpu_max_logical_replication_workers: Literal["auto"] | WorkerProcessInt | None = Field(
        default="auto"
    )
    cpu_max_parallel_apply_workers_per_subscription: Literal["auto"] | WorkerProcessInt | None = (
        Field(default="auto")
    )
    cpu_max_parallel_maintenance_workers: Literal["auto"] | WorkerProcessInt | None = Field(
        default="auto"
    )
    cpu_max_parallel_workers: Literal["auto"] | WorkerProcessInt | None = Field(default="auto")
    cpu_max_sync_workers_per_subscription: Literal["auto"] | WorkerProcessInt | None = Field(
        default="auto"
    )
    cpu_max_worker_processes: Literal["auto"] | WorkerProcessInt | None = Field(default="auto")
    cpu_parallel_leader_participation: bool | None = Field(default=None)
    cpu_wal_compression: bool | None = Field(default=None)
    durability_synchronous_commit: Literal["on", "remote_apply", "remote_write"] | None = Field(
        default=None
    )
    durability_wal_keep_size: int | None = Field(ge=0, le=2147483647, default=None)
    experimental_max_connections: int | None = Field(default=None)
    instance_default_text_search_config: str | None = Field(default=None)
    instance_max_locks_per_transaction: int | None = Field(ge=64, le=2147483647, default=None)
    instance_password_encryption: Literal["md5", "scram-sha-256"] | None = Field(default=None)
    instance_synchronize_seqscans: bool | None = Field(default=None)
    ldap_map: str | None = Field(default=None)
    ldap_search_filter: str | None = Field(default=None)
    logging_client_min_messages: (
        Literal[
            "debug5",
            "debug4",
            "debug3",
            "debug2",
            "debug1",
            "log",
            "notice",
            "warning",
            "error",
        ]
        | None
    ) = Field(default=None)
    logging_log_connections: bool | None = Field(default=None)
    logging_log_disconnections: bool | None = Field(default=None)
    logging_log_lock_waits: bool | None = Field(default=None)
    logging_log_min_duration_statement: int | None = Field(ge=-1, le=2147483647, default=None)
    logging_track_functions: Literal["none", "pl", "all"] | None = Field(default=None)
    # logical_replication_subscription_request: str | None
    memory_maintenance_work_mem: int | None = Field(ge=1024, le=2147483647, default=None)
    memory_max_prepared_transactions: int | None = Field(ge=0, le=262143, default=None)
    memory_shared_buffers: int | None = Field(ge=16, le=1073741823, default=None)
    memory_temp_buffers: int | None = Field(ge=100, le=1073741823, default=None)
    memory_work_mem: int | None = Field(ge=64, le=2147483647, default=None)
    optimizer_constraint_exclusion: Literal["on", "off", "partition"] | None = Field(default=None)
    optimizer_cpu_index_tuple_cost: float | None = Field(ge=0, le=1.80e308, default=None)
    optimizer_cpu_operator_cost: float | None = Field(ge=0, le=1.80e308, default=None)
    optimizer_cpu_tuple_cost: float | None = Field(ge=0, le=1.80e308, default=None)
    optimizer_cursor_tuple_fraction: float | None = Field(ge=0, le=1, default=None)
    optimizer_default_statistics_target: int | None = Field(ge=1, le=10000, default=None)
    optimizer_enable_async_append: bool | None = Field(default=None)
    optimizer_enable_bitmapscan: bool | None = Field(default=None)
    optimizer_enable_gathermerge: bool | None = Field(default=None)
    optimizer_enable_hashagg: bool | None = Field(default=None)
    optimizer_enable_hashjoin: bool | None = Field(default=None)
    optimizer_enable_incremental_sort: bool | None = Field(default=None)
    optimizer_enable_indexonlyscan: bool | None = Field(default=None)
    optimizer_enable_indexscan: bool | None = Field(default=None)
    optimizer_enable_material: bool | None = Field(default=None)
    optimizer_enable_memoize: bool | None = Field(default=None)
    optimizer_enable_mergejoin: bool | None = Field(default=None)
    optimizer_enable_nestloop: bool | None = Field(default=None)
    optimizer_enable_parallel_append: bool | None = Field(default=None)
    optimizer_enable_parallel_hash: bool | None = Field(default=None)
    optimizer_enable_partition_pruning: bool | None = Field(default=None)
    optimizer_enable_partitionwise_aggregate: bool | None = Field(default=None)
    optimizer_enable_partitionwise_join: bool | None = Field(default=None)
    optimizer_enable_seqscan: bool | None = Field(default=None)
    optimizer_enable_sort: bool | None = Field(default=None)
    optimizer_enable_tidscan: bool | None = Field(default=None)
    optimizer_from_collapse_limit: int | None = Field(ge=1, le=2147483647, default=None)
    optimizer_geqo: bool | None = Field(default=None)
    optimizer_geqo_effort: int | None = Field(ge=1, le=10, default=None)
    optimizer_geqo_generations: int | None = Field(ge=0, le=2147483647, default=None)
    optimizer_geqo_pool_size: int | None = Field(ge=0, le=2147483647, default=None)
    optimizer_geqo_seed: float | None = Field(ge=0, le=1, default=None)
    optimizer_geqo_selection_bias: float | None = Field(ge=1.5, le=2, default=None)
    optimizer_geqo_threshold: int | None = Field(ge=2, le=2147483647, default=None)
    optimizer_jit: bool | None = Field(default=None)
    optimizer_jit_above_cost: float | None = Field(ge=-1, le=1.80e308, default=None)
    optimizer_jit_inline_above_cost: float | None = Field(ge=-1, le=1.80e308, default=None)
    optimizer_jit_optimize_above_cost: float | None = Field(ge=-1, le=1.80e308, default=None)
    optimizer_join_collapse_limit: int | None = Field(ge=1, le=2147483647, default=None)
    optimizer_min_parallel_index_scan_size: int | None = Field(ge=0, le=715827882, default=None)
    optimizer_min_parallel_table_scan_size: int | None = Field(ge=0, le=715827882, default=None)
    optimizer_parallel_setup_cost: float | None = Field(ge=0, le=1.80e308, default=None)
    optimizer_parallel_tuple_cost: float | None = Field(ge=0, le=1.80e308, default=None)
    profile: Literal["testing", "production"]
    profile_limit_memory: int | None = Field(ge=128, le=9999999, default=None)
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
    request_array_nulls: bool | None = Field(default=None)
    request_backslash_quote: Literal["safe_encoding", "on", "off"] | None = Field(default=None)
    request_date_style: str | None = Field(default=None)
    request_deadlock_timeout: int | None = Field(ge=1, le=2147483647, default=None)
    request_default_transaction_deferrable: bool | None = Field(default=None)
    request_default_transaction_isolation: (
        Literal["serializable", "repeatable read", "read committed", "read uncommitted"] | None
    ) = Field(default=None)
    request_default_transaction_read_only: bool | None = Field(default=None)
    request_escape_string_warning: bool | None = Field(default=None)
    request_lock_timeout: int | None = Field(ge=0, le=2147483647, default=None)
    request_standard_conforming_strings: bool | None = Field(default=None)
    request_time_zone: str | None = Field(default=None)
    request_track_activity_query_size: int | None = Field(ge=100, le=1048576, default=None)
    request_transform_null_equals: bool | None = Field(default=None)
    request_xmlbinary: Literal["base64", "hex"] | None = Field(default=None)
    request_xmloption: Literal["content", "document"] | None = Field(default=None)
    response_bytea_output: Literal["escape", "hex"] | None = Field(default=None)
    response_exit_on_error: bool | None = Field(default=None)
    response_extra_float_digits: float | None = Field(ge=-15, le=3, default=None)
    response_gin_fuzzy_search_limit: int | None = Field(ge=0, le=2147483647, default=None)
    response_lc_monetary: SNAP_LOCALES | None = Field(default=None)
    response_lc_numeric: SNAP_LOCALES | None = Field(default=None)
    response_lc_time: SNAP_LOCALES | None = Field(default=None)
    session_idle_in_transaction_session_timeout: int | None = Field(
        ge=0, le=2147483647, default=None
    )
    storage_bgwriter_lru_maxpages: int | None = Field(ge=0, le=1073741823, default=None)
    storage_bgwriter_lru_multiplier: float | None = Field(ge=0, le=10, default=None)
    storage_default_table_access_method: (
        Literal["serializable", "repeatable read", "read committed", "read uncommitted", "heap"]
        | None
    ) = Field(default=None)
    storage_gin_pending_list_limit: int | None = Field(ge=64, le=2147483647, default=None)
    storage_old_snapshot_threshold: int | None = Field(ge=-1, le=86400, default=None)
    system_users: str | None = Field(default=None)
    vacuum_autovacuum_analyze_scale_factor: float | None = Field(ge=0, le=100, default=None)
    vacuum_autovacuum_analyze_threshold: int | None = Field(ge=0, le=2147483647, default=None)
    vacuum_autovacuum_freeze_max_age: int | None = Field(ge=100000, le=2000000000, default=None)
    vacuum_autovacuum_naptime: int | None = Field(ge=1, le=2147483, default=None)
    vacuum_autovacuum_vacuum_cost_delay: float | None = Field(ge=-1, le=100, default=None)
    vacuum_autovacuum_vacuum_cost_limit: int | None = Field(ge=-1, le=10000, default=None)
    vacuum_autovacuum_vacuum_insert_scale_factor: float | None = Field(ge=0, le=100, default=None)
    vacuum_autovacuum_vacuum_insert_threshold: int | None = Field(
        ge=-1, le=2147483647, default=None
    )
    vacuum_autovacuum_vacuum_scale_factor: float | None = Field(ge=0, le=100, default=None)
    vacuum_autovacuum_vacuum_threshold: int | None = Field(ge=0, le=2147483647, default=None)
    vacuum_vacuum_cost_delay: float | None = Field(ge=-1, le=100, default=None)
    vacuum_vacuum_cost_limit: int | None = Field(ge=-1, le=10000, default=None)
    vacuum_vacuum_cost_page_dirty: int | None = Field(ge=0, le=10000, default=None)
    vacuum_vacuum_cost_page_hit: int | None = Field(ge=0, le=10000, default=None)
    vacuum_vacuum_cost_page_miss: int | None = Field(ge=0, le=10000, default=None)
    vacuum_vacuum_failsafe_age: int | None = Field(ge=0, le=2100000000, default=None)
    vacuum_vacuum_freeze_min_age: int | None = Field(ge=0, le=1000000000, default=None)
    vacuum_vacuum_freeze_table_age: int | None = Field(ge=0, le=2000000000, default=None)
    vacuum_vacuum_multixact_failsafe_age: int | None = Field(ge=0, le=2100000000, default=None)
    vacuum_vacuum_multixact_freeze_min_age: int | None = Field(ge=0, le=1000000000, default=None)
    vacuum_vacuum_multixact_freeze_table_age: int | None = Field(ge=0, le=2000000000, default=None)

    @classmethod
    def keys(cls) -> list[str]:
        """Return config as list items."""
        return list(cls.model_fields.keys())

    @classmethod
    def plugin_keys(cls) -> filter:
        """Return plugin config names in a iterable."""
        return filter(lambda x: x.startswith("plugin_"), cls.keys())
