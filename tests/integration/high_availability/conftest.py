#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest

from .high_availability_helpers_new import get_app_leader

logger = logging.getLogger(__name__)

DB_TEST_APP_NAME = "postgresql-test-app"


@pytest.fixture()
def continuous_writes(juju):
    """Starts continuous writes to the MySQL cluster for a test and clear the writes at the end."""
    application_unit = get_app_leader(juju, DB_TEST_APP_NAME)

    logger.info("Clearing continuous writes")
    juju.run(unit=application_unit, action="clear-continuous-writes", wait=120)

    logger.info("Starting continuous writes")
    juju.run(unit=application_unit, action="start-continuous-writes")

    yield

    logger.info("Clearing continuous writes")
    juju.run(unit=application_unit, action="clear-continuous-writes", wait=120)
