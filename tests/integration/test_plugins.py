#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    CHARM_SERIES,
    DATABASE_APP_NAME,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
)

logger = logging.getLogger(__name__)

CITEXT_EXTENSION_STATEMENT = "CREATE TABLE citext_test (value CITEXT);"
DEBVERSION_EXTENSION_STATEMENT = "CREATE TABLE debversion_test (value DEBVERSION);"
HSTORE_EXTENSION_STATEMENT = "CREATE TABLE hstore_test (value hstore);"
PG_TRGM_EXTENSION_STATEMENT = "SELECT word_similarity('word', 'two words');"
PLPYTHON3U_EXTENSION_STATEMENT = 'CREATE FUNCTION plpython_test() RETURNS varchar[] AS $$ return "hello" $$ LANGUAGE plpython3u;'
UNACCENT_EXTENSION_STATEMENT = "SELECT ts_lexize('unaccent','Hôtel');"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_plugins(ops_test: OpsTest) -> None:
    """Build and deploy one unit of PostgreSQL and then test the available plugins."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        charm = await ops_test.build_charm(".")
        await ops_test.model.deploy(
            charm,
            num_units=2,
            series=CHARM_SERIES,
            config={"profile": "testing"},
        )
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    # Check that the available plugins are disabled.
    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    password = await get_password(ops_test, primary)
    address = get_unit_address(ops_test, primary)
    logger.info("checking that the plugins are disabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True

        # Test citext extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(CITEXT_EXTENSION_STATEMENT)

        # Test debversion extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(DEBVERSION_EXTENSION_STATEMENT)

        # Test hstore extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(HSTORE_EXTENSION_STATEMENT)

        # Test pg_trgm extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PG_TRGM_EXTENSION_STATEMENT)

        # Test PL/Python extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PLPYTHON3U_EXTENSION_STATEMENT)

        # Test unaccent extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(UNACCENT_EXTENSION_STATEMENT)
    connection.close()

    # Enable the plugins.
    logger.info("enabling the plugins")
    config = {
        "plugin_citext_enable": "True",
        "plugin_debversion_enable": "True",
        "plugin_hstore_enable": "True",
        "plugin_pg_trgm_enable": "True",
        "plugin_plpython3u_enable": "True",
        "plugin_unaccent_enable": "True",
    }
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    # Check that the available plugins are enabled.
    logger.info("checking that the plugins are enabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True

        # Test citext extension enabled.
        connection.cursor().execute(CITEXT_EXTENSION_STATEMENT)

        # Test debversion extension enabled.
        connection.cursor().execute(DEBVERSION_EXTENSION_STATEMENT)

        # Test hstore extension enabled.
        connection.cursor().execute(HSTORE_EXTENSION_STATEMENT)

        # Test pg_trgm extension enabled.
        connection.cursor().execute(PG_TRGM_EXTENSION_STATEMENT)

        # Test PL/Python extension enabled.
        connection.cursor().execute(PLPYTHON3U_EXTENSION_STATEMENT)

        # Test unaccent extension enabled.
        connection.cursor().execute(UNACCENT_EXTENSION_STATEMENT)
    connection.close()
