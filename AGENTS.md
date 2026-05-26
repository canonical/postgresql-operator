# PostgreSQL Operator - Agent Guidelines

Charmed PostgreSQL VM Operator — a Juju charm (Python/ops framework) deploying and managing
PostgreSQL 16 on virtual machines via Patroni for high availability.

## Architectural Rules

### Module Responsibilities

- **`charm.py`** — Main operator class. Event handler registration, orchestration, and business
  logic. Delegates domain-specific work to specialized modules.
- **`cluster.py`** — Patroni lifecycle management: start/stop, switchover, health checks, Raft
  consensus, Patroni configuration rendering (`render_patroni_yml_file`).
- **`backups.py`** — pgBackRest integration: backup, restore, PITR, S3 credential management,
  pgBackRest configuration rendering.
- **`config.py`** — Pydantic configuration model (`CharmConfig`) only. Pure schema definition
  with validated fields. Does not render config files.
- **`relations/`** — One handler class per relation interface (5 files):
  `postgresql_provider.py`, `async_replication.py`, `logical_replication.py`, `tls.py`,
  `watcher.py`.
- **`cluster_topology_observer.py`** — Watches for cluster topology changes via a spawned
  background process and emits custom charm events.
- **`ldap.py`** — LDAP integration via the `LdapRequirer` interface.
- **`locales.py`** — Literal type definition of all locales available in the snap.
- **`rotate_logs.py`** — Background process management for log rotation (pgBackRest logs).
- **`constants.py`** — Global shared constants (paths, ports, password keys). Domain-specific
  constants (error messages, local state values) live in their respective modules.
- **`grafana_dashboards/`** — Grafana dashboard JSON definitions for COS integration.
- **`prometheus_alert_rules/`** — Prometheus alerting rule definitions (YAML).
- **`loki_alert_rules/`** — Loki alerting rule definitions.

The following directories are at the **repository root** (not under `src/`):

- **`templates/`** — Jinja2 templates: `patroni.yml.j2`, `pgbackrest.conf.j2`,
  `pgbackrest.logrotate.j2`.

### External Package: `single_kernel_postgresql`

The `single_kernel_postgresql` package is the shared library used across PostgreSQL charms on
all substrates (VM and K8s). It provides:

- **`PostgreSQL` class** — SQL-level operations (user/role management, database creation,
  extension management, parameter building). Never manages PostgreSQL lifecycle.
- **Config literals** — `SYSTEM_USERS`, `REPLICATION_USER`, `REWIND_USER`, `MONITORING_USER`,
  `USER`, `BACKUP_USER`, `PEER`, `Substrates`, `POSTGRESQL_STORAGE_PERMISSIONS`.
- **Exception classes** — `PostgreSQLCreateUserError`, `PostgreSQLBaseError`, etc.
- **`TLSTransfer`** — TLS certificate transfer event handling.
- **Utility functions** — File rendering, password generation, HTTP helpers.

### Key Rules

1. **Never manage PostgreSQL directly** — all lifecycle operations (start, stop, restart,
   reload) go through Patroni via the `Patroni` class in `cluster.py`. The `PostgreSQL` class
   from `single_kernel_postgresql` is for SQL operations only.

2. **Relation handler pattern** — handlers inherit from `ops.Object`, receive a charm reference
   in `__init__`, and observe their own relation events internally. Do not observe relation
   events in `charm.py`, except for the peer relation (`database-peers`) whose
   `relation_changed` and `relation_departed` events are observed directly in `charm.py`.

3. **Leader-only writes** — app-scoped relation data writes require a
   `self.unit.is_leader()` guard. Unit-scoped data can be written by any unit.

4. **Peer data access** — use `self.charm.app_peer_data` and `self.charm.unit_peer_data` dict
   properties for reading/writing peer relation data.

5. **Configuration flow** — `CharmConfig` (Pydantic model in `config.py`) validates charm
   config. Config file rendering happens in `cluster.py` (Patroni YAML) and `backups.py`
   (pgBackRest conf) using Jinja2 templates from `templates/`.

6. **Constants placement** — global constants shared across modules go in `constants.py`.
   Domain-specific constants (error messages, local state values) stay in the module that
   uses them.

7. **TYPE_CHECKING guard** — use `if TYPE_CHECKING:` for imports needed only by type checkers
   (especially the charm class in relation handlers to avoid circular imports).

8. **Snap-based workload** — PostgreSQL runs as a snap (`charmed-postgresql`). All paths are
   under `/var/snap/charmed-postgresql/`. Service management uses the `charmlibs.snap` library.

9. **Event deferral** — check preconditions before proceeding in event handlers: peer relation
   exists (`self._peers is not None`), cluster initialized (`"cluster_initialised" in
   self.app_peer_data`), Patroni started (`self._patroni.member_started`), can connect to
   PostgreSQL. Defer the event if preconditions are not met.

10. **Status setting** — use `self.set_unit_status()` instead of `self.unit.status =` directly.
    This method respects the refresh lifecycle and will not override refresh status.

11. **Rolling restarts** — use `RollingOpsManager` (bound to the `restart` peer relation) for
    coordinated PostgreSQL restarts. Never restart Patroni/PostgreSQL directly without going
    through the rolling ops mechanism.

12. **Retry patterns** — transient operations (database connections, Patroni API calls) use
    `tenacity` for retry logic. Use `Retrying` context manager or `@retry` decorator with
    appropriate stop/wait strategies. Catch `RetryError` when all retries are exhausted.

## Code Quality Rules

### Copyright Header

Every file must start with:

```python
# Copyright YYYY Canonical Ltd.
# See LICENSE file for licensing details.
```

### Style

- **Line length**: 99 characters
- **Python target**: 3.12
- **Imports**: sorted via ruff I001 — stdlib, then third-party, then local. Absolute imports
  preferred.
- **Docstrings**: Google style, required for public functions and classes.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_CASE`
  for constants.
- **McCabe complexity**: max 10.
- **Security rules** (ruff S-series): enabled for `src/`, disabled for `tests/`.
- **Password-like string labels**: annotate with `# noqa: S105` when the string is a label or
  key name, not an actual secret.

### Type Checking

- `ty` type checker via `ty check`.
- Type hints required for all function signatures in `src/`.
- Use `TYPE_CHECKING` guard for type-only imports (avoids circular imports at runtime).

## Testing Rules

### Unit Tests

- **Framework**: pytest + pytest-asyncio (auto mode).
- **Location**: `tests/unit/`.
- **Run all**: `tox run -e unit`
- **Run single test**: `tox run -e unit -- tests/unit/test_charm.py::test_function_name`
- **Coverage**: branch coverage enabled, excludes `logger.debug` lines.
- **Auto-mocked in `conftest.py`**: `charm_refresh.Machines` and `ops.JujuVersion.has_secrets`
  (set to `True`). Do not mock these again.
- **Charm instantiation**: uses `ops.testing.Harness`.
- **Test structure**: primarily flat functions. Some files (e.g., `test_watcher_relation.py`)
  use test classes — both `::test_function` and `::TestClass::test_method` work with pytest.
- **Exit behavior**: `--exitfirst` is the default (stops on first failure).

### Integration Tests

- **Framework**: pytest-operator + jubilant.
- **Location**: `tests/integration/`.
- **Run**: `tox run -e integration -- tests/integration/test_file.py`
- **Requirements**: running Juju controller + cloud credentials (AWS, GCP, or similar).
- **Duration**: minutes to hours — do not run the full suite casually.

### Testing Expectations

- Changing `src/X.py` means running `tests/unit/test_X.py`.
- New public methods need corresponding unit tests.
- Do not re-mock what `conftest.py` already handles.

## Build

- **Build charm**: `charmcraftcache pack`
- **Format code**: `tox run -e format`
- **Lint**: `tox run -e lint`
- **Unit tests**: `tox run -e unit`
- **Single unit test**: `tox run -e unit -- tests/unit/test_charm.py::test_function_name`
- **Integration tests**: `tox run -e integration -- tests/integration/test_file.py`

## Workflow Checklist

Before submitting any change:

1. Run `tox run -e format` — auto-fix formatting issues.
2. Run `tox run -e lint` — fix all errors (codespell, ruff, shellcheck, ty).
3. Run `tox run -e unit` — ensure all unit tests pass.
4. If Prometheus alert rules were modified, validate with `promtool check rules` and run
   `promtool test rules` against test files in `tests/alerts/`.
5. Verify corresponding tests exist for any new or changed behavior.
6. Confirm global constants are in `constants.py`, domain-specific ones in the relevant module.
7. Confirm leader checks are present for any app-scoped relation data writes.
