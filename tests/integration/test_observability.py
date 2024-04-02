import asyncio

import pytest_asyncio
from _pytest.config import Config
from pytest_operator.plugin import OpsTest
import pytest
from integration.helpers import CHARM_SERIES, APPLICATION_NAME
from integration.conftest import OpsTestK8s


COS_BUNDLE_NAME = "cos-lite"
GRAFANA_AGENT_APPLICATION_NAME = "grafana-agent"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
@pytest.mark.abort_on_fail_k8s
async def test_deploy(ops_test: OpsTest, ops_test_k8s: OpsTestK8s, charm: str):
    await asyncio.gather(
        ops_test.model.deploy(
            charm,
            application_name=APPLICATION_NAME,
            num_units=2,
            series=CHARM_SERIES,
            config={"profile": "testing"},
        ),
        ops_test.model.deploy(GRAFANA_AGENT_APPLICATION_NAME),
        ops_test_k8s.model.deploy(
            COS_BUNDLE_NAME,
            trust=True
        ),
    )

    await asyncio.gather(
        ops_test.model.wait_for_idle(apps=[APPLICATION_NAME], status="active", timeout=1000),
        ops_test_k8s.model.wait_for_idle(status="active", timeout=1000),
    )

    await ops_test.model.integrate(APPLICATION_NAME, GRAFANA_AGENT_APPLICATION_NAME)

    await ops_test.model.wait_for_idle()

    # Setup monitoring integrations (with cos-lite).
    await ops_test_k8s.model.create_offer("grafana:grafana-dashboard", "grafana-dashboards")
    await ops_test_k8s.model.create_offer("loki:logging", "loki-logging")
    await ops_test_k8s.model.create_offer("prometheus:receive-remote-write", "prometheus-receive-remote-write")
    await ops_test.model.consume("admin/cos.grafana-dashboards", controller_name="microk8s-localhost")
    await ops_test.model.consume("admin/cos.loki-logging", controller_name="microk8s-localhost")
    await ops_test.model.consume("admin/cos.prometheus-receive-remote-write", controller_name="microk8s-localhost")
    await ops_test.model.integrate(GRAFANA_AGENT_APPLICATION_NAME, "grafana-dashboards")
    await ops_test.model.integrate(GRAFANA_AGENT_APPLICATION_NAME, "loki-logging")
    await ops_test.model.integrate(GRAFANA_AGENT_APPLICATION_NAME, "prometheus-receive-remote-write")

    await asyncio.gather(
        ops_test.model.wait_for_idle(status="active"),
        ops_test_k8s.model.wait_for_idle(status="active"),
    )


