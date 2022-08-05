#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    check_database_users_existence,
    check_databases_creation,
    deploy_and_relate_bundle_with_postgresql,
)

logger = logging.getLogger(__name__)

HAPROXY_APP_NAME = "haproxy"
LANDSCAPE_APP_NAME = "landscape-server"
LANDSCAPE_SCALABLE_BUNDLE_NAME = "ch:landscape-scalable"
LANDSCAPE_SCALABLE_BUNDLE_OVERLAY_PATH = "./tests/integration/landscape_scalable_overlay.yaml"
RABBITMQ_APP_NAME = "rabbitmq-server"


# async def test_landscape_scalable_bundle_db(ops_test: OpsTest) -> None:
#     await prepare_overlay(ops_test, LANDSCAPE_SCALABLE_BUNDLE_NAME)


async def test_landscape_scalable_bundle_db(ops_test: OpsTest, charm: str) -> None:
    """Deploy Landscape Scalable Bundle to test the 'db-admin' relation."""
    config = {
        "extra-packages": "python-apt postgresql-contrib postgresql-.*-debversion postgresql-plpython.*"
    }
    resources = {"patroni": "patroni.tar.gz"}
    await ops_test.model.deploy(
        charm, config=config, resources=resources, application_name=DATABASE_APP_NAME
    )
    # Attach the resource to the controller.
    await ops_test.juju("attach-resource", DATABASE_APP_NAME, "patroni=patroni.tar.gz")

    # Deploy and test the Landscape Scalable bundle (using this PostgreSQL charm).
    relation_id = await deploy_and_relate_bundle_with_postgresql(
        ops_test,
        LANDSCAPE_SCALABLE_BUNDLE_NAME,
        LANDSCAPE_SCALABLE_BUNDLE_OVERLAY_PATH,
        LANDSCAPE_APP_NAME,
    )
    await check_databases_creation(
        ops_test,
        [
            "landscape-account-1",
            "landscape-knowledge",
            "landscape-main",
            "landscape-package",
            "landscape-resource-1",
            "landscape-session",
        ],
    )

    landscape_users = [f"relation_id_{relation_id}"]

    await check_database_users_existence(ops_test, landscape_users, [])

    # Remove the applications from the bundle.
    await ops_test.model.remove_application(HAPROXY_APP_NAME, block_until_done=True)
    await ops_test.model.remove_application(LANDSCAPE_SCALABLE_BUNDLE_NAME, block_until_done=True)
    await ops_test.model.remove_application(RABBITMQ_APP_NAME, block_until_done=True)
    await ops_test.model.remove_application(DATABASE_APP_NAME, block_until_done=True)
