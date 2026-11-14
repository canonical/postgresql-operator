#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from asyncio import exceptions, gather, sleep
from copy import deepcopy

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    APPLICATION_NAME,
    CHARM_BASE,
    DATABASE_APP_NAME,
    get_machine_from_unit,
    stop_machine,
)
from .helpers import (
    app_name,
    are_writes_increasing,
    check_writes,
    get_cluster_roles,
    start_continuous_writes,
)

logger = logging.getLogger(__name__)

charm = None


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm) -> None:
    """Build and deploy two PostgreSQL clusters."""
    # This is a potentially destructive test, so it shouldn't be run against existing clusters
    async with ops_test.fast_forward():
        # Deploy the first cluster with reusable storage
        await gather(
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=3,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
            ops_test.model.deploy(
                APPLICATION_NAME,
                application_name=APPLICATION_NAME,
                base="ubuntu@24.04",
                channel="latest/edge",
            ),
        )

        await ops_test.model.relate(DATABASE_APP_NAME, f"{APPLICATION_NAME}:database")
        await ops_test.model.wait_for_idle(status="active", timeout=1500)


@pytest.mark.abort_on_fail
async def test_promote_to_primary(ops_test: OpsTest) -> None:
    """Switch the primary over to a sync standby and check the Primary label follows it."""
    database_app = ops_test.model.applications[DATABASE_APP_NAME]
    any_unit_name = database_app.units[0].name
    original_roles = await get_cluster_roles(ops_test, any_unit_name)
    old_primary = original_roles["primaries"][0]
    new_primary = original_roles["sync_standbys"][0]

    def primary_labels() -> dict[str, str]:
        return {unit.name: unit.workload_status_message for unit in database_app.units}

    expected_labels = {
        unit.name: "Primary" if unit.name == new_primary else "" for unit in database_app.units
    }

    # The update-status hook recomputes the label on its own. Keep it from firing,
    # so the label has to be moved by the switchover handling itself.
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})
    try:
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=600, idle_period=30
        )
        assert primary_labels()[old_primary] == "Primary"

        logger.info(f"Promoting {new_primary} (current primary: {old_primary})")
        run_action = await ops_test.model.units[new_primary].run_action(
            "promote-to-primary", scope="unit"
        )
        await run_action.wait()
        assert run_action.status == "completed"

        try:
            await ops_test.model.block_until(
                lambda: primary_labels() == expected_labels, timeout=180
            )
        except exceptions.TimeoutError:
            pytest.fail(
                f"Primary label did not move from {old_primary} to {new_primary}: "
                f"{primary_labels()}"
            )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=600, idle_period=30
        )
        # The label must also stay put once the units settle.
        assert primary_labels() == expected_labels
    finally:
        await ops_test.model.set_config({"update-status-hook-interval": "5m"})

    new_roles = await get_cluster_roles(ops_test, any_unit_name)
    assert new_roles["primaries"] == [new_primary]
    assert old_primary in new_roles["sync_standbys"]
    assert len(new_roles["sync_standbys"]) == 2
    assert len(new_roles["replicas"]) == 0


@pytest.mark.parametrize(
    "roles",
    [
        ["primaries"],
        ["sync_standbys"],
        ["primaries", "sync_standbys"],
        ["sync_standbys", "sync_standbys"],
    ],
)
@pytest.mark.abort_on_fail
async def test_removing_unit(ops_test: OpsTest, roles: list[str], continuous_writes) -> None:
    logger.info(f"removing {', '.join(roles)}")
    # Start an application that continuously writes data to the database.
    app = await app_name(ops_test)
    original_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    copied_roles = deepcopy(original_roles)
    await start_continuous_writes(ops_test, app)
    units = [copied_roles[role].pop(0) for role in roles]
    for unit in units:
        logger.info(f"Stopping unit {unit}")
        await stop_machine(ops_test, await get_machine_from_unit(ops_test, unit))
    await sleep(15)
    for unit in units:
        logger.info(f"Deleting unit {unit}")
        await ops_test.model.destroy_unit(unit, force=True, destroy_storage=False, max_wait=1500)

    if len(roles) > 1:
        for left_unit in ops_test.model.applications[DATABASE_APP_NAME].units:
            if left_unit.name not in units:
                break
        try:
            await ops_test.model.block_until(
                lambda: (
                    left_unit.workload_status == "blocked"
                    and left_unit.workload_status_message
                    == "Raft majority loss, run: promote-to-primary"
                ),
                timeout=600,
            )
            await ops_test.model.wait_for_idle(timeout=600, idle_period=45)

            run_action = (
                await ops_test.model
                .applications[DATABASE_APP_NAME]
                .units[0]
                .run_action("promote-to-primary", scope="unit", force=True)
            )
            await run_action.wait()
        except exceptions.TimeoutError:
            # Check if Patroni self healed
            assert (
                left_unit.workload_status == "active"
                and left_unit.workload_status_message == "Primary"
            )
            logger.warning(f"Patroni self-healed without raft reinitialisation for roles {roles}")

    await ops_test.model.wait_for_idle(status="active", timeout=600, idle_period=45)

    logger.info("Scaling back up")
    await ops_test.model.applications[DATABASE_APP_NAME].add_unit(count=len(roles))
    await ops_test.model.wait_for_idle(status="active", timeout=1500)

    await are_writes_increasing(ops_test, units)

    new_roles = await get_cluster_roles(
        ops_test, ops_test.model.applications[DATABASE_APP_NAME].units[0].name
    )
    assert len(new_roles["primaries"]) == 1
    assert len(new_roles["sync_standbys"]) == 2
    assert len(new_roles["replicas"]) == 0
    if "primaries" in roles:
        assert new_roles["primaries"][0] in original_roles["sync_standbys"]
    else:
        assert new_roles["primaries"][0] == original_roles["primaries"][0]

    await check_writes(ops_test)
