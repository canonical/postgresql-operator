#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the PostgreSQL charm."""

import logging
from typing import Annotated, Literal

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import Field, NonNegativeInt, PositiveInt, validator

from locales import SNAP_LOCALES

logger = logging.getLogger(__name__)

# Type for worker process parameters that must be >= 2
WorkerProcessInt = Annotated[int, Field(ge=2)]

# Generic PostgreSQL numeric ranges
PgIntMax = Annotated[int, Field(ge=0, le=2147483647)]
PgPositiveIntMax = Annotated[int, Field(ge=1, le=2147483647)]
PgSignedIntMax = Annotated[int, Field(ge=-1, le=2147483647)]
PercentFloat = Annotated[float, Field(ge=0, le=100)]
UnitIntervalFloat = Annotated[float, Field(ge=0, le=1)]
PgHugeFloat = Annotated[float, Field(ge=0, le=1.80e308)]
PgSignedHugeFloat = Annotated[float, Field(ge=-1, le=1.80e308)]

# Reusable bounded ranges
AuthTimeoutInt = Annotated[int, Field(ge=1, le=600)]
MaxLocksPerTransactionInt = Annotated[int, Field(ge=64, le=2147483647)]
MaintenanceWorkMemInt = Annotated[int, Field(ge=1024, le=2147483647)]
MaxPreparedTransactionsInt = Annotated[int, Field(ge=0, le=262143)]
SharedBuffersInt = Annotated[int, Field(ge=16, le=1073741823)]
TempBuffersInt = Annotated[int, Field(ge=100, le=1073741823)]
WorkMemInt = Annotated[int, Field(ge=64, le=2147483647)]
StatisticsTargetInt = Annotated[int, Field(ge=1, le=10000)]
ExtraFloatDigitsInt = Annotated[int, Field(ge=-15, le=3)]
GeqoEffortInt = Annotated[int, Field(ge=1, le=10)]
GeqoThresholdInt = Annotated[int, Field(ge=2, le=2147483647)]
GeqoSeedFloat = Annotated[float, Field(ge=0, le=1)]
GeqoSelectionBiasFloat = Annotated[float, Field(ge=1.5, le=2)]
ParallelScanSizeInt = Annotated[int, Field(ge=0, le=715827882)]
OldSnapshotThresholdInt = Annotated[int, Field(ge=-1, le=86400)]
BgwriterLruMaxpagesInt = Annotated[int, Field(ge=0, le=1073741823)]
BgwriterLruMultiplierFloat = Annotated[float, Field(ge=0, le=10)]
TrackActivityQuerySizeInt = Annotated[int, Field(ge=100, le=1048576)]
GinPendingListLimitInt = Annotated[int, Field(ge=64, le=2147483647)]
AutoVacuumCostDelayFloat = Annotated[float, Field(ge=-1, le=100)]
AutoVacuumCostLimitInt = Annotated[int, Field(ge=-1, le=10000)]
VacuumCostDelayFloat = Annotated[float, Field(ge=0, le=100)]
VacuumCostLimitInt = Annotated[int, Field(ge=1, le=10000)]
VacuumCostInt = Annotated[int, Field(ge=0, le=10000)]
FreezeMaxAgeInt = Annotated[int, Field(ge=100000, le=2000000000)]
FreezeTableAgeInt = Annotated[int, Field(ge=0, le=2000000000)]
FailsafeAgeInt = Annotated[int, Field(ge=0, le=2100000000)]
FreezeMinAgeInt = Annotated[int, Field(ge=0, le=1000000000)]
AutovacuumNapTimeInt = Annotated[int, Field(ge=1, le=2147483)]
DeadlockTimeoutInt = Annotated[int, Field(ge=1, le=2147483647)]


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    synchronous_node_count: Literal["all", "majority"] | PositiveInt
    synchronous_mode_strict: bool = Field(default=True)
    connection_authentication_timeout: AuthTimeoutInt | None
    connection_statement_timeout: PgIntMax | None
    cpu_max_logical_replication_workers: Literal["auto"] | WorkerProcessInt | None
    cpu_max_parallel_maintenance_workers: Literal["auto"] | WorkerProcessInt | None
    cpu_max_parallel_workers: Literal["auto"] | WorkerProcessInt | None
    cpu_max_sync_workers_per_subscription: Literal["auto"] | WorkerProcessInt | None
    cpu_max_worker_processes: Literal["auto"] | WorkerProcessInt | None
    cpu_parallel_leader_participation: bool | None
    cpu_wal_compression: bool | None
    durability_maximum_lag_on_failover: NonNegativeInt | None
    durability_synchronous_commit: str | None
    durability_wal_keep_size: PgIntMax | None
    experimental_max_connections: int | None
    instance_default_text_search_config: str | None
    instance_max_locks_per_transaction: MaxLocksPerTransactionInt | None
    instance_password_encryption: str | None
    instance_synchronize_seqscans: bool | None
    ldap_map: str | None
    ldap_search_filter: str | None
    logging_client_min_messages: str | None
    logging_log_connections: bool | None
    logging_log_disconnections: bool | None
    logging_log_lock_waits: bool | None
    logging_log_min_duration_statement: PgSignedIntMax | None
    logging_track_functions: str | None
    memory_maintenance_work_mem: MaintenanceWorkMemInt | None
    memory_max_prepared_transactions: MaxPreparedTransactionsInt | None
    memory_shared_buffers: SharedBuffersInt | None
    memory_temp_buffers: TempBuffersInt | None
    memory_work_mem: WorkMemInt | None
    optimizer_constraint_exclusion: str | None
    optimizer_cpu_index_tuple_cost: PgHugeFloat | None
    optimizer_cpu_operator_cost: PgHugeFloat | None
    optimizer_cpu_tuple_cost: PgHugeFloat | None
    optimizer_cursor_tuple_fraction: UnitIntervalFloat | None
    optimizer_default_statistics_target: StatisticsTargetInt | None
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
    optimizer_from_collapse_limit: PgPositiveIntMax | None
    optimizer_geqo: bool | None
    optimizer_geqo_effort: GeqoEffortInt | None
    optimizer_geqo_generations: PgIntMax | None
    optimizer_geqo_pool_size: PgIntMax | None
    optimizer_geqo_seed: GeqoSeedFloat | None
    optimizer_geqo_selection_bias: GeqoSelectionBiasFloat | None
    optimizer_geqo_threshold: GeqoThresholdInt | None
    optimizer_jit: bool | None
    optimizer_jit_above_cost: PgSignedHugeFloat | None
    optimizer_jit_inline_above_cost: PgSignedHugeFloat | None
    optimizer_jit_optimize_above_cost: PgSignedHugeFloat | None
    optimizer_join_collapse_limit: PgPositiveIntMax | None
    optimizer_min_parallel_index_scan_size: ParallelScanSizeInt | None
    optimizer_min_parallel_table_scan_size: ParallelScanSizeInt | None
    optimizer_parallel_setup_cost: PgHugeFloat | None
    optimizer_parallel_tuple_cost: PgHugeFloat | None
    optimizer_pg_stat_statements_track: Literal["none", "top", "all"]
    optimizer_pg_stat_statements_track_utility: bool
    optimizer_pg_stat_statements_save: bool
    optimizer_track_io_timing: bool
    optimizer_track_wal_io_timing: bool
    optimizer_track_functions: Literal["none", "pl", "all"]
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
    plugin_pg_stat_statements_enable: bool
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
    request_deadlock_timeout: DeadlockTimeoutInt | None
    request_default_transaction_deferrable: bool | None
    request_default_transaction_isolation: str | None
    request_default_transaction_read_only: bool | None
    request_escape_string_warning: bool | None
    request_lock_timeout: PgIntMax | None
    request_standard_conforming_strings: bool | None
    request_time_zone: str | None
    request_track_activity_query_size: TrackActivityQuerySizeInt | None
    request_transform_null_equals: bool | None
    request_xmlbinary: str | None
    request_xmloption: str | None
    response_bytea_output: str | None
    response_exit_on_error: bool | None
    response_extra_float_digits: ExtraFloatDigitsInt | None
    response_gin_fuzzy_search_limit: PgIntMax | None
    response_lc_monetary: Literal[tuple(SNAP_LOCALES)] | None
    response_lc_numeric: Literal[tuple(SNAP_LOCALES)] | None
    response_lc_time: Literal[tuple(SNAP_LOCALES)] | None
    session_idle_in_transaction_session_timeout: PgIntMax | None
    storage_bgwriter_lru_maxpages: BgwriterLruMaxpagesInt | None
    storage_bgwriter_lru_multiplier: BgwriterLruMultiplierFloat | None
    storage_default_table_access_method: str | None
    storage_gin_pending_list_limit: GinPendingListLimitInt | None
    storage_hot_standby_feedback: bool | None
    storage_old_snapshot_threshold: OldSnapshotThresholdInt | None
    vacuum_autovacuum_analyze_scale_factor: PercentFloat | None
    vacuum_autovacuum_analyze_threshold: PgIntMax | None
    vacuum_autovacuum_freeze_max_age: FreezeMaxAgeInt | None
    vacuum_autovacuum_naptime: AutovacuumNapTimeInt | None
    vacuum_autovacuum_vacuum_cost_delay: AutoVacuumCostDelayFloat | None
    vacuum_autovacuum_vacuum_cost_limit: AutoVacuumCostLimitInt | None
    vacuum_autovacuum_vacuum_insert_scale_factor: PercentFloat | None
    vacuum_autovacuum_vacuum_insert_threshold: PgSignedIntMax | None
    vacuum_autovacuum_vacuum_scale_factor: PercentFloat | None
    vacuum_autovacuum_vacuum_threshold: PgIntMax | None
    vacuum_vacuum_cost_delay: VacuumCostDelayFloat | None
    vacuum_vacuum_cost_limit: VacuumCostLimitInt | None
    vacuum_vacuum_cost_page_dirty: VacuumCostInt | None
    vacuum_vacuum_cost_page_hit: VacuumCostInt | None
    vacuum_vacuum_cost_page_miss: VacuumCostInt | None
    vacuum_vacuum_failsafe_age: FailsafeAgeInt | None
    vacuum_vacuum_freeze_min_age: FreezeMinAgeInt | None
    vacuum_vacuum_freeze_table_age: FreezeTableAgeInt | None
    vacuum_vacuum_multixact_failsafe_age: FailsafeAgeInt | None
    vacuum_vacuum_multixact_freeze_min_age: FreezeMinAgeInt | None
    vacuum_vacuum_multixact_freeze_table_age: FreezeTableAgeInt | None

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

    @validator("optimizer_constraint_exclusion")
    @classmethod
    def optimizer_constraint_exclusion_values(cls, value: str) -> str | None:
        """Check optimizer_constraint_exclusion config option is one of `on`, `off` or `partition`."""
        if value not in ["on", "off", "partition"]:
            raise ValueError("Value not one of 'on', 'off' or 'partition'")

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

    @validator("request_backslash_quote")
    @classmethod
    def request_backslash_quote_values(cls, value: str) -> str | None:
        """Check request_backslash_quote config option is one of `safe_encoding`, `on` or 'off'."""
        if value not in ["safe_encoding", "on", "off"]:
            raise ValueError("Value not one of `safe_encoding` or `on` or 'off'")

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

    @validator("logging_track_functions")
    @classmethod
    def logging_track_functions_values(cls, value: str) -> str | None:
        """Check logging_track_functions config option is one of 'none', 'pl', 'all'."""
        if value not in ["none", "pl", "all"]:
            raise ValueError("Value not one of 'none', 'pl', 'all'.")

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
