#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from pathlib import Path

import pytest
import requests
import yaml
from alertmanager import Alertmanager
from pytest_operator.plugin import OpsTest  # type: ignore[import]  # noqa: F401

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
ALERTMANAGER_CONFIGURER_APP_NAME = METADATA["name"]
ALERTMANAGER_APP_NAME = "alertmanager-k8s"
WAIT_FOR_STATUS_TIMEOUT = 1 * 60
DUMMY_HTTP_SERVER_PORT = 80


class TestAlertmanagerConfigurerOperatorCharm:
    @pytest.fixture(scope="module")
    @pytest.mark.abort_on_fail
    async def setup(self, ops_test: OpsTest):
        await ops_test.model.set_config({"update-status-hook-interval": "2s"})
        await self._deploy_alertmanager_k8s(ops_test)
        charm = await ops_test.build_charm(".")
        resources = {
            f"{ALERTMANAGER_CONFIGURER_APP_NAME}-image": METADATA["resources"][
                f"{ALERTMANAGER_CONFIGURER_APP_NAME}-image"
            ]["upstream-source"],
            "dummy-http-server-image": METADATA["resources"]["dummy-http-server-image"][
                "upstream-source"
            ],
        }
        await ops_test.model.deploy(
            charm,
            resources=resources,
            application_name=ALERTMANAGER_CONFIGURER_APP_NAME,
            trust=True,
        )

    @pytest.mark.abort_on_fail
    async def test_given_alertmanager_configurer_charm_is_not_related_to_alertmanager_when_charm_deployed_then_charm_goes_to_blocked_status(  # noqa: E501
        self, ops_test: OpsTest, setup
    ):
        await ops_test.model.wait_for_idle(
            apps=[ALERTMANAGER_CONFIGURER_APP_NAME],
            status="blocked",
            timeout=WAIT_FOR_STATUS_TIMEOUT,
        )

    # @pytest.mark.abort_on_fail
    # async def test_given_alertmanager_configurer_charm_in_blocked_status_when_alertmanager_relation_created_then_charm_goes_to_active_status(  # noqa: E501
    #     self, ops_test: OpsTest, setup
    # ):
    #     await ops_test.model.add_relation(
    #         relation1=f"{ALERTMANAGER_CONFIGURER_APP_NAME}",
    #         relation2=f"{ALERTMANAGER_APP_NAME}:remote-configurer",
    #     )
    #     await ops_test.model.wait_for_idle(
    #         apps=[ALERTMANAGER_CONFIGURER_APP_NAME],
    #         status="active",
    #         timeout=WAIT_FOR_STATUS_TIMEOUT,
    #     )

    @pytest.mark.abort_on_fail
    async def test_given_alertmanager_configurer_running_when_post_sent_to_the_dummy_http_server_called_then_server_responds_with_200(  # noqa: E501
        self, ops_test: OpsTest, setup
    ):
        dummy_http_server_ip = await _unit_address(ops_test, ALERTMANAGER_CONFIGURER_APP_NAME, 0)
        dummy_server_response = requests.post(
            f"http://{dummy_http_server_ip}:{DUMMY_HTTP_SERVER_PORT}"
        )
        assert dummy_server_response.status_code == 200

    @staticmethod
    async def _deploy_alertmanager_k8s(ops_test: OpsTest):
        await ops_test.model.deploy(
            ALERTMANAGER_APP_NAME,
            application_name=ALERTMANAGER_APP_NAME,
            channel="edge",
            trust=True,
        )


async def _unit_address(ops_test: OpsTest, app_name: str, unit_num: int) -> str:
    """Find unit address for any application.

    Args:
        ops_test: pytest-operator plugin
        app_name: string name of application
        unit_num: integer number of a juju unit

    Returns:
        str: unit address as a string
    """
    status = await ops_test.model.get_status()
    return status["applications"][app_name]["units"][f"{app_name}/{unit_num}"]["address"]


async def _get_alertmanager_config(ops_test: OpsTest, app_name: str, unit_num: int) -> str:
    """Fetch Alertmanager config.

    Args:
        ops_test: pytest-operator plugin
        app_name: string name of Prometheus application
        unit_num: integer number of a Prometheus juju unit

    Returns:
          str: YAML config in string format or empty string
    """
    host = await _unit_address(ops_test, app_name, unit_num)
    alertmanager = Alertmanager(host=host)
    config = await alertmanager.config()
    return config
