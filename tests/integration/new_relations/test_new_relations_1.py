# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import asyncio
import logging
from pathlib import Path

import psycopg2
import pytest
import yaml
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from constants import DATABASE_DEFAULT_NAME

from ..helpers import (
    CHARM_BASE,
    assert_sync_standbys,
    get_leader_unit,
    get_machine_from_unit,
    get_primary,
    scale_application,
    start_machine,
    stop_machine,
)
from .helpers import (
    build_connection_string,
    get_application_relation_data,
)

logger = logging.getLogger(__name__)

APPLICATION_APP_NAME = "postgresql-test-app"
DATABASE_APP_NAME = "database"
ANOTHER_DATABASE_APP_NAME = "another-database"
DATA_INTEGRATOR_APP_NAME = "data-integrator"
APP_NAMES = [APPLICATION_APP_NAME, DATABASE_APP_NAME, ANOTHER_DATABASE_APP_NAME]
DATABASE_APP_METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
FIRST_DATABASE_RELATION_NAME = "database"
SECOND_DATABASE_RELATION_NAME = "second-database"
MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "multiple-database-clusters"
ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME = "aliased-multiple-database-clusters"
NO_DATABASE_RELATION_NAME = "no-database"
INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE = "invalid role(s) for extra user roles"


@pytest.mark.abort_on_fail
async def test_deploy_charms(ops_test: OpsTest, charm):
    """Deploy both charms (application and database) to use in the tests."""
    # Deploy both charms (multiple units for each application to test that later they correctly
    # set data in the relation application databag using only the leader unit).
    async with ops_test.fast_forward():
        await asyncio.gather(
            ops_test.model.deploy(
                APPLICATION_APP_NAME,
                application_name=APPLICATION_APP_NAME,
                num_units=2,
                base=CHARM_BASE,
                channel="edge",
            ),
            ops_test.model.deploy(
                charm,
                application_name=DATABASE_APP_NAME,
                num_units=1,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
            ops_test.model.deploy(
                charm,
                application_name=ANOTHER_DATABASE_APP_NAME,
                num_units=2,
                base=CHARM_BASE,
                config={"profile": "testing"},
            ),
        )

        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", timeout=3000)


async def test_primary_read_only_endpoint_in_standalone_cluster(ops_test: OpsTest):
    """Test that there is no read-only endpoint in a standalone cluster."""
    async with ops_test.fast_forward():
        # Ensure the cluster starts with only one member.
        # We can't scale down a running cluster to 1 unit because the way
        # Patroni raft implementation works (to scale from 2 units to 1 Patroni
        # needs at least one mode unit that run only raft to have quorum).
        assert len(ops_test.model.applications[DATABASE_APP_NAME].units) == 1

        # Relate the charms and wait for them exchanging some connection data.
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

        # Check that on juju 3 we have secrets and no username and password in the rel databag
        logger.info("checking for secrets")
        secret_uri, password = await asyncio.gather(
            get_application_relation_data(
                ops_test,
                APPLICATION_APP_NAME,
                FIRST_DATABASE_RELATION_NAME,
                "secret-user",
            ),
            get_application_relation_data(
                ops_test,
                APPLICATION_APP_NAME,
                FIRST_DATABASE_RELATION_NAME,
                "password",
            ),
        )
        assert secret_uri is not None
        assert password is None

        # Try to get the connection string of the database using the read-only endpoint.
        # It should be the primary.
        primary_unit = ops_test.model.applications[DATABASE_APP_NAME].units[0]
        for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(3), reraise=True):
            with attempt:
                data = await get_application_relation_data(
                    ops_test,
                    APPLICATION_APP_NAME,
                    FIRST_DATABASE_RELATION_NAME,
                    "read-only-endpoints",
                )
                assert data == f"{primary_unit.public_address}:5432"


async def test_read_only_endpoint_in_scaled_up_cluster(ops_test: OpsTest):
    """Test that there is read-only endpoint in a scaled up cluster."""
    async with ops_test.fast_forward():
        # Scale up the database.
        await scale_application(ops_test, DATABASE_APP_NAME, 2)
        primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
        replica = next(
            unit
            for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            if unit.name != primary
        )

        # Try to get the connection string of the database using the read-only endpoint.
        # It should be the replica unit.
        for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(3), reraise=True):
            with attempt:
                data = await get_application_relation_data(
                    ops_test,
                    APPLICATION_APP_NAME,
                    FIRST_DATABASE_RELATION_NAME,
                    "read-only-endpoints",
                )
                assert data == f"{replica.public_address}:5432"


async def test_database_relation_with_charm_libraries(ops_test: OpsTest):
    """Test basic functionality of database relation interface."""
    # Get the connection string to connect to the database using the read/write endpoint.
    connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
    )

    # Connect to the database using the read/write endpoint.
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        # Check that it's possible to write and read data from the database that
        # was created for the application.
        connection.autocommit = True
        cursor.execute("DROP TABLE IF EXISTS test;")
        cursor.execute("CREATE TABLE test(data TEXT);")
        cursor.execute("INSERT INTO test(data) VALUES('some data');")
        cursor.execute("SELECT data FROM test;")
        data = cursor.fetchone()
        assert data[0] == "some data"

        # Check the version that the application received is the same on the database server.
        cursor.execute("SELECT version();")
        data = cursor.fetchone()[0].split(" ")[1]

        # Get the version of the database and compare with the information that
        # was retrieved directly from the database.
        version = await get_application_relation_data(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME, "version"
        )
        assert version == data

    # Get the connection string to connect to the database using the read-only endpoint.
    connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME, read_only_endpoint=True
    )

    # Connect to the database using the read-only endpoint.
    with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
        # Read some data.
        cursor.execute("SELECT data FROM test;")
        data = cursor.fetchone()
        assert data[0] == "some data"

        # Try to alter some data in a read-only transaction.
        with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
            cursor.execute("DROP TABLE test;")


@pytest.mark.abort_on_fail
async def test_filter_out_degraded_replicas(ops_test: OpsTest):
    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    replica = next(
        unit.name
        for unit in ops_test.model.applications[DATABASE_APP_NAME].units
        if unit.name != primary
    )
    machine = await get_machine_from_unit(ops_test, replica)
    await stop_machine(ops_test, machine)

    for attempt in Retrying(stop=stop_after_attempt(3), wait=wait_fixed(3), reraise=True):
        # Topology observer runs every half a minute
        await asyncio.sleep(60)
        with attempt:
            data = await get_application_relation_data(
                ops_test,
                APPLICATION_APP_NAME,
                FIRST_DATABASE_RELATION_NAME,
                "read-only-endpoints",
            )
            assert data == f"{ops_test.model.units[primary].public_address}:5432"

    await start_machine(ops_test, machine)
    await ops_test.model.wait_for_idle(
        apps=[DATABASE_APP_NAME], status="active", timeout=200, raise_on_error=False
    )


async def test_two_applications_doesnt_share_the_same_relation_data(ops_test: OpsTest):
    """Test that two different application connect to the database with different credentials."""
    # Set some variables to use in this test.
    another_application_app_name = "another-application"
    all_app_names = [another_application_app_name]
    all_app_names.extend(APP_NAMES)

    # Deploy another application.
    await ops_test.model.deploy(
        APPLICATION_APP_NAME,
        application_name=another_application_app_name,
        channel="edge",
        base=CHARM_BASE,
    )
    await ops_test.model.wait_for_idle(apps=all_app_names, status="active")

    # Relate the new application with the database
    # and wait for them exchanging some connection data.
    await ops_test.model.add_relation(
        f"{another_application_app_name}:{FIRST_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
    )
    await ops_test.model.wait_for_idle(apps=all_app_names, status="active")

    # Assert the two application have different relation (connection) data.
    application_connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
    )
    another_application_connection_string = await build_connection_string(
        ops_test, another_application_app_name, FIRST_DATABASE_RELATION_NAME
    )

    assert application_connection_string != another_application_connection_string

    # Check that the user cannot access other databases.
    for application, other_application_database in [
        (APPLICATION_APP_NAME, "another_application_database"),
        (another_application_app_name, f"{APPLICATION_APP_NAME.replace('-', '_')}_database"),
    ]:
        connection_string = await build_connection_string(
            ops_test,
            application,
            FIRST_DATABASE_RELATION_NAME,
            database=DATABASE_DEFAULT_NAME,
        )
        with pytest.raises(psycopg2.Error):
            psycopg2.connect(connection_string)
        connection_string = await build_connection_string(
            ops_test,
            application,
            FIRST_DATABASE_RELATION_NAME,
            database=other_application_database,
        )
        with pytest.raises(psycopg2.Error):
            psycopg2.connect(connection_string)


async def test_an_application_can_connect_to_multiple_database_clusters(ops_test: OpsTest):
    """Test that an application can connect to different clusters of the same database."""
    # Relate the application with both database clusters
    # and wait for them exchanging some connection data.
    first_cluster_relation = await ops_test.model.add_relation(
        f"{APPLICATION_APP_NAME}:{MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}", DATABASE_APP_NAME
    )
    second_cluster_relation = await ops_test.model.add_relation(
        f"{APPLICATION_APP_NAME}:{MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}",
        ANOTHER_DATABASE_APP_NAME,
    )
    await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

    # Retrieve the connection string to both database clusters using the relation aliases
    # and assert they are different.
    application_connection_string = await build_connection_string(
        ops_test,
        APPLICATION_APP_NAME,
        MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
        relation_id=first_cluster_relation.id,
    )
    another_application_connection_string = await build_connection_string(
        ops_test,
        APPLICATION_APP_NAME,
        MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
        relation_id=second_cluster_relation.id,
    )
    assert application_connection_string != another_application_connection_string


# @pytest.mark.skip(reason="Unstable")
# async def test_an_application_can_connect_to_multiple_aliased_database_clusters(ops_test: OpsTest):
#     """Test that an application can connect to different clusters of the same database."""
#     # Relate the application with both database clusters
#     # and wait for them exchanging some connection data.
#     await asyncio.gather(
#         ops_test.model.add_relation(
#             f"{APPLICATION_APP_NAME}:{ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}",
#             DATABASE_APP_NAME,
#         ),
#         ops_test.model.add_relation(
#             f"{APPLICATION_APP_NAME}:{ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME}",
#             ANOTHER_DATABASE_APP_NAME,
#         ),
#     )
#     await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

#     # Retrieve the connection string to both database clusters using the relation aliases
#     # and assert they are different.
#     application_connection_string = await build_connection_string(
#         ops_test,
#         APPLICATION_APP_NAME,
#         ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
#         relation_alias="cluster1",
#     )
#     another_application_connection_string = await build_connection_string(
#         ops_test,
#         APPLICATION_APP_NAME,
#         ALIASED_MULTIPLE_DATABASE_CLUSTERS_RELATION_NAME,
#         relation_alias="cluster2",
#     )
#     assert application_connection_string != another_application_connection_string


async def test_an_application_can_request_multiple_databases(ops_test: OpsTest):
    """Test that an application can request additional databases using the same interface."""
    # Relate the charms using another relation and wait for them exchanging some connection data.
    await ops_test.model.add_relation(
        f"{APPLICATION_APP_NAME}:{SECOND_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
    )
    await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active")

    # Get the connection strings to connect to both databases.
    first_database_connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
    )
    second_database_connection_string = await build_connection_string(
        ops_test, APPLICATION_APP_NAME, SECOND_DATABASE_RELATION_NAME
    )

    # Assert the two application have different relation (connection) data.
    assert first_database_connection_string != second_database_connection_string


@pytest.mark.abort_on_fail
async def test_relation_data_is_updated_correctly_when_scaling(ops_test: OpsTest):
    """Test that relation data, like connection data, is updated correctly when scaling."""
    # Retrieve the list of current database unit names.
    units_to_remove = [unit.name for unit in ops_test.model.applications[DATABASE_APP_NAME].units]

    async with ops_test.fast_forward(fast_interval="60s"):
        # Add two more units.
        await ops_test.model.applications[DATABASE_APP_NAME].add_units(2)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=3000, wait_for_exact_units=4
        )

        assert_sync_standbys(
            ops_test.model.applications[DATABASE_APP_NAME].units[0].public_address, 2
        )

        # Remove the original units.
        leader_unit = await get_leader_unit(ops_test, DATABASE_APP_NAME)
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(*[
            unit for unit in units_to_remove if unit != leader_unit.name
        ])
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=600, wait_for_exact_units=3
        )
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_units(leader_unit.name)
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=600, wait_for_exact_units=2
        )

        # Get the updated connection data and assert it can be used
        # to write and read some data properly.
        primary_connection_string = await build_connection_string(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME
        )
        replica_connection_string = await build_connection_string(
            ops_test, APPLICATION_APP_NAME, FIRST_DATABASE_RELATION_NAME, read_only_endpoint=True
        )

        # Connect to the database using the primary connection string.
        with psycopg2.connect(primary_connection_string) as connection:
            connection.autocommit = True
            with connection.cursor() as cursor:
                # Check that it's possible to write and read data from the database that
                # was created for the application.
                cursor.execute("DROP TABLE IF EXISTS test;")
                cursor.execute("CREATE TABLE test(data TEXT);")
                cursor.execute("INSERT INTO test(data) VALUES('some data');")
                cursor.execute("SELECT data FROM test;")
                data = cursor.fetchone()
                assert data[0] == "some data"
        connection.close()

        # Connect to the database using the replica endpoint.
        with (
            psycopg2.connect(replica_connection_string) as connection,
            connection.cursor() as cursor,
        ):
            # Read some data.
            cursor.execute("SELECT data FROM test;")
            data = cursor.fetchone()
            assert data[0] == "some data"

            # Try to alter some data in a read-only transaction.
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cursor.execute("DROP TABLE test;")
        connection.close()

    async with ops_test.fast_forward():
        # Remove the relation and test that its user was deleted
        # (by checking that the connection string doesn't work anymore).
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}:database",
            f"{APPLICATION_APP_NAME}:{FIRST_DATABASE_RELATION_NAME}",
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME], status="active", timeout=1000, idle_period=30
        )
        with pytest.raises(psycopg2.OperationalError):
            psycopg2.connect(primary_connection_string)


async def test_relation_with_no_database_name(ops_test: OpsTest):
    """Test that a relation with no database name doesn't block the charm."""
    async with ops_test.fast_forward():
        # Relate the charms using a relation that doesn't provide a database name.
        await ops_test.model.add_relation(
            f"{APPLICATION_APP_NAME}:{NO_DATABASE_RELATION_NAME}", DATABASE_APP_NAME
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", raise_on_blocked=True)

        # Break the relation.
        await ops_test.model.applications[DATABASE_APP_NAME].remove_relation(
            f"{DATABASE_APP_NAME}", f"{APPLICATION_APP_NAME}:{NO_DATABASE_RELATION_NAME}"
        )
        await ops_test.model.wait_for_idle(apps=APP_NAMES, status="active", raise_on_blocked=True)


async def test_invalid_extra_user_roles(ops_test: OpsTest):
    async with ops_test.fast_forward():
        await ops_test.model.deploy(DATA_INTEGRATOR_APP_NAME, base=CHARM_BASE)
        await ops_test.model.wait_for_idle(apps=[DATA_INTEGRATOR_APP_NAME], status="blocked")

        another_data_integrator_app_name = f"another-{DATA_INTEGRATOR_APP_NAME}"
        data_integrator_apps_names = [DATA_INTEGRATOR_APP_NAME, another_data_integrator_app_name]
        await ops_test.model.deploy(
            DATA_INTEGRATOR_APP_NAME,
            application_name=another_data_integrator_app_name,
            base=CHARM_BASE,
        )
        await ops_test.model.wait_for_idle(
            apps=[another_data_integrator_app_name], status="blocked"
        )
        for app in data_integrator_apps_names:
            await ops_test.model.applications[app].set_config({
                "database-name": app.replace("-", "_"),
                "extra-user-roles": "test",
            })
        await ops_test.model.wait_for_idle(apps=data_integrator_apps_names, status="blocked")
        for app in data_integrator_apps_names:
            await ops_test.model.add_relation(f"{app}:postgresql", f"{DATABASE_APP_NAME}:database")
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME])
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status_message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=1000,
        )

        # Verify that the charm remains blocked if there are still other relations with invalid
        # extra user roles.
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
            f"{DATABASE_APP_NAME}:database", f"{DATA_INTEGRATOR_APP_NAME}:postgresql"
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME])
        await ops_test.model.block_until(
            lambda: any(
                unit.workload_status_message == INVALID_EXTRA_USER_ROLE_BLOCKING_MESSAGE
                for unit in ops_test.model.applications[DATABASE_APP_NAME].units
            ),
            timeout=1000,
        )

        # Verify that active status is restored after all relations are removed.
        await ops_test.model.applications[DATABASE_APP_NAME].destroy_relation(
            f"{DATABASE_APP_NAME}:database", f"{another_data_integrator_app_name}:postgresql"
        )
        await ops_test.model.wait_for_idle(
            apps=[DATABASE_APP_NAME],
            status="active",
            raise_on_blocked=False,
            timeout=1000,
        )
