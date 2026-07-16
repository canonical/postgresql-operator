#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Regression test for DPE-10203.

After a dead-datacenter failover, re-establishing async replication to a fresh
cluster used to deadlock. The teardown ordering that triggers it:

  1. the primary cluster's units all go away (a dead DC),
  2. the standby is promoted to primary with ``force``,
  3. the now-dead consumed offer is cleared with ``remove-saas --force`` (which,
     unlike a graceful ``remove-relation``, never delivers ``relation-broken``),
  4. ``create-replication`` is run to replicate to a fresh cluster.

Step 4 failed with "committing requested changes failed" / "secret with label
async-replication-secret already exists": the offer/primary and consumer/standby
sides shared one fixed Juju secret label, and a cluster that had been a standby
kept that label reserved as a consumer alias, so the later owner-create collided.
A stale ``promoted-cluster-counter`` left by the force-removal compounded it.

The fix owns the shared secret under a distinct owner label
(``async-replication-secret-offer``), has the consumer read it purely by
secret-id — registering no consumer-side alias that could go stale — and
reconciles the orphaned counter from update-status. This test drives the exact
ordering above and asserts recovery succeeds — it fails on the pre-fix charm and
passes on the fixed one.

NOTE: this mirrors the ticket scenario — 2xPG + 1 watcher per datacenter. The
watcher is a stereo-mode Raft witness built for 2-node clusters: 2 PG + 1 watcher
= 3 members (odd, healthy quorum). Deploying 3 PG + 1 watcher instead forms a
4-member (even) raft that stalls standby formation, so clusters use 2 units, not 3.
The watcher keeps cross-cluster Raft quorum through the datacenter death so the
promoted cluster can complete its standby->primary promotion and recovery proceeds.
"""

import logging
import subprocess
import time
from collections.abc import Generator

import jubilant
import pytest
from jubilant import Juju
from tenacity import Retrying, retry_if_exception_type, stop_after_delay, wait_fixed

from .. import architecture
from .high_availability_helpers_new import (
    get_app_leader,
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

# src/relations/async_replication.py::OFFER_SECRET_LABEL — the distinct owner-side
# label the DPE-10203 fix introduced so an owner-create can never collide with a
# stale consumer alias. Kept as a literal so this stays a black-box test.
OFFER_SECRET_LABEL = "async-replication-secret-offer"
# The legacy shared label. The consumer no longer attaches it (it reads the offer
# secret by id), so a standby must register NO alias under this name — that stale
# alias was the DPE-10203 deadlock's other half. Literal, for the same reason.
SECRET_LABEL = "async-replication-secret"

MINUTE_SECS = 60

logging.getLogger("jubilant.wait").setLevel(logging.WARNING)


@pytest.fixture(scope="module")
def first_model(juju: Juju) -> Generator:
    """Return the first (original primary) model."""
    yield juju.model


def _extra_model(juju: Juju, request: pytest.FixtureRequest, suffix: str) -> Generator:
    model_name = f"{juju.model}-{suffix}"
    logging.info(f"Creating model: {model_name}")
    juju.add_model(model_name)
    yield model_name
    if request.config.getoption("--keep-models"):
        return
    logging.info(f"Destroying model: {model_name}")
    juju.destroy_model(model_name, destroy_storage=True, force=True)


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
    """Deploy three 2-unit PostgreSQL clusters, each with its own watcher, one per model."""
    configuration = {"profile": "testing"}
    constraints = {"arch": architecture.architecture}

    clusters = (
        (first_model, DB_APP_1, WATCHER_APP_1),
        (second_model, DB_APP_2, WATCHER_APP_2),
        (third_model, DB_APP_3, WATCHER_APP_3),
    )

    for model_name, app, watcher in clusters:
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
        model.integrate(f"{app}:watcher-offer", f"{watcher}:watcher")

    for model_name, app, watcher in clusters:
        Juju(model=model_name).wait(
            ready=wait_for_apps_status(jubilant.all_active, app, watcher),
            timeout=25 * MINUTE_SECS,
        )


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

    # db1 owns the shared secret under the distinct offer label (the fix), and db2
    # is now the read-only standby cluster.
    assert OFFER_SECRET_LABEL in _async_secret_labels(model_1, DB_APP_1)
    assert get_db_standby_leader_unit(model_2, DB_APP_2)

    # Consumer side of the fix: the standby reached standby state by reading the
    # offer secret purely by id, so it registered NO consumer alias under the
    # legacy shared label. Pre-fix, db2 would hold that alias — the half that goes
    # stale on a dead-DC teardown and blocks a later owner-create (DPE-10203).
    assert not _consumer_alias_exists(model_2, DB_APP_2, SECRET_LABEL), (
        f"db2 registered a stale-prone {SECRET_LABEL!r} consumer alias"
    )


def test_dead_dc_failover_and_recreate_replication(
    first_model: str, second_model: str, third_model: str
) -> None:
    """The DPE-10203 regression: dead-DC teardown must not deadlock create-replication."""
    model_1 = Juju(model=first_model)
    model_2 = Juju(model=second_model)
    model_3 = Juju(model=third_model)

    # 1. Kill the primary datacenter: force-stop every db1 machine and leave it down.
    status = model_1.status()
    machines = [
        status.machines[unit.machine].instance_id for unit in status.get_units(DB_APP_1).values()
    ]
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

    # 3. Clear the dead consumed offer with --force. This is the ordering that skips
    #    relation-broken and leaves the stale consumer label + promotion counter.
    logging.info("Clearing the dead offer with remove-saas --force")
    model_2.cli("remove-saas", DB_APP_1, "--force")
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
    #    collision. Retry to absorb the update-status latency of clear_stale_promotion
    #    (the orphaned promoted-cluster-counter is reconciled from update-status); on
    #    the pre-fix charm it fails with the label collision every time and this raises.
    db2_leader = get_app_leader(model_2, DB_APP_2)
    for attempt in Retrying(
        stop=stop_after_delay(10 * MINUTE_SECS), wait=wait_fixed(30), reraise=True
    ):
        with attempt:
            model_2.run(
                unit=db2_leader, action="create-replication", wait=5 * MINUTE_SECS
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

    # The promoted db2 owns the shared secret under the distinct OFFER label — proof
    # the owner-create did not collide with the stale consumer alias (DPE-10203).
    assert OFFER_SECRET_LABEL in _async_secret_labels(model_2, DB_APP_2), (
        "db2 does not own the async-replication offer secret under OFFER_SECRET_LABEL"
    )
    # db3 is the standby of the recovered primary.
    assert get_db_standby_leader_unit(model_3, DB_APP_3)
    # The fresh standby likewise reads the offer secret by id and registers no
    # consumer alias under the legacy shared label.
    assert not _consumer_alias_exists(model_3, DB_APP_3, SECRET_LABEL), (
        f"db3 registered a stale-prone {SECRET_LABEL!r} consumer alias"
    )
