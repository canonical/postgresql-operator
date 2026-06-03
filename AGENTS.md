# PostgreSQL Operator - Agent Guidelines

Charmed PostgreSQL VM Operator — a Juju charm (Python/ops framework) deploying and managing
PostgreSQL 16 on virtual machines via Patroni for high availability.

## Architectural Rules

### Module Responsibilities

- **`charm.py`** — Main operator class. Event handler registration, orchestration, and business
  logic. Delegates domain-specific work to specialized modules.
- **`cluster.py`** — Patroni lifecycle management: start/stop, switchover, health checks, Raft
  consensus, and Patroni configuration rendering.
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
- **`constants.py`** — All shared constants (paths, ports, password keys, error messages,
  local state values).
- **`grafana_dashboards/`** — Grafana dashboard JSON definitions for COS integration.
- **`prometheus_alert_rules/`** — Prometheus alerting rule definitions (YAML).
- **`loki_alert_rules/`** — Loki alerting rule definitions.
- **`lib/`** — Vendored charm libraries managed by `charmcraft fetch-lib`. Never modify
  these files directly — changes will be overwritten on the next fetch.

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

3. **Leader-only writes** — app-scoped relation data writes require a leader guard
   (`self.unit.is_leader()`). Unit-scoped data can be written by any unit.

4. **Peer data access** — use the charm's peer-data properties for reading/writing peer
   relation data, rather than reaching into the relation object directly.

5. **Configuration flow** — `CharmConfig` (Pydantic model in `config.py`) validates charm
   config. Config file rendering happens in `cluster.py` (Patroni YAML) and `backups.py`
   (pgBackRest conf) using Jinja2 templates from `templates/`.

6. **Constants placement** — all constants go in `constants.py`.

7. **TYPE_CHECKING guard** — use `if TYPE_CHECKING:` for imports needed only by type checkers
   (especially the charm class in relation handlers to avoid circular imports).

8. **Snap-based workload** — PostgreSQL runs as a snap (`charmed-postgresql`). All paths are
   under `/var/snap/charmed-postgresql/`. Service management uses the `charmlibs.snap` library.

9. **Event deferral** — check preconditions before proceeding in event handlers: the peer
   relation exists, the cluster is initialised, Patroni has started, and PostgreSQL is
   reachable. Defer the event if any precondition is not met.

10. **Status setting** — set unit status through the charm's status helper rather than
    assigning `self.unit.status` directly. The helper respects the refresh lifecycle and will
    not override refresh status.

11. **Rolling restarts** — use `RollingOpsManager` (bound to the `restart` peer relation) for
    coordinated PostgreSQL restarts. Never restart Patroni/PostgreSQL directly without going
    through the rolling ops mechanism.

12. **Retry patterns** — transient operations (database connections, Patroni API calls) use
    `tenacity` for retry logic. Use `Retrying` context manager or `@retry` decorator with
    appropriate stop/wait strategies. Catch `RetryError` when all retries are exhausted.

13. **Juju secrets** — sensitive data (passwords, TLS keys) must be stored using Juju secrets
    via the charm's secret helpers. Never store passwords or credentials in plain relation
    data.

14. **Vendored libraries** — the `lib/` directory contains charm libraries managed by
    `charmcraft fetch-lib`. Never modify these files — submit fixes upstream instead.

15. **Backward compatibility** — A new charm revision must keep working with data written by
    older revisions, and during a refresh new- and old-code units run at the same time. Don't
    drop or rename a config option, peer-data key, or stored secret field without preserving
    the old form — keep reading it, alias it, or migrate it. Put upgrade logic in the
    `charm_refresh` framework rather than ad-hoc upgrade hooks, and don't perform an operation
    a still-old peer can't handle while a refresh is in progress.

16. **Idempotency** — Handlers re-run constantly (update-status fires every few minutes;
    deferred events replay), so every handler must be safe to run repeatedly and converge to
    the same result. Write handlers as reconcilers: compute desired state, compare to actual,
    act only on the difference. Avoid unconditional side effects — generate credentials only
    if absent, guard one-time setup behind peer-data flags, use "create if not exists"
    semantics, and prefer set-based writes over blind appends.

## Tooling

Formatting, imports, naming, complexity, copyright headers, security lints, docstrings, and
type checks (`ty`, with type hints on all `src/` and `scripts/` signatures) are enforced by
`tox run -e format` and `tox run -e lint`. Run those rather than hand-applying style rules.
Before submitting, run `tox run -e format`, `tox run -e lint`, and `tox run -e unit`.

When writing unit tests, note that `conftest.py` already auto-mocks `charm_refresh.Machines`
and `ops.JujuVersion.has_secrets` (both `True`) — don't re-mock these.

## Further reading

Before build or test work, see `CONTRIBUTING.md` — dev environment, build, lint, and test
commands, plus testing specifics (`conftest` auto-mocks, running a single test, frameworks,
alert-rule validation) under `#testing` and `#build-charm`.
