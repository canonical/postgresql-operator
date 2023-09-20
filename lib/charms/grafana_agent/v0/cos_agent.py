# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

r"""## Overview.

This library can be used to manage the cos_agent relation interface:

- `COSAgentProvider`: Use in machine charms that need to have a workload's metrics
  or logs scraped, or forward rule files or dashboards to Prometheus, Loki or Grafana through
  the Grafana Agent machine charm.

- `COSAgentConsumer`: Used in the Grafana Agent machine charm to manage the requirer side of
  the `cos_agent` interface.


## COSAgentProvider Library Usage

Grafana Agent machine Charmed Operator interacts with its clients using the cos_agent library.
Charms seeking to send telemetry, must do so using the `COSAgentProvider` object from
this charm library.

Using the `COSAgentProvider` object only requires instantiating it,
typically in the `__init__` method of your charm (the one which sends telemetry).

The constructor of `COSAgentProvider` has only one required and nine optional parameters:

```python
    def __init__(
        self,
        charm: CharmType,
        relation_name: str = DEFAULT_RELATION_NAME,
        metrics_endpoints: Optional[List[_MetricsEndpointDict]] = None,
        metrics_rules_dir: str = "./src/prometheus_alert_rules",
        logs_rules_dir: str = "./src/loki_alert_rules",
        recurse_rules_dirs: bool = False,
        log_slots: Optional[List[str]] = None,
        dashboard_dirs: Optional[List[str]] = None,
        refresh_events: Optional[List] = None,
        scrape_configs: Optional[Union[List[Dict], Callable]] = None,
    ):
```

### Parameters

- `charm`: The instance of the charm that instantiates `COSAgentProvider`, typically `self`.

- `relation_name`: If your charmed operator uses a relation name other than `cos-agent` to use
    the `cos_agent` interface, this is where you have to specify that.

- `metrics_endpoints`: In this parameter you can specify the metrics endpoints that Grafana Agent
    machine Charmed Operator will scrape. The configs of this list will be merged with the configs
    from `scrape_configs`.

- `metrics_rules_dir`: The directory in which the Charmed Operator stores its metrics alert rules
  files.

- `logs_rules_dir`: The directory in which the Charmed Operator stores its logs alert rules files.

- `recurse_rules_dirs`: This parameters set whether Grafana Agent machine Charmed Operator has to
  search alert rules files recursively in the previous two directories or not.

- `log_slots`: Snap slots to connect to for scraping logs in the form ["snap-name:slot", ...].

- `dashboard_dirs`: List of directories where the dashboards are stored in the Charmed Operator.

- `refresh_events`: List of events on which to refresh relation data.

- `scrape_configs`: List of standard scrape_configs dicts or a callable that returns the list in
    case the configs need to be generated dynamically. The contents of this list will be merged
    with the configs from `metrics_endpoints`.


### Example 1 - Minimal instrumentation:

In order to use this object the following should be in the `charm.py` file.

```python
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
...
class TelemetryProviderCharm(CharmBase):
    def __init__(self, *args):
        ...
        self._grafana_agent = COSAgentProvider(self)
```

### Example 2 - Full instrumentation:

In order to use this object the following should be in the `charm.py` file.

```python
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
...
class TelemetryProviderCharm(CharmBase):
    def __init__(self, *args):
        ...
        self._grafana_agent = COSAgentProvider(
            self,
            relation_name="custom-cos-agent",
            metrics_endpoints=[
                # specify "path" and "port" to scrape from localhost
                {"path": "/metrics", "port": 9000},
                {"path": "/metrics", "port": 9001},
                {"path": "/metrics", "port": 9002},
            ],
            metrics_rules_dir="./src/alert_rules/prometheus",
            logs_rules_dir="./src/alert_rules/loki",
            recursive_rules_dir=True,
            log_slots=["my-app:slot"],
            dashboard_dirs=["./src/dashboards_1", "./src/dashboards_2"],
            refresh_events=["update-status", "upgrade-charm"],
            scrape_configs=[
                {
                    "job_name": "custom_job",
                    "metrics_path": "/metrics",
                    "authorization": {"credentials": "bearer-token"},
                    "static_configs": [
                        {
                            "targets": ["localhost:9003"]},
                            "labels": {"key": "value"},
                        },
                    ],
                },
            ]
        )
```

### Example 3 - Dynamic scrape configs generation:

Pass a function to the `scrape_configs` to decouple the generation of the configs
from the instantiation of the COSAgentProvider object.

```python
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
...

class TelemetryProviderCharm(CharmBase):
    def generate_scrape_configs(self):
        return [
            {
                "job_name": "custom",
                "metrics_path": "/metrics",
                "static_configs": [{"targets": ["localhost:9000"]}],
            },
        ]

    def __init__(self, *args):
        ...
        self._grafana_agent = COSAgentProvider(
            self,
            scrape_configs=self.generate_scrape_configs,
        )
```

## COSAgentConsumer Library Usage

This object may be used by any Charmed Operator which gathers telemetry data by
implementing the consumer side of the `cos_agent` interface.
For instance Grafana Agent machine Charmed Operator.

For this purpose the charm needs to instantiate the `COSAgentConsumer` object with one mandatory
and two optional arguments.

### Parameters

- `charm`: A reference to the parent (Grafana Agent machine) charm.

- `relation_name`: The name of the relation that the charm uses to interact
  with its clients that provides telemetry data using the `COSAgentProvider` object.

  If provided, this relation name must match a provided relation in metadata.yaml with the
  `cos_agent` interface.
  The default value of this argument is "cos-agent".

- `refresh_events`: List of events on which to refresh relation data.


### Example 1 - Minimal instrumentation:

In order to use this object the following should be in the `charm.py` file.

```python
from charms.grafana_agent.v0.cos_agent import COSAgentConsumer
...
class GrafanaAgentMachineCharm(GrafanaAgentCharm)
    def __init__(self, *args):
        ...
        self._cos = COSAgentRequirer(self)
```


### Example 2 - Full instrumentation:

In order to use this object the following should be in the `charm.py` file.

```python
from charms.grafana_agent.v0.cos_agent import COSAgentConsumer
...
class GrafanaAgentMachineCharm(GrafanaAgentCharm)
    def __init__(self, *args):
        ...
        self._cos = COSAgentRequirer(
            self,
            relation_name="cos-agent-consumer",
            refresh_events=["update-status", "upgrade-charm"],
        )
```
"""

import base64
import json
import logging
import lzma
from collections import namedtuple
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, List, Optional, Set, Union

import pydantic
from cosl import JujuTopology
from cosl.rules import AlertRules
from ops.charm import RelationChangedEvent
from ops.framework import EventBase, EventSource, Object, ObjectEvents
from ops.model import Relation, Unit
from ops.testing import CharmType

if TYPE_CHECKING:
    try:
        from typing import TypedDict

        class _MetricsEndpointDict(TypedDict):
            path: str
            port: int

    except ModuleNotFoundError:
        _MetricsEndpointDict = Dict  # pyright: ignore

LIBID = "dc15fa84cef84ce58155fb84f6c6213a"
LIBAPI = 0
LIBPATCH = 6

PYDEPS = ["cosl", "pydantic < 2"]

DEFAULT_RELATION_NAME = "cos-agent"
DEFAULT_PEER_RELATION_NAME = "peers"
DEFAULT_SCRAPE_CONFIG = {
    "static_configs": [{"targets": ["localhost:80"]}],
    "metrics_path": "/metrics",
}

logger = logging.getLogger(__name__)
SnapEndpoint = namedtuple("SnapEndpoint", "owner, name")


class GrafanaDashboard(str):
    """Grafana Dashboard encoded json; lzma-compressed."""

    # TODO Replace this with a custom type when pydantic v2 released (end of 2023 Q1?)
    # https://github.com/pydantic/pydantic/issues/4887
    @staticmethod
    def _serialize(raw_json: Union[str, bytes]) -> "GrafanaDashboard":
        if not isinstance(raw_json, bytes):
            raw_json = raw_json.encode("utf-8")
        encoded = base64.b64encode(lzma.compress(raw_json)).decode("utf-8")
        return GrafanaDashboard(encoded)

    def _deserialize(self) -> Dict:
        try:
            raw = lzma.decompress(base64.b64decode(self.encode("utf-8"))).decode()
            return json.loads(raw)
        except json.decoder.JSONDecodeError as e:
            logger.error("Invalid Dashboard format: %s", e)
            return {}

    def __repr__(self):
        """Return string representation of self."""
        return "<GrafanaDashboard>"


class CosAgentProviderUnitData(pydantic.BaseModel):
    """Unit databag model for `cos-agent` relation."""

    # The following entries are the same for all units of the same principal.
    # Note that the same grafana agent subordinate may be related to several apps.
    # this needs to make its way to the gagent leader
    metrics_alert_rules: dict
    log_alert_rules: dict
    dashboards: List[GrafanaDashboard]
    subordinate: Optional[bool]

    # The following entries may vary across units of the same principal app.
    # this data does not need to be forwarded to the gagent leader
    metrics_scrape_jobs: List[Dict]
    log_slots: List[str]

    # when this whole datastructure is dumped into a databag, it will be nested under this key.
    # while not strictly necessary (we could have it 'flattened out' into the databag),
    # this simplifies working with the model.
    KEY: ClassVar[str] = "config"


class CosAgentPeersUnitData(pydantic.BaseModel):
    """Unit databag model for `peers` cos-agent machine charm peer relation."""

    # We need the principal unit name and relation metadata to be able to render identifiers
    # (e.g. topology) on the leader side, after all the data moves into peer data (the grafana
    # agent leader can only see its own principal, because it is a subordinate charm).
    principal_unit_name: str
    principal_relation_id: str
    principal_relation_name: str

    # The only data that is forwarded to the leader is data that needs to go into the app databags
    # of the outgoing o11y relations.
    metrics_alert_rules: Optional[dict]
    log_alert_rules: Optional[dict]
    dashboards: Optional[List[GrafanaDashboard]]

    # when this whole datastructure is dumped into a databag, it will be nested under this key.
    # while not strictly necessary (we could have it 'flattened out' into the databag),
    # this simplifies working with the model.
    KEY: ClassVar[str] = "config"

    @property
    def app_name(self) -> str:
        """Parse out the app name from the unit name.

        TODO: Switch to using `model_post_init` when pydantic v2 is released?
          https://github.com/pydantic/pydantic/issues/1729#issuecomment-1300576214
        """
        return self.principal_unit_name.split("/")[0]


class COSAgentProvider(Object):
    """Integration endpoint wrapper for the provider side of the cos_agent interface."""

    def __init__(
        self,
        charm: CharmType,
        relation_name: str = DEFAULT_RELATION_NAME,
        metrics_endpoints: Optional[List["_MetricsEndpointDict"]] = None,
        metrics_rules_dir: str = "./src/prometheus_alert_rules",
        logs_rules_dir: str = "./src/loki_alert_rules",
        recurse_rules_dirs: bool = False,
        log_slots: Optional[List[str]] = None,
        dashboard_dirs: Optional[List[str]] = None,
        refresh_events: Optional[List] = None,
        *,
        scrape_configs: Optional[Union[List[dict], Callable]] = None,
    ):
        """Create a COSAgentProvider instance.

        Args:
            charm: The `CharmBase` instance that is instantiating this object.
            relation_name: The name of the relation to communicate over.
            metrics_endpoints: List of endpoints in the form [{"path": path, "port": port}, ...].
                This argument is a simplified form of the `scrape_configs`.
                The contents of this list will be merged with the contents of `scrape_configs`.
            metrics_rules_dir: Directory where the metrics rules are stored.
            logs_rules_dir: Directory where the logs rules are stored.
            recurse_rules_dirs: Whether to recurse into rule paths.
            log_slots: Snap slots to connect to for scraping logs
                in the form ["snap-name:slot", ...].
            dashboard_dirs: Directory where the dashboards are stored.
            refresh_events: List of events on which to refresh relation data.
            scrape_configs: List of standard scrape_configs dicts or a callable
                that returns the list in case the configs need to be generated dynamically.
                The contents of this list will be merged with the contents of `metrics_endpoints`.
        """
        super().__init__(charm, relation_name)
        dashboard_dirs = dashboard_dirs or ["./src/grafana_dashboards"]

        self._charm = charm
        self._relation_name = relation_name
        self._metrics_endpoints = metrics_endpoints or []
        self._scrape_configs = scrape_configs or []
        self._metrics_rules = metrics_rules_dir
        self._logs_rules = logs_rules_dir
        self._recursive = recurse_rules_dirs
        self._log_slots = log_slots or []
        self._dashboard_dirs = dashboard_dirs
        self._refresh_events = refresh_events or [self._charm.on.config_changed]

        events = self._charm.on[relation_name]
        self.framework.observe(events.relation_joined, self._on_refresh)
        self.framework.observe(events.relation_changed, self._on_refresh)
        for event in self._refresh_events:
            self.framework.observe(event, self._on_refresh)

    def _on_refresh(self, event):
        """Trigger the class to update relation data."""
        relations = self._charm.model.relations[self._relation_name]

        for relation in relations:
            # Before a principal is related to the grafana-agent subordinate, we'd get
            # ModelError: ERROR cannot read relation settings: unit "zk/2": settings not found
            # Add a guard to make sure it doesn't happen.
            if relation.data and self._charm.unit in relation.data:
                # Subordinate relations can communicate only over unit data.
                try:
                    data = CosAgentProviderUnitData(
                        metrics_alert_rules=self._metrics_alert_rules,
                        log_alert_rules=self._log_alert_rules,
                        dashboards=self._dashboards,
                        metrics_scrape_jobs=self._scrape_jobs,
                        log_slots=self._log_slots,
                        subordinate=self._charm.meta.subordinate,
                    )
                    relation.data[self._charm.unit][data.KEY] = data.json()
                except (
                    pydantic.ValidationError,
                    json.decoder.JSONDecodeError,
                ) as e:
                    logger.error("Invalid relation data provided: %s", e)

    @property
    def _scrape_jobs(self) -> List[Dict]:
        """Return a prometheus_scrape-like data structure for jobs.

        https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config
        """
        if callable(self._scrape_configs):
            scrape_configs = self._scrape_configs()
        else:
            # Create a copy of the user scrape_configs, since we will mutate this object
            scrape_configs = self._scrape_configs.copy()

        # Convert "metrics_endpoints" to standard scrape_configs, and add them in
        for endpoint in self._metrics_endpoints:
            scrape_configs.append(
                {
                    "metrics_path": endpoint["path"],
                    "static_configs": [{"targets": [f"localhost:{endpoint['port']}"]}],
                }
            )

        scrape_configs = scrape_configs or [DEFAULT_SCRAPE_CONFIG]

        # Augment job name to include the app name and a unique id (index)
        for idx, scrape_config in enumerate(scrape_configs):
            scrape_config["job_name"] = "_".join(
                [self._charm.app.name, str(idx), scrape_config.get("job_name", "default")]
            )

        return scrape_configs

    @property
    def _metrics_alert_rules(self) -> Dict:
        """Use (for now) the prometheus_scrape AlertRules to initialize this."""
        alert_rules = AlertRules(
            query_type="promql", topology=JujuTopology.from_charm(self._charm)
        )
        alert_rules.add_path(self._metrics_rules, recursive=self._recursive)
        return alert_rules.as_dict()

    @property
    def _log_alert_rules(self) -> Dict:
        """Use (for now) the loki_push_api AlertRules to initialize this."""
        alert_rules = AlertRules(query_type="logql", topology=JujuTopology.from_charm(self._charm))
        alert_rules.add_path(self._logs_rules, recursive=self._recursive)
        return alert_rules.as_dict()

    @property
    def _dashboards(self) -> List[GrafanaDashboard]:
        dashboards: List[GrafanaDashboard] = []
        for d in self._dashboard_dirs:
            for path in Path(d).glob("*"):
                dashboard = GrafanaDashboard._serialize(path.read_bytes())
                dashboards.append(dashboard)
        return dashboards


class COSAgentDataChanged(EventBase):
    """Event emitted by `COSAgentRequirer` when relation data changes."""


class COSAgentValidationError(EventBase):
    """Event emitted by `COSAgentRequirer` when there is an error in the relation data."""

    def __init__(self, handle, message: str = ""):
        super().__init__(handle)
        self.message = message

    def snapshot(self) -> Dict:
        """Save COSAgentValidationError source information."""
        return {"message": self.message}

    def restore(self, snapshot):
        """Restore COSAgentValidationError source information."""
        self.message = snapshot["message"]


class COSAgentRequirerEvents(ObjectEvents):
    """`COSAgentRequirer` events."""

    data_changed = EventSource(COSAgentDataChanged)
    validation_error = EventSource(COSAgentValidationError)


class MultiplePrincipalsError(Exception):
    """Custom exception for when there are multiple principal applications."""

    pass


class COSAgentRequirer(Object):
    """Integration endpoint wrapper for the Requirer side of the cos_agent interface."""

    on = COSAgentRequirerEvents()  # pyright: ignore

    def __init__(
        self,
        charm: CharmType,
        *,
        relation_name: str = DEFAULT_RELATION_NAME,
        peer_relation_name: str = DEFAULT_PEER_RELATION_NAME,
        refresh_events: Optional[List[str]] = None,
    ):
        """Create a COSAgentRequirer instance.

        Args:
            charm: The `CharmBase` instance that is instantiating this object.
            relation_name: The name of the relation to communicate over.
            peer_relation_name: The name of the peer relation to communicate over.
            refresh_events: List of events on which to refresh relation data.
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._peer_relation_name = peer_relation_name
        self._refresh_events = refresh_events or [self._charm.on.config_changed]

        events = self._charm.on[relation_name]
        self.framework.observe(
            events.relation_joined, self._on_relation_data_changed
        )  # TODO: do we need this?
        self.framework.observe(events.relation_changed, self._on_relation_data_changed)
        for event in self._refresh_events:
            self.framework.observe(event, self.trigger_refresh)  # pyright: ignore

        # Peer relation events
        # A peer relation is needed as it is the only mechanism for exchanging data across
        # subordinate units.
        # self.framework.observe(
        #     self.on[self._peer_relation_name].relation_joined, self._on_peer_relation_joined
        # )
        peer_events = self._charm.on[peer_relation_name]
        self.framework.observe(peer_events.relation_changed, self._on_peer_relation_changed)

    @property
    def peer_relation(self) -> Optional["Relation"]:
        """Helper function for obtaining the peer relation object.

        Returns: peer relation object
        (NOTE: would return None if called too early, e.g. during install).
        """
        return self.model.get_relation(self._peer_relation_name)

    def _on_peer_relation_changed(self, _):
        # Peer data is used for forwarding data from principal units to the grafana agent
        # subordinate leader, for updating the app data of the outgoing o11y relations.
        if self._charm.unit.is_leader():
            self.on.data_changed.emit()  # pyright: ignore

    def _on_relation_data_changed(self, event: RelationChangedEvent):
        # Peer data is the only means of communication between subordinate units.
        if not self.peer_relation:
            event.defer()
            return

        cos_agent_relation = event.relation
        if not event.unit or not cos_agent_relation.data.get(event.unit):
            return
        principal_unit = event.unit

        # Coherence check
        units = cos_agent_relation.units
        if len(units) > 1:
            # should never happen
            raise ValueError(
                f"unexpected error: subordinate relation {cos_agent_relation} "
                f"should have exactly one unit"
            )

        if not (raw := cos_agent_relation.data[principal_unit].get(CosAgentProviderUnitData.KEY)):
            return

        if not (provider_data := self._validated_provider_data(raw)):
            return

        # Copy data from the principal relation to the peer relation, so the leader could
        # follow up.
        # Save the originating unit name, so it could be used for topology later on by the leader.
        data = CosAgentPeersUnitData(  # peer relation databag model
            principal_unit_name=event.unit.name,
            principal_relation_id=str(event.relation.id),
            principal_relation_name=event.relation.name,
            metrics_alert_rules=provider_data.metrics_alert_rules,
            log_alert_rules=provider_data.log_alert_rules,
            dashboards=provider_data.dashboards,
        )
        self.peer_relation.data[self._charm.unit][
            f"{CosAgentPeersUnitData.KEY}-{event.unit.name}"
        ] = data.json()

        # We can't easily tell if the data that was changed is limited to only the data
        # that goes into peer relation (in which case, if this is not a leader unit, we wouldn't
        # need to emit `on.data_changed`), so we're emitting `on.data_changed` either way.
        self.on.data_changed.emit()  # pyright: ignore

    def _validated_provider_data(self, raw) -> Optional[CosAgentProviderUnitData]:
        try:
            return CosAgentProviderUnitData(**json.loads(raw))
        except (pydantic.ValidationError, json.decoder.JSONDecodeError) as e:
            self.on.validation_error.emit(message=str(e))  # pyright: ignore
            return None

    def trigger_refresh(self, _):
        """Trigger a refresh of relation data."""
        # FIXME: Figure out what we should do here
        self.on.data_changed.emit()  # pyright: ignore

    @property
    def _principal_unit(self) -> Optional[Unit]:
        """Return the principal unit for a relation.

        Assumes that the relation is of type subordinate.
        Relies on the fact that, for subordinate relations, the only remote unit visible to
        *this unit* is the principal unit that this unit is attached to.
        """
        if relations := self._principal_relations:
            # Technically it's a list, but for subordinates there can only be one relation
            principal_relation = next(iter(relations))
            if units := principal_relation.units:
                # Technically it's a list, but for subordinates there can only be one
                return next(iter(units))

        return None

    @property
    def _principal_relations(self):
        relations = []
        for relation in self._charm.model.relations[self._relation_name]:
            if not json.loads(relation.data[next(iter(relation.units))]["config"]).get(
                ["subordinate"], False
            ):
                relations.append(relation)
        if len(relations) > 1:
            logger.error(
                "Multiple applications claiming to be principal. Update the cos-agent library in the client application charms."
            )
            raise MultiplePrincipalsError("Multiple principal applications.")
        return relations

    @property
    def _remote_data(self) -> List[CosAgentProviderUnitData]:
        """Return a list of remote data from each of the related units.

        Assumes that the relation is of type subordinate.
        Relies on the fact that, for subordinate relations, the only remote unit visible to
        *this unit* is the principal unit that this unit is attached to.
        """
        all_data = []

        for relation in self._charm.model.relations[self._relation_name]:
            if not relation.units:
                continue
            unit = next(iter(relation.units))
            if not (raw := relation.data[unit].get(CosAgentProviderUnitData.KEY)):
                continue
            if not (provider_data := self._validated_provider_data(raw)):
                continue
            all_data.append(provider_data)

        return all_data

    def _gather_peer_data(self) -> List[CosAgentPeersUnitData]:
        """Collect data from the peers.

        Returns a trimmed-down list of CosAgentPeersUnitData.
        """
        relation = self.peer_relation

        # Ensure that whatever context we're running this in, we take the necessary precautions:
        if not relation or not relation.data or not relation.app:
            return []

        # Iterate over all peer unit data and only collect every principal once.
        peer_data: List[CosAgentPeersUnitData] = []
        app_names: Set[str] = set()

        for unit in chain((self._charm.unit,), relation.units):
            if not relation.data.get(unit):
                continue

            for unit_name in relation.data.get(unit):  # pyright: ignore
                if not unit_name.startswith(CosAgentPeersUnitData.KEY):
                    continue
                raw = relation.data[unit].get(unit_name)
                if raw is None:
                    continue
                data = CosAgentPeersUnitData(**json.loads(raw))
                # Have we already seen this principal app?
                if (app_name := data.app_name) in app_names:
                    continue
                peer_data.append(data)
                app_names.add(app_name)

        return peer_data

    @property
    def metrics_alerts(self) -> Dict[str, Any]:
        """Fetch metrics alerts."""
        alert_rules = {}

        seen_apps: List[str] = []
        for data in self._gather_peer_data():
            if rules := data.metrics_alert_rules:
                app_name = data.app_name
                if app_name in seen_apps:
                    continue  # dedup!
                seen_apps.append(app_name)
                # This is only used for naming the file, so be as specific as we can be
                identifier = JujuTopology(
                    model=self._charm.model.name,
                    model_uuid=self._charm.model.uuid,
                    application=app_name,
                    # For the topology unit, we could use `data.principal_unit_name`, but that unit
                    # name may not be very stable: `_gather_peer_data` de-duplicates by app name so
                    # the exact unit name that turns up first in the iterator may vary from time to
                    # time. So using the grafana-agent unit name instead.
                    unit=self._charm.unit.name,
                ).identifier

                alert_rules[identifier] = rules

        return alert_rules

    @property
    def metrics_jobs(self) -> List[Dict]:
        """Parse the relation data contents and extract the metrics jobs."""
        scrape_jobs = []
        for data in self._remote_data:
            for job in data.metrics_scrape_jobs:
                # In #220, relation schema changed from a simplified dict to the standard
                # `scrape_configs`.
                # This is to ensure backwards compatibility with Providers older than v0.5.
                if "path" in job and "port" in job and "job_name" in job:
                    job = {
                        "job_name": job["job_name"],
                        "metrics_path": job["path"],
                        "static_configs": [{"targets": [f"localhost:{job['port']}"]}],
                    }

                scrape_jobs.append(job)

        return scrape_jobs

    @property
    def snap_log_endpoints(self) -> List[SnapEndpoint]:
        """Fetch logging endpoints exposed by related snaps."""
        plugs = []
        for data in self._remote_data:
            targets = data.log_slots
            if targets:
                for target in targets:
                    if target in plugs:
                        logger.warning(
                            f"plug {target} already listed. "
                            "The same snap is being passed from multiple "
                            "endpoints; this should not happen."
                        )
                    else:
                        plugs.append(target)

        endpoints = []
        for plug in plugs:
            if ":" not in plug:
                logger.error(f"invalid plug definition received: {plug}. Ignoring...")
            else:
                endpoint = SnapEndpoint(*plug.split(":"))
                endpoints.append(endpoint)
        return endpoints

    @property
    def logs_alerts(self) -> Dict[str, Any]:
        """Fetch log alerts."""
        alert_rules = {}
        seen_apps: List[str] = []

        for data in self._gather_peer_data():
            if rules := data.log_alert_rules:
                # This is only used for naming the file, so be as specific as we can be
                app_name = data.app_name
                if app_name in seen_apps:
                    continue  # dedup!
                seen_apps.append(app_name)

                identifier = JujuTopology(
                    model=self._charm.model.name,
                    model_uuid=self._charm.model.uuid,
                    application=app_name,
                    # For the topology unit, we could use `data.principal_unit_name`, but that unit
                    # name may not be very stable: `_gather_peer_data` de-duplicates by app name so
                    # the exact unit name that turns up first in the iterator may vary from time to
                    # time. So using the grafana-agent unit name instead.
                    unit=self._charm.unit.name,
                ).identifier

                alert_rules[identifier] = rules

        return alert_rules

    @property
    def dashboards(self) -> List[Dict[str, str]]:
        """Fetch dashboards as encoded content.

        Dashboards are assumed not to vary across units of the same primary.
        """
        dashboards: List[Dict[str, Any]] = []

        seen_apps: List[str] = []
        for data in self._gather_peer_data():
            app_name = data.app_name
            if app_name in seen_apps:
                continue  # dedup!
            seen_apps.append(app_name)

            for encoded_dashboard in data.dashboards or ():
                content = GrafanaDashboard(encoded_dashboard)._deserialize()

                title = content.get("title", "no_title")

                dashboards.append(
                    {
                        "relation_id": data.principal_relation_id,
                        # We have the remote charm name - use it for the identifier
                        "charm": f"{data.principal_relation_name}-{app_name}",
                        "content": content,
                        "title": title,
                    }
                )

        return dashboards
