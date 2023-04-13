#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
import requests
import yaml
from alertmanager import Alertmanager
from deepdiff import DeepDiff
from pytest_operator.plugin import OpsTest  # type: ignore[import]  # noqa: F401

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
ALERTMANAGER_CONFIGURER_APP_NAME = METADATA["name"]
ALERTMANAGER_CONFIGURER_DEFAULT_CONFIG = yaml.safe_load(Path("./src/alertmanager.yml").read_text())
ALERTMANAGER_APP_NAME = "alertmanager-k8s"
WAIT_FOR_STATUS_TIMEOUT = 5 * 60
DUMMY_HTTP_SERVER_PORT = 80
TEST_TENANT = "test-tenant"
TEST_RECEIVER_NAME = "example"


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
            series="focal",
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

    @pytest.mark.abort_on_fail
    async def test_given_alertmanager_configurer_charm_in_blocked_status_when_alertmanager_relation_created_then_charm_goes_to_active_status(  # noqa: E501, W505
        self, ops_test: OpsTest, setup
    ):
        await ops_test.model.add_relation(
            relation1=f"{ALERTMANAGER_CONFIGURER_APP_NAME}",
            relation2=f"{ALERTMANAGER_APP_NAME}:remote-configuration",
        )
        await ops_test.model.wait_for_idle(
            apps=[ALERTMANAGER_CONFIGURER_APP_NAME],
            status="active",
            timeout=WAIT_FOR_STATUS_TIMEOUT,
        )

    @pytest.mark.abort_on_fail
    async def test_given_alertmanager_configurer_running_when_post_sent_to_the_dummy_http_server_called_then_server_responds_with_200(  # noqa: E501
        self, ops_test: OpsTest, setup
    ):
        dummy_http_server_ip = await _unit_address(ops_test, ALERTMANAGER_CONFIGURER_APP_NAME, 0)
        dummy_server_response = requests.post(
            f"http://{dummy_http_server_ip}:{DUMMY_HTTP_SERVER_PORT}"
        )
        assert dummy_server_response.status_code == 200

    @pytest.mark.abort_on_fail
    async def test_given_alertmanager_configurer_ready_when_get_alertmanager_config_then_alertmanager_has_config_from_alertmanager_configurer(  # noqa: E501
        self, ops_test: OpsTest, setup
    ):
        expected_config = deepcopy(ALERTMANAGER_CONFIGURER_DEFAULT_CONFIG)
        expected_config = await _add_juju_topology_to_group_by(expected_config)

        alertmanager_config_raw = await _get_alertmanager_config(
            ops_test, ALERTMANAGER_APP_NAME, 0
        )
        alertmanager_config = yaml.safe_load(alertmanager_config_raw)

        assert await _get_config_difs(expected_config, alertmanager_config) == {}

    @pytest.mark.abort_on_fail
    async def test_given_alertmanager_configurer_ready_when_new_receiver_created_then_alertmanager_config_is_updated_with_the_new_receiver(  # noqa: E501
        self, ops_test: OpsTest, setup
    ):
        test_receiver_json = {
            "name": f"{TEST_RECEIVER_NAME}",
            "webhook_configs": [{"url": "http://receiver_example.com"}],
        }
        expected_config = deepcopy(ALERTMANAGER_CONFIGURER_DEFAULT_CONFIG)
        expected_config = await _add_juju_topology_to_group_by(expected_config)
        expected_config = await _add_new_receiver(expected_config, test_receiver_json)
        alertmanager_configurer_server_ip = await _unit_address(
            ops_test, ALERTMANAGER_CONFIGURER_APP_NAME, 0
        )

        server_response = requests.post(
            f"http://{alertmanager_configurer_server_ip}:9101/v1/{TEST_TENANT}/receiver",
            json=test_receiver_json,
        )
        assert server_response.status_code == 200

        # Wait for Alertmanager to apply new config
        await ops_test.model.wait_for_idle(
            apps=[ALERTMANAGER_APP_NAME],
            status="active",
            timeout=WAIT_FOR_STATUS_TIMEOUT,
            idle_period=5,
        )

        alertmanager_config_raw = await _get_alertmanager_config(
            ops_test, ALERTMANAGER_APP_NAME, 0
        )
        alertmanager_config = yaml.safe_load(alertmanager_config_raw)

        assert await _get_config_difs(expected_config, alertmanager_config) == {}

    @pytest.mark.abort_on_fail
    async def test_given_alertmanager_configurer_ready_when_delete_receiver_then_receiver_is_removed_from_alertmanager_config(  # noqa: E501
        self, ops_test: OpsTest, setup
    ):
        expected_config = deepcopy(ALERTMANAGER_CONFIGURER_DEFAULT_CONFIG)
        expected_config = await _add_juju_topology_to_group_by(expected_config)
        alertmanager_configurer_server_ip = await _unit_address(
            ops_test, ALERTMANAGER_CONFIGURER_APP_NAME, 0
        )

        server_response = requests.delete(
            f"http://{alertmanager_configurer_server_ip}:9101/v1/{TEST_TENANT}/receiver/{TEST_RECEIVER_NAME}"  # noqa: E501, W505
        )
        assert server_response.status_code == 200

        # Wait for Alertmanager to apply new config
        await ops_test.model.wait_for_idle(
            apps=[ALERTMANAGER_APP_NAME],
            status="active",
            timeout=WAIT_FOR_STATUS_TIMEOUT,
            idle_period=5,
        )

        alertmanager_config_raw = await _get_alertmanager_config(
            ops_test, ALERTMANAGER_APP_NAME, 0
        )
        alertmanager_config = yaml.safe_load(alertmanager_config_raw)

        assert await _get_config_difs(expected_config, alertmanager_config) == {}

    @pytest.mark.abort_on_fail
    async def test_scale_up(self, ops_test: OpsTest, setup):
        await ops_test.model.applications[ALERTMANAGER_CONFIGURER_APP_NAME].scale(2)

        await ops_test.model.wait_for_idle(
            apps=[ALERTMANAGER_CONFIGURER_APP_NAME],
            status="active",
            timeout=WAIT_FOR_STATUS_TIMEOUT,
            idle_period=5,
            wait_for_exact_units=2,
        )

    @pytest.mark.xfail(reason="Bug in Juju: https://bugs.launchpad.net/juju/+bug/1977582")
    async def test_scale_down(self, ops_test: OpsTest, setup):
        await ops_test.model.applications[ALERTMANAGER_CONFIGURER_APP_NAME].scale(1)

        await ops_test.model.wait_for_idle(
            apps=[ALERTMANAGER_CONFIGURER_APP_NAME],
            status="active",
            timeout=60,
            wait_for_exact_units=1,
        )

    @staticmethod
    async def _deploy_alertmanager_k8s(ops_test: OpsTest):
        await ops_test.model.deploy(
            ALERTMANAGER_APP_NAME,
            application_name=ALERTMANAGER_APP_NAME,
            channel="stable",
            trust=True,
            series="focal",
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


async def _add_juju_topology_to_group_by(config: dict) -> dict:
    route = cast(dict, config.get("route", {}))
    route["group_by"] = list(
        set(route.get("group_by", [])).union(["juju_application", "juju_model", "juju_model_uuid"])
    )
    config["route"] = route
    return config


async def _add_new_receiver(config: dict, receiver_json: dict) -> dict:
    receiver = deepcopy(receiver_json)
    receivers = config.get("receivers")
    new_receiver = await _update_receiver_name_with_tenant_id(receiver)
    new_receiver = await _add_default_webhook_configs(new_receiver)
    receivers.append(new_receiver)
    config["receivers"] = receivers
    return config


async def _update_receiver_name_with_tenant_id(receiver: dict) -> dict:
    receiver_name = receiver["name"]
    new_name = f"{TEST_TENANT}_{receiver_name}"
    receiver["name"] = new_name
    return receiver


async def _add_default_webhook_configs(receiver: dict) -> dict:
    default_webhook_configs = {
        "send_resolved": False,
        "http_config": {"follow_redirects": True},
        "max_alerts": 0,
    }
    webhook_configs = receiver["webhook_configs"]
    webhook_configs[0].update(default_webhook_configs)
    receiver["webhook_configs"] = webhook_configs
    return receiver


async def _get_config_difs(expected_config: dict, actual_config: dict) -> dict:
    difs = {}
    difs.update(
        DeepDiff(actual_config["receivers"], expected_config["receivers"], ignore_order=True)
    )
    difs.update(
        DeepDiff(
            actual_config["route"]["receiver"],
            expected_config["route"]["receiver"],
            ignore_order=True,
        )
    )
    difs.update(
        DeepDiff(
            actual_config["route"]["group_by"],
            expected_config["route"]["group_by"],
            ignore_order=True,
        )
    )
    difs.update(
        DeepDiff(actual_config["route"]["group_wait"], expected_config["route"]["group_wait"])
    )
    difs.update(
        DeepDiff(
            actual_config["route"]["group_interval"], expected_config["route"]["group_interval"]
        )
    )
    difs.update(
        DeepDiff(
            actual_config["route"]["repeat_interval"], expected_config["route"]["repeat_interval"]
        )
    )
    return difs
