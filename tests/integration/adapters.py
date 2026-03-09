import json
import logging
import secrets
import subprocess
from collections.abc import Generator, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from typing import Any, TypedDict

from jubilant import (
    ConfigValue,
    Juju,
    Status,
    Task,
    TaskError,
    all_active,
    all_agents_idle,
    any_error,
)
from jubilant.statustypes import UnitStatus

logger = logging.getLogger(__name__)


TConstraints = Any
TDevices = Any
ShowUnitOutput = dict


class TStorageInfo(TypedDict):
    """JSON type of Storage returned by `juju list-storage`."""

    key: str
    attachments: dict[str, dict]
    kind: str
    life: str
    persistent: bool


@dataclass
class RequiresInfo:
    """Data model for requires info of a relation."""

    application_name: str
    name: str


@dataclass
class RelationInfo:
    """Data model for `juju show-unit`:`relation-info` section."""

    app: str
    endpoint: str
    related_endpoint: str
    raw: dict[str, Any]

    @property
    def is_peer(self) -> bool:
        """Is this a peer relation?"""
        apps = {_unit_name_to_app(unit_name) for unit_name in self.raw["related-units"]}
        return not bool(apps - {self.app})

    @property
    def requires(self) -> RequiresInfo:
        """Return the requires side info of the relation."""
        name = self.raw.get("related-endpoint", "")
        app = ""
        if related_units := self.raw.get("related-units", {}):
            app = _unit_name_to_app(next(iter(related_units)))

        return RequiresInfo(name=name, application_name=app)


def _unit_name_to_app(name: str) -> str:
    """Convert unit name to app name."""
    return name.split("/")[0]


def all_statuses_are(expected: str, status: Status, apps: Iterable[str]) -> bool:
    """Return True if all units and apps have the `expected` status."""
    if not apps:
        apps = status.apps

    for app in apps:
        app_info = status.apps.get(app)
        if app_info is None:
            return False
        if app_info.app_status.current != expected:
            return False
        for unit_info in status.get_units(app).values():
            if unit_info.workload_status.current != expected:
                return False
    return True


def all_active_idle(status: Status, *apps: str):
    """Return True if all units are active|idle."""
    return all_agents_idle(status, *apps) and all_active(status, *apps)


class ActionAdapter:
    """Action model adapter for libjuju."""

    def __init__(self, task: Task, failed: bool = False):
        self.task = task
        self.status = "failed" if failed else "succeeded"
        self.results = task.results

    def wait(self):
        """Mock wait, since jubilant actions are sync."""
        return self


class UnitAdapter:
    """Unit model adapter for libjuju."""

    def __init__(self, name: str, app: str, status: UnitStatus, juju: Juju):
        self.app = app
        self.name = name
        self.status = status
        self._juju = juju

    def is_leader_from_status(self) -> bool:
        """Check to see if this unit is the leader."""
        return self.status.leader

    def run_action(self, action_name: str, **params):
        """Run an action on this unit."""
        failed = False
        try:
            task = self._juju.run(self.name, action=action_name, params=dict(params))
        except TaskError as e:
            task = e.task
            failed = True
        return ActionAdapter(task, failed=failed)

    def show(self) -> ShowUnitOutput:
        """Return the parsed `show-unit` command."""
        raw = self._juju.cli("show-unit", "--format", "json", self.name)
        return json.loads(raw).get(self.name, {})

    def relation_info(self) -> dict[int, RelationInfo]:
        """Return the unit `relation-info` for `juju show-unit` output."""
        ret = {}
        for item in self.show().get("relation-info", []):
            if not (_id := item.get("relation-id")):
                continue

            ret[_id] = RelationInfo(
                app=self.app,
                endpoint=item.get("endpoint", ""),
                related_endpoint=item.get("related-endpoint", ""),
                raw=dict(item),
            )

        return ret

    @property
    def public_address(self) -> str:
        """Unit public address."""
        return self.status.public_address

    @property
    def workload_status(self) -> str:
        """Return workload status."""
        return self.status.workload_status.current

    @property
    def workload_status_message(self) -> str:
        """Return workload status message."""
        return self.status.workload_status.message


class ApplicationAdapter:
    """Application model adapter for libjuju."""

    def __init__(self, name: str, juju: Juju):
        self.name = name
        self._juju = juju

    def add_unit(
        self,
        count: int = 1,
        to: str | Iterable[str] | None = None,
        attach_storage: Iterable[str] = [],
    ):
        """Add one or more units to this application."""
        _attach_storage = attach_storage if attach_storage else None
        self._juju.add_unit(self.name, num_units=count, to=to, attach_storage=_attach_storage)

    add_units = add_unit

    def destroy_unit(self, *unit_names: str):
        """Destroy units by name."""
        self._juju.remove_unit(*unit_names, destroy_storage=True)

    destroy_units = destroy_unit

    def remove_relation(
        self, local_relation: str, remote_relation: str, block_until_done: bool = False
    ):
        """Remove a relation to another application."""
        self._juju.remove_relation(local_relation, remote_relation)

    def set_config(self, config: Mapping[str, ConfigValue]):
        """Set configuration options for this application."""
        self._juju.config(self.name, values=config)

    @property
    def relations(self) -> list[RelationInfo]:
        """Application relations."""
        return ModelAdapter.get_relations(self.units).values()

    @property
    def units(self) -> list[UnitAdapter]:
        """Application units."""
        units = self._juju.status().apps[self.name].units
        return [
            UnitAdapter(name=unit_name, app=self.name, status=unit_status, juju=self._juju)
            for unit_name, unit_status in units.items()
        ]


class ModelAdapter:
    """Adapter for libjuju `Model` objects."""

    def __init__(self, juju: Juju, wait_delay: float = 3.0):
        self._juju = juju
        self._delay = wait_delay

    def add_secret(self, name: str, data_args: Iterable[str], file: str = "", info: str = ""):
        """Adds a secret with a list of key values.

        Equivalent to the cli command:
        juju add-secret [options] <name> [key[#base64|#file]=value...]

        :param name str: The name of the secret to be added.
        :param data_args []str: The key value pairs to be added into the secret.
        :param file str: A path to a yaml file containing secret key values.
        :param info str: The secret description.
        """
        pass

    def block_until(self, *conditions, timeout: float | None = None, wait_period: float = 0.5):
        """Return only after all conditions are true."""
        self._juju.wait(
            lambda status: all(conditions),
            timeout=timeout,
            successes=10,
        )

    def deploy(
        self,
        entity_url: str,
        application_name: str | None = None,
        bind: dict[str, str] = {},  # noqa
        channel: str | None = None,
        config: dict[str, ConfigValue] | None = None,
        constraints: TDevices = None,
        force: bool = False,
        num_units: int = 1,
        overlays: list[str] | None = None,
        base: str | None = None,
        resources: dict[str, str] | None = None,
        series: str | None = None,
        revision: str | int | None = None,
        storage: Mapping[str, str] | None = None,
        to: str | None = None,
        devices: TDevices = None,
        trust: bool = False,
        attach_storage: list[str] | None = None,
    ) -> None:
        """Deploy a new service or bundle.

        :param str entity_url: Charm or bundle to deploy. Charm url or file path
        :param str application_name: Name to give the service
        :param dict bind: <charm endpoint>:<network space> pairs
        :param str channel: Charm store channel from which to retrieve
            the charm or bundle, e.g. 'edge'
        :param dict config: Charm configuration dictionary
        :param constraints: Service constraints
        :type constraints: :class:`juju.Constraints`
        :param bool force: Allow charm to be deployed to a machine running
            an unsupported series
        :param int num_units: Number of units to deploy
        :param [] overlays: Bundles to overlay on the primary bundle, applied in order
        :param str base: The base on which to deploy
        :param dict resources: <resource name>:<file path> pairs
        :param str series: Series on which to deploy DEPRECATED: use --base (with Juju 3.1)
        :param int revision: specifying a revision requires a channel for future upgrades for charms.
            For bundles, revision and channel are mutually exclusive.
        :param dict storage: optional storage constraints, in the form of `{label: constraint}`.
            The label is a string specified by the charm, while the constraint is
            a constraints.StorageConstraintsDict, or a string following
            `the juju storage constraint directive format <https://juju.is/docs/juju/storage-constraint>`_,
            specifying the storage pool, number of volumes, and size of each volume.
        :param to: Placement directive as a string. For example:

            '23' - place on machine 23
            'lxd:7' - place in new lxd container on machine 7
            '24/lxd/3' - place in container 3 on machine 24

            If None, a new machine is provisioned.
        :param devices: charm device constraints
        :param bool trust: Trust signifies that the charm should be deployed
            with access to trusted credentials. Hooks run by the charm can access
            cloud credentials and other trusted access credentials.

        :param str[] attach_storage: Existing storage to attach to the deployed unit
            (not available on k8s models)
        """
        _overlays = list(overlays) if overlays else []
        # For compatibility with libjuju num_units=0 for subordinate charms
        kwargs = {}
        if num_units > 0:
            kwargs = {"num_units": num_units}
        self._juju.deploy(
            entity_url,
            app=application_name,
            attach_storage=attach_storage,
            base=base,
            bind=bind,
            channel=channel,
            config=config,
            constraints=constraints,
            force=force,
            overlays=_overlays,
            resources=resources,
            revision=revision,
            storage=storage,
            to=to,
            trust=trust,
            **kwargs,
        )

    def destroy_unit(
        self,
        unit_id: str,
        destroy_storage: bool = False,
        dry_run: bool = False,
        force: bool = False,
        max_wait: float | None = None,
    ):
        """Destroy units by name."""
        self._juju.remove_unit(unit_id, destroy_storage=destroy_storage, force=force)

    def list_storage(self, filesystem: bool = False, volume: bool = False) -> list[TStorageInfo]:
        """Lists storage details."""
        raw = self._juju.cli("list-storage", "--format", "json")
        json_ = json.loads(raw)
        ret = []
        for storage_key, storage_details in json_.get("storage", {}).items():
            ret.append({"key": storage_key, **storage_details})

        return ret

    def relate(self, relation1: str, relation2: str):
        """The relate function is deprecated in favor of integrate.

        The logic is the same.
        """
        self._juju.integrate(relation1, relation2)

    add_relation = relate
    integrate = relate

    def remove_application(
        self,
        app_name: str,
        block_until_done: bool = False,
        force: bool = False,
        destroy_storage: bool = False,
        no_wait: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Removes the given application from the model.

        :param str app_name: Name of the application
        :param bool force: Completely remove an application and all its dependencies. (=false)
        :param bool destroy_storage: Destroy storage attached to application unit. (=false)
        :param bool no_wait: Rush through application removal without waiting for each individual step to complete (=false)
        :param bool block_until_done: Ensure the app is removed from the
        model when returned
        :param int timeout: Raise asyncio.exceptions.TimeoutError if the application is not removed
        within the timeout period.
        """
        self._juju.remove_application(app_name, destroy_storage=destroy_storage, force=force)
        if not block_until_done:
            return

        self._juju.wait(
            lambda status: app_name not in status.apps,
            delay=self._delay,
            timeout=timeout,
        )

    def set_config(self, config: Mapping[str, ConfigValue]):
        """Set configuration options for this application."""
        self._juju.model_config(values=config)

    # TODO: add support for wait_for_... args
    def wait_for_idle(
        self,
        apps: Iterable[str] | None = None,
        raise_on_error: bool = True,
        raise_on_blocked: bool = False,
        wait_for_active: bool = False,
        timeout: float | None = 10 * 60,
        idle_period: float = 15,
        check_freq: float = 0.5,
        status: str | None = None,
        wait_for_at_least_units: int | None = None,
        wait_for_exact_units: int | None = None,
    ) -> None:
        """Wait for applications in the model to settle into an idle state.

        :param Iterable[str]|None apps: Optional list of specific app names to wait on.
            If given, all apps must be present in the model and idle, while other
            apps in the model can still be busy. If not given, all apps currently
            in the model must be idle.

        :param bool raise_on_error: If True, then any unit or app going into
            "error" status immediately raises either a JujuAppError or a JujuUnitError.
            Note that machine or agent failures will always raise an exception (either
            JujuMachineError or JujuAgentError), regardless of this param. The default
            is True.

        :param bool raise_on_blocked: If True, then any unit or app going into
            "blocked" status immediately raises either a JujuAppError or a JujuUnitError.
            The default is False.

        :param bool wait_for_active: If True, then also wait for all unit workload
            statuses to be "active" as well. The default is False.

        :param float timeout: How long to wait, in seconds, for the bundle settles
            before raising an asyncio.TimeoutError. If None, will wait forever.
            The default is 10 minutes.

        :param float idle_period: How long, in seconds, the agent statuses of all
            units of all apps need to be `idle`. This delay is used to ensure that
            any pending hooks have a chance to start to avoid false positives.
            The default is 15 seconds.
            Exact behaviour is undefined for very small values and 0.

        :param float check_freq: How frequently, in seconds, to check the model.
            The default is every half-second.

        :param str status: The status to wait for. If None, not waiting.
            The default is None (not waiting for any status).

        :param int wait_for_at_least_units: The least number of units to go into the idle
        state. wait_for_idle will return after that many units are available (across all the
        given applications).
            The default is 1 unit.

        :param int wait_for_exact_units: The exact number of units to be expected before
            going into the idle state. (e.g. useful for scaling down).
            When set, takes precedence over the `wait_for_units` parameter.
        """

        def _all_idle_with_status(juju_status: Status, *apps: str):
            return all_agents_idle(juju_status, *apps) and all_statuses_are(
                status, juju_status, *apps
            )

        if status == "active" or wait_for_active:
            wait_func = all_active_idle
        else:
            wait_func = _all_idle_with_status

        error_func = any_error if raise_on_error else None
        delay = check_freq if check_freq else self._delay
        _apps = apps if apps else list(self._juju.status().apps)

        self._juju.wait(
            lambda juju_status: wait_func(juju_status, *_apps),
            error=error_func,
            delay=delay,
            timeout=timeout,
            successes=(idle_period) // delay,
        )

    @property
    def applications(self) -> dict[str, ApplicationAdapter]:
        """Return a mapping of application name: Application objects."""
        apps = self._juju.status().apps
        return {app: ApplicationAdapter(app, self._juju) for app in apps}

    @property
    def relations(self) -> dict[int, RelationInfo]:
        """Return a map of relation-id:Relation for all relations currently in the model."""
        self.get_relations(self.units.values())

    @property
    def units(self) -> dict[str, UnitAdapter]:
        ret = {}
        for app in self.applications.values():
            for unit in app.units:
                ret[unit.name] = unit
        return ret

    @staticmethod
    def get_relations(units: Iterable[UnitAdapter]) -> dict[int, RelationInfo]:
        """Return a map of relation-id: RelationInfo for all relations currently in the model."""
        ret = {}
        for unit in units:
            for rel_id, rel_info in unit.relation_info().items():
                ret[rel_id] = rel_info

        return ret


class LibjujuExtensions:
    """python-libjuju extensions for Jubilant."""

    def __init__(self, juju: Juju):
        self._juju = juju

    @contextmanager
    def fast_forward(self, fast_interval: str = "10s", slow_interval: str | None = None):
        self._juju.model_config({"update-status-hook-interval": fast_interval})
        yield
        interval = slow_interval or "5m"
        self._juju.model_config({"update-status-hook-interval": interval})

    @property
    def model(self) -> ModelAdapter:
        """python-libjuju model adapter."""
        return ModelAdapter(self._juju)


class JujuFixture(Juju):
    def __init__(self, *, model=None, wait_timeout=3 * 60, cli_binary=None):
        super().__init__(model=model, wait_timeout=wait_timeout, cli_binary=cli_binary)

    @cached_property
    def ext(self) -> LibjujuExtensions:
        """python-libjuju extensions."""
        return LibjujuExtensions(self)


@contextmanager
def temp_model_fixture(
    keep: bool = False,
    controller: str | None = None,
    cloud: str | None = None,
    config: Mapping[str, ConfigValue] | None = None,
    credential: str | None = None,
) -> Generator[JujuFixture]:
    """Context manager to create a temporary model for running tests in."""
    juju = JujuFixture()
    model = "jubilant-" + secrets.token_hex(4)  # 4 bytes (8 hex digits) should be plenty
    juju.add_model(model, cloud=cloud, controller=controller, config=config, credential=credential)
    try:
        yield juju
    finally:
        if not keep:
            assert juju.model is not None
            try:
                # We're not using juju.destroy_model() here, as Juju doesn't provide a way
                # to specify the timeout for the entire model destruction operation.
                args = ["destroy-model", juju.model, "--no-prompt", "--destroy-storage", "--force"]
                juju._cli(*args, include_model=False, timeout=10 * 60)
                juju.model = None
            except subprocess.TimeoutExpired as exc:
                logger.error(
                    "timeout destroying model: %s\nStdout:\n%s\nStderr:\n%s",
                    exc,
                    exc.stdout,
                    exc.stderr,
                )
