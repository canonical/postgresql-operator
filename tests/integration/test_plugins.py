#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
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
BLOOM_EXTENSION_STATEMENT = (
    "CREATE TABLE tbloom_test (i int);CREATE INDEX btreeidx ON tbloom_test USING bloom (i);"
)
BTREEGIN_EXTENSION_STATEMENT = "CREATE TABLE btree_gin_test (a int4);CREATE INDEX btreeginidx ON btree_gin_test USING GIN (a);"
BTREEGIST_EXTENSION_STATEMENT = "CREATE TABLE btree_gist_test (a int4);CREATE INDEX btreegistidx ON btree_gist_test USING GIST (a);"
CUBE_EXTENSION_STATEMENT = "SELECT cube_inter('(0,-1),(1,1)', '(-2),(2)');"
DICTINT_EXTENSION_STATEMENT = "SELECT ts_lexize('intdict', '12345678');"
DICTXSYN_EXTENSION_STATEMENT = "SELECT ts_lexize('xsyn', 'word');"
EARTHDISTANCE_EXTENSION_STATEMENT = "SELECT earth_distance(ll_to_earth(-81.3927381, 30.2918842),ll_to_earth(-87.6473133, 41.8853881));"
FUZZYSTRMATCH_EXTENSION_STATEMENT = "SELECT soundex('hello world!');"
INTARRAY_EXTENSION_STATEMENT = "CREATE TABLE intarray_test (mid INT PRIMARY KEY, sections INT[]);SELECT intarray_test.mid FROM intarray_test WHERE intarray_test.sections @> '{1,2}';"
ISN_EXTENSION_STATEMENT = "SELECT isbn('978-0-393-04002-9');"
LO_EXTENSION_STATEMENT = "CREATE TABLE lo_test (value lo);"
LTREE_EXTENSION_STATEMENT = "CREATE TABLE ltree_test (path ltree);"
OLD_SNAPSHOT_EXTENSION_STATEMENT = "SELECT * from pg_old_snapshot_time_mapping();"
PG_FREESPACEMAP_EXTENSION_STATEMENT = (
    "CREATE TABLE pg_freespacemap_test (i int);SELECT * FROM pg_freespace('pg_freespacemap_test');"
)
PGROWLOCKS_EXTENSION_STATEMENT = (
    "CREATE TABLE pgrowlocks_test (i int);SELECT * FROM pgrowlocks('pgrowlocks_test');"
)
PGSTATTUPLE_EXTENSION_STATEMENT = "SELECT * FROM pgstattuple('pg_catalog.pg_proc');"
PG_VISIBILITY_EXTENSION_STATEMENT = "CREATE TABLE pg_visibility_test (i int);SELECT * FROM pg_visibility('pg_visibility_test'::regclass);"
SEG_EXTENSION_STATEMENT = "SELECT '10(+-)1'::seg as seg;"
TABLEFUNC_EXTENSION_STATEMENT = "SELECT * FROM normal_rand(1000, 5, 3);"
TCN_EXTENSION_STATEMENT = "CREATE TABLE tcn_test (i int);CREATE TRIGGER tcn_test_idx AFTER INSERT OR UPDATE OR DELETE ON tcn_test FOR EACH ROW EXECUTE FUNCTION TRIGGERED_CHANGE_NOTIFICATION();"
TSM_SYSTEM_ROWS_EXTENSION_STATEMENT = "CREATE TABLE tsm_system_rows_test (i int);SELECT * FROM tsm_system_rows_test TABLESAMPLE SYSTEM_ROWS(100);"
TSM_SYSTEM_TIME_EXTENSION_STATEMENT = "CREATE TABLE tsm_system_time_test (i int);SELECT * FROM tsm_system_time_test TABLESAMPLE SYSTEM_TIME(1000);"
UUID_OSSP_EXTENSION_STATEMENT = "SELECT uuid_nil();"
REFINT_EXTENSION_STATEMENT = "CREATE TABLE A (ID int4 not null); CREATE UNIQUE INDEX AI ON A (ID);CREATE TABLE B (REFB int4);CREATE INDEX BI ON B (REFB);CREATE TRIGGER BT BEFORE INSERT OR UPDATE ON B FOR EACH ROW EXECUTE PROCEDURE check_primary_key ('REFB', 'A', 'ID');"
AUTOINC_EXTENSION_STATEMENT = "CREATE TABLE ids (id int4, idesc text);CREATE TRIGGER ids_nextid BEFORE INSERT OR UPDATE ON ids FOR EACH ROW EXECUTE PROCEDURE autoinc (id, next_id);"
INSERT_USERNAME_EXTENSION_STATEMENT = "CREATE TABLE username_test (name text, username text not null);CREATE TRIGGER insert_usernames BEFORE INSERT OR UPDATE ON username_test FOR EACH ROW EXECUTE PROCEDURE insert_username (username);"
MODDATETIME_EXTENSION_STATEMENT = "CREATE TABLE mdt (moddate timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL);CREATE TRIGGER mdt_moddatetime BEFORE UPDATE ON mdt FOR EACH ROW EXECUTE PROCEDURE moddatetime (moddate);"


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
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active", timeout=1500)

    sql_tests = {
        "plugin_citext_enable": CITEXT_EXTENSION_STATEMENT,
        "plugin_debversion_enable": DEBVERSION_EXTENSION_STATEMENT,
        "plugin_hstore_enable": HSTORE_EXTENSION_STATEMENT,
        "plugin_pg_trgm_enable": PG_TRGM_EXTENSION_STATEMENT,
        "plugin_plpython3u_enable": PLPYTHON3U_EXTENSION_STATEMENT,
        "plugin_unaccent_enable": UNACCENT_EXTENSION_STATEMENT,
        "plugin_bloom_enable": BLOOM_EXTENSION_STATEMENT,
        "plugin_btree_gin_enable": BTREEGIN_EXTENSION_STATEMENT,
        "plugin_btree_gist_enable": BTREEGIST_EXTENSION_STATEMENT,
        "plugin_cube_enable": CUBE_EXTENSION_STATEMENT,
        "plugin_dict_int_enable": DICTINT_EXTENSION_STATEMENT,
        "plugin_dict_xsyn_enable": DICTXSYN_EXTENSION_STATEMENT,
        "plugin_earthdistance_enable": EARTHDISTANCE_EXTENSION_STATEMENT,
        "plugin_fuzzystrmatch_enable": FUZZYSTRMATCH_EXTENSION_STATEMENT,
        "plugin_intarray_enable": INTARRAY_EXTENSION_STATEMENT,
        "plugin_isn_enable": ISN_EXTENSION_STATEMENT,
        "plugin_lo_enable": LO_EXTENSION_STATEMENT,
        "plugin_ltree_enable": LTREE_EXTENSION_STATEMENT,
        "plugin_old_snapshot_enable": OLD_SNAPSHOT_EXTENSION_STATEMENT,
        "plugin_pg_freespacemap_enable": PG_FREESPACEMAP_EXTENSION_STATEMENT,
        "plugin_pgrowlocks_enable": PGROWLOCKS_EXTENSION_STATEMENT,
        "plugin_pgstattuple_enable": PGSTATTUPLE_EXTENSION_STATEMENT,
        "plugin_pg_visibility_enable": PG_VISIBILITY_EXTENSION_STATEMENT,
        "plugin_seg_enable": SEG_EXTENSION_STATEMENT,
        "plugin_tablefunc_enable": TABLEFUNC_EXTENSION_STATEMENT,
        "plugin_tcn_enable": TCN_EXTENSION_STATEMENT,
        "plugin_tsm_system_rows_enable": TSM_SYSTEM_ROWS_EXTENSION_STATEMENT,
        "plugin_tsm_system_time_enable": TSM_SYSTEM_TIME_EXTENSION_STATEMENT,
        "plugin_uuid_ossp_enable": UUID_OSSP_EXTENSION_STATEMENT,
        "plugin_spi_enable": [
            REFINT_EXTENSION_STATEMENT,
            AUTOINC_EXTENSION_STATEMENT,
            INSERT_USERNAME_EXTENSION_STATEMENT,
            MODDATETIME_EXTENSION_STATEMENT,
        ],
    }

    def enable_disable_config(enabled: False):
        config = {}
        for plugin in sql_tests.keys():
            config[plugin] = f"{enabled}"
        return config

    # Check that the available plugins are disabled.
    primary = await get_primary(ops_test, f"{DATABASE_APP_NAME}/0")
    password = await get_password(ops_test, primary)
    address = get_unit_address(ops_test, primary)

    config = enable_disable_config(False)
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    logger.info("checking that the plugins are disabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        for query in sql_tests.values():
            if isinstance(query, list):
                for test in query:
                    with pytest.raises(psycopg2.Error):
                        connection.cursor().execute(test)
            else:
                with pytest.raises(psycopg2.Error):
                    connection.cursor().execute(query)
    connection.close()

    # Enable the plugins.
    logger.info("enabling the plugins")

    config = enable_disable_config(True)
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    # Check that the available plugins are enabled.
    logger.info("checking that the plugins are enabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        for query in sql_tests.values():
            if isinstance(query, list):
                for test in query:
                    connection.cursor().execute(test)
            else:
                connection.cursor().execute(query)
    connection.close()
