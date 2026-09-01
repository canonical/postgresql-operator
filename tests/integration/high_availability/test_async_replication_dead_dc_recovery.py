#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Regression test for DPE-10203.

After a dead-datacenter failover, re-establishing async replication to a fresh
cluster used to deadlock: the offer/primary and consumer/standby sides shared one
fixed Juju secret label, and a cluster that had been a standby kept that label
reserved as a consumer alias, so the later owner-create collided. The force-removed
cross-model relation delivers no ``relation-broken``, which also leaves a stale
``promoted-cluster-counter`` behind.

This test kills the primary datacenter (cluster units and its Raft-witness watcher),
force-promotes the standby, clears the dead relation and offer, runs
``create-replication`` against a fresh cluster, and asserts recovery succeeds with
the pre-death data intact.

Clusters use 2 units so that 2 PG + 1 watcher form a 3-member (odd) Raft; a
4-member raft stalls standby formation.
"""

import logging
import subprocess
import time
from collections.abc import Generator

import jubilant
import pytest
from jubilant import Juju
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_fixed,
)

from .. import architecture
from .high_availability_helpers_new import (
    get_app_leader,
    get_app_units,
    get_db_max_written_value,
    get_db_standby_leader_unit,
    wait_for_apps_status,
)

DB_APP_1 = "db1"  # original primary DC (killed mid-test)
DB_APP_2 = "db2"  # standby cluster, force-promoted to primary
DB_APP_3 = "db3"  # fresh DC the recovered primary re-replicates to

# Each cluster gets its own Raft-witness watcher so the cross-cluster Raft keeps
# quorum when a DC dies (otherwise the promoted cluster can't finish its
# standby->primary promotion). Deployed from Charmhub — it is a separate charm.
WATCHER_CHARM = "postgresql-watcher"
WATCHER_APP_1 = "watcher1"
WATCHER_APP_2 = "watcher2"
WATCHER_APP_3 = "watcher3"

# Each cluster also gets a client application so the regression covers real data:
# writes made through the pre-death primary must survive the force-promotion and
# re-appear on the fresh re-replication target (mirrors the original async
# replication tests, which relate the test app to every cluster).
DB_TEST_APP_NAME = "postgresql-test-app"
DB_TEST_APP_1 = "test-app1"
DB_TEST_APP_2 = "test-app2"
DB_TEST_APP_3 = "test-app3"
DB_NAME_1 = f"{DB_TEST_APP_1.replace('-', '_')}_database"

# The shared cluster-credentials secret is owned LABELLESS (DPE-10203): the owner
# references it by the id persisted in app peer data, the consumer purely by the id
# published in relation data. This is the legacy label the charm must never attach
# again — kept as a literal so this stays a black-box test.
FORBIDDEN_LABEL = "async-replication-secret"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.fixture(scope="module")
def first_model(juju: Juju) -> Generator:
    """Return the first (original primary) model."""
    yield juju.model


def _extra_model(juju: Juju, request: pytest.FixtureRequest, suffix: str) -> Generator:
    previous_model = juju.model
    model_name = f"{previous_model}-{suffix}"
    logging.info(f"Creating model: {model_name}")
    juju.add_model(model_name)
    yield model_name
    if request.config.getoption("--keep-models"):
        juju.switch(previous_model)
        return
    logging.info(f"Destroying model: {model_name}")
    juju.destroy_model(model_name, destroy_storage=True, force=True)
    # destroy_model nulls the fixture's model when it matches the destroyed one;
    # restore the previous model so the temp-model teardown assert holds.
    juju.switch(previous_model)


@pytest.fixture(scope="module")
def second_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Create and return the second (standby -> promoted primary) model."""
    yield from _extra_model(juju, request, "other")


@pytest.fixture(scope="module")
def third_model(juju: Juju, request: pytest.FixtureRequest) -> Generator:
    """Create and return the third (fresh re-replication target) model."""
    yield from _extra_model(juju, request, "third")


def _async_secret_labels(juju: Juju, app: str) -> set[str]:
    """Return the async-replication secret labels owned by *app*'s leader unit."""
    leader = get_app_leader(juju, app)
    labels: set[str] = set()
    for secret_id in juju.cli("exec", "--unit", leader, "--", "secret-ids").split():
        info = juju.cli("exec", "--unit", leader, "--", "secret-info-get", secret_id)
        for line in info.splitlines():
            stripped = line.strip()
            if stripped.startswith("label:"):
                label = stripped.split(":", 1)[1].strip()
                if "async-replication" in label:
                    labels.add(label)
    return labels


def _consumer_alias_exists(juju: Juju, app: str, label: str) -> bool:
    """Whether *app*'s leader holds a consumer-side alias for *label*.

    A consumed-secret alias is not listed by ``secret-ids`` (which returns only
    owned secrets), so ``_async_secret_labels`` can't see it. Probe it directly:
    ``secret-get --label`` returns content when the alias exists and errors with
    ``consumer label "<label>" not found`` when it was never registered.
    """
    leader = get_app_leader(juju, app)
    try:
        juju.cli("exec", "--unit", leader, "--", "secret-get", f"--label={label}")
        return True
    except jubilant.CLIError as error:
        haystack = f"{error} {getattr(error, 'stderr', '')} {getattr(error, 'stdout', '')}".lower()
        if "not found" in haystack:
            return False
        raise


def _wait_resilient(juju: Juju, **kwargs) -> None:
    """Run ``juju.wait`` but retry through transient controller CLIErrors.

    Force-stopping the primary DC's machines momentarily stresses the single,
    LXD-hosted juju controller — ``juju status`` can transiently fail mid-teardown
    with "no controller API addresses; is bootstrap still in progress?". Retry
    across those blips; a real readiness timeout (``WaitError``) still propagates.
    """
    for attempt in Retrying(
        stop=stop_after_delay(8 * MINUTE_SECS),
        wait=wait_fixed(15),
        retry=retry_if_exception_type(jubilant.CLIError),
        reraise=True,
    ):
        with attempt:
            juju.wait(**kwargs)


def test_deploy(first_model: str, second_model: str, third_model: str, charm: str) -> None:
    """Deploy three 2-unit PostgreSQL clusters, each with its own watcher and test app."""
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}

    clusters = (
        (first_model, DB_APP_1, WATCHER_APP_1, DB_TEST_APP_1),
        (second_model, DB_APP_2, WATCHER_APP_2, DB_TEST_APP_2),
        (third_model, DB_APP_3, WATCHER_APP_3, DB_TEST_APP_3),
    )

    for model_name, app, watcher, test_app in clusters:
        model = Juju(model=model_name)
        model.deploy(
            charm=charm,
            app=app,
            base="ubuntu@24.04",
            config=configuration,
            constraints=constraints,
            num_units=2,
        )
        model.deploy(
            charm=WATCHER_CHARM,
            app=watcher,
            base="ubuntu@24.04",
            channel="16/edge",
            config=configuration,
            constraints=constraints,
            num_units=1,
        )
        model.deploy(
            charm=DB_TEST_APP_NAME,
            app=test_app,
            base="ubuntu@24.04",
            channel="latest/edge",
            constraints=constraints,
            num_units=1,
        )
        model.integrate(f"{app}:watcher-offer", f"{watcher}:watcher")
        model.integrate(f"{test_app}:database", f"{app}:database")

    for model_name, app, watcher, test_app in clusters:
        Juju(model=model_name).wait(
            ready=wait_for_apps_status(jubilant.all_active, app, watcher, test_app),
            timeout=25 * MINUTE_SECS,
        )


def _start_continuous_writes(model: Juju, test_app: str) -> None:
    """Start continuous writes through the test application (retry through transient errors)."""
    for attempt in Retrying(stop=stop_after_attempt(10), reraise=True):
        with attempt:
            model.run(
                unit=get_app_leader(model, test_app), action="start-continuous-writes"
            ).raise_on_failure()


def test_relate_and_replicate(first_model: str, second_model: str) -> None:
    """Make db2 a standby cluster of db1 via async replication."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)

    model_1.offer(f"{first_model}.{DB_APP_1}", endpoint="replication-offer")
    model_2.consume(f"{first_model}.{DB_APP_1}")
    model_2.integrate(DB_APP_1, f"{DB_APP_2}:replication")

    # Wait for the relation to settle before create-replication: the action fails
    # unless every unit has published its address in the relation data.
    model_1.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_1), timeout=10 * MINUTE_SECS
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_2), timeout=10 * MINUTE_SECS
    )

    model_1.run(
        unit=get_app_leader(model_1, DB_APP_1), action="create-replication", wait=5 * MINUTE_SECS
    ).raise_on_failure()

    model_1.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_1), timeout=20 * MINUTE_SECS
    )
    model_2.wait(
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2), timeout=20 * MINUTE_SECS
    )

    # db1 owns the shared secret with NO label (the fix), and db2 is now the
    # read-only standby cluster.
    assert _async_secret_labels(model_1, DB_APP_1) == set()
    assert get_db_standby_leader_unit(model_2, DB_APP_2)

    # Start client writes on the primary: the data they produce must survive the
    # dead-DC teardown and re-appear on the fresh re-replication target.
    _start_continuous_writes(model_1, DB_TEST_APP_1)

    # Consumer side of the fix: the standby reached standby state by reading the
    # offer secret purely by id, so it registered NO consumer alias under any label.
    assert not _consumer_alias_exists(model_2, DB_APP_2, FORBIDDEN_LABEL), (
        "db2 registered a stale-prone consumer-side label alias"
    )


def test_dead_dc_failover_and_recreate_replication(
    first_model: str, second_model: str, third_model: str
) -> None:
    """The DPE-10203 regression: dead-DC teardown must not deadlock create-replication."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)
    model_3 = Juju(model=third_model)

    # 1. Stop the client writes, capture the last written value, and wait until async
    #    replication has caught up — the data written so far must survive everything
    #    that follows. Then kill the primary datacenter: force-stop every machine in
    #    it — db1's units and the watcher alike ("all Rome units" in the ticket) —
    #    and leave them down.
    model_1.run(
        unit=get_app_leader(model_1, DB_TEST_APP_1),
        action="stop-continuous-writes",
        wait=2 * MINUTE_SECS,
    ).raise_on_failure()
    max_written = get_db_max_written_value(
        model_1, DB_APP_1, get_app_leader(model_1, DB_APP_1), DB_NAME_1
    )
    for attempt in Retrying(
        stop=stop_after_delay(5 * MINUTE_SECS), wait=wait_fixed(10), reraise=True
    ):
        with attempt:
            assert all(
                get_db_max_written_value(model_2, DB_APP_2, unit, DB_NAME_1) == max_written
                for unit in get_app_units(model_2, DB_APP_2)
            ), "async replication has not caught up with the pre-death writes"
    status = model_1.status()
    machines = sorted({
        status.machines[unit.machine].instance_id
        for unit in (
            *status.get_units(DB_APP_1).values(),
            *status.get_units(WATCHER_APP_1).values(),
        )
    })
    logging.info(f"Killing the primary DC by force-stopping machines: {machines}")
    for machine in machines:
        subprocess.run(["lxc", "stop", "--force", machine], check=True)
    time.sleep(30)  # let db2 observe the primary loss before forcing promotion

    # 2. Force-promote the standby (a graceful promote refuses with the primary gone).
    logging.info("Force-promoting the standby cluster to primary")
    model_2.run(
        unit=get_app_leader(model_2, DB_APP_2),
        action="promote-to-primary",
        params={"scope": "cluster", "force": True},
        wait=5 * MINUTE_SECS,
    ).raise_on_failure()
    _wait_resilient(
        model_2,
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=20 * MINUTE_SECS,
    )
    # The pre-death data must have survived the force-promotion.
    assert all(
        get_db_max_written_value(model_2, DB_APP_2, unit, DB_NAME_1) == max_written
        for unit in get_app_units(model_2, DB_APP_2)
    ), "pre-death data did not survive the force-promotion"

    # 3. Issue 1 from the ticket: attack the dead relation with remove-relation
    #    --force, for which Juju delivers no events. While that limitation stands the
    #    offer survives it, so the ticket's workaround — remove-saas --force — runs
    #    next; if Juju ever honors the removal, the offer is already gone and the
    #    workaround's "not found" is tolerated (the _consumer_alias_exists idiom).
    #    Either way no relation-broken reaches the charm, which is what leaves the
    #    stale consumer label + promotion counter.
    logging.info("Attempting remove-relation --force on the dead relation (ticket Issue 1)")
    model_2.cli(
        "remove-relation", f"{DB_APP_1}:replication-offer", f"{DB_APP_2}:replication", "--force"
    )
    logging.info("Clearing the dead offer with remove-saas --force")
    try:
        model_2.cli("remove-saas", DB_APP_1, "--force")
    except jubilant.CLIError as error:
        haystack = f"{error} {getattr(error, 'stderr', '')} {getattr(error, 'stdout', '')}".lower()
        if "not found" not in haystack:
            raise
        logging.info("Offer already gone; remove-relation --force had cleared it")
    _wait_resilient(
        model_2,
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=20 * MINUTE_SECS,
    )

    # 4. Re-establish async replication from the promoted db2 to the fresh cluster db3.
    model_2.offer(f"{second_model}.{DB_APP_2}", endpoint="replication-offer")
    model_3.consume(f"{second_model}.{DB_APP_2}")
    model_3.integrate(DB_APP_2, f"{DB_APP_3}:replication")
    _wait_resilient(
        model_2,
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_2),
        timeout=10 * MINUTE_SECS,
    )
    _wait_resilient(
        model_3,
        ready=wait_for_apps_status(jubilant.any_active, DB_APP_3),
        timeout=10 * MINUTE_SECS,
    )

    # 5. THE TICKET POINT: create-replication must SUCCEED, not deadlock on a label
    #    collision. The action itself clears an orphaned promoted-cluster-counter
    #    before its guard runs (no update-status round-trip needed); on the pre-fix
    #    charm it fails with the label collision every time and this raises.
    model_2.run(
        unit=get_app_leader(model_2, DB_APP_2),
        action="create-replication",
        wait=5 * MINUTE_SECS,
    ).raise_on_failure()

    _wait_resilient(
        model_2,
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_2),
        timeout=20 * MINUTE_SECS,
    )
    _wait_resilient(
        model_3,
        ready=wait_for_apps_status(jubilant.all_active, DB_APP_3),
        timeout=20 * MINUTE_SECS,
    )

    # The promoted db2 owns the shared secret with NO label — proof the
    # owner-create did not collide with any stale consumer alias (DPE-10203).
    assert _async_secret_labels(model_2, DB_APP_2) == set(), (
        "db2 owns the async-replication secret under a label"
    )
    # db3 is the standby of the recovered primary.
    assert not _consumer_alias_exists(model_3, DB_APP_3, FORBIDDEN_LABEL), (
        "db3 registered a stale-prone consumer-side label alias"
    )
    # The fresh re-replication target carries the pre-death data — the whole point
    # of the recovered async replication leg.
    assert all(
        get_db_max_written_value(model_3, DB_APP_3, unit, DB_NAME_1) == max_written
        for unit in get_app_units(model_3, DB_APP_3)
    ), "pre-death data did not re-replicate to the fresh cluster"
