#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
import pytest_asyncio
from _pytest.config import Config
from pytest_operator.plugin import OpsTest


OpsTestK8s = OpsTest


def pytest_configure(config: Config):
    config.addinivalue_line("markers", "abort_on_fail_k8s")


@pytest.fixture(scope="module")
async def charm(ops_test: OpsTest):
    """Build the charm-under-test."""
    # Build charm from local source folder.
    yield await ops_test.build_charm(".")


@pytest.fixture(autouse=True)
def abort_on_fail_k8s(request):
    if OpsTestK8s._instance is None:
        # If we don't have an ops_test already in play, this should be a no-op.
        yield
        return
    ops_test = OpsTestK8s._instance
    if ops_test.aborted:
        pytest.xfail("aborted")

    yield
    abort_on_fail = request.node.get_closest_marker("abort_on_fail_k8s")
    failed = getattr(request.node, "failed", False)
    if abort_on_fail and abort_on_fail.kwargs.get("abort_on_xfail", False):
        failed = failed or request.node.xfailed
    if failed and abort_on_fail:
        ops_test.aborted = True


@pytest_asyncio.fixture(scope="module")
async def ops_test_k8s(request, tmp_path_factory):
    request.config.option.controller = "microk8s-localhost"
    ops_test = OpsTestK8s(request, tmp_path_factory)
    await ops_test._setup_model()
    OpsTestK8s._instance = ops_test
    yield ops_test
    OpsTestK8s._instance = None
    await ops_test._cleanup_models()