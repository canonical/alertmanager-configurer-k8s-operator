#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import unittest
from unittest.mock import Mock, PropertyMock, call, patch

import yaml
from ops import testing
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus

from charm import AlertmanagerConfigurerOperatorCharm

testing.SIMULATE_CAN_CONNECT = True

TEST_MULTITENANT_LABEL = "some_test_label"
TEST_CONFIG = f"""options:
  multitenant_label:
    type: string
    description: |
      Alertmanager Configurer has been designed to support multiple tenants. In a multitenant
      Alertmanager Configurer setup, each alert is first routed on the tenancy label, and then
      the routing tree is distinct for each tenant.
    default: {TEST_MULTITENANT_LABEL}
"""
with open("./tests/unit/test_config/alertmanager_default.yml", "r") as default_yaml:
    TEST_ALERTMANAGER_DEFAULT_CONFIG = default_yaml.read()
TEST_ALERTMANAGER_CONFIG_FILE = "/test/rules/dir/config_file.yml"


class TestAlertmanagerConfigurerOperatorCharm(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda charm, ports: None)
    def setUp(self):
        self.harness = testing.Harness(AlertmanagerConfigurerOperatorCharm, config=TEST_CONFIG)
        self.addCleanup(self.harness.cleanup)
        self.harness.set_leader(True)
        self.harness.begin()

    @patch("charm.AlertmanagerConfigDirWatcher")
    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_CONFIG_DIR",
        new_callable=PropertyMock,
    )
    @patch("ops.model.Container.push", Mock())
    def test_given_alertmanager_config_directory_when_start_then_watchdog_starts_watching_given_alertmanager_config_directory(  # noqa: E501
        self, patched_config_dir, patched_alertmanager_config_dir_watcher
    ):
        test_config_dir = "/test/rules/dir"
        patched_config_dir.return_value = test_config_dir
        self.harness.charm.on.start.emit()

        patched_alertmanager_config_dir_watcher.assert_called_with(
            self.harness.charm, test_config_dir
        )

    @patch("charm.AlertmanagerConfigDirWatcher", Mock())
    def test_given_alertmanager_relation_not_created_when_pebble_ready_then_charm_goes_to_blocked_state(  # noqa: E501
        self,
    ):
        self.harness.container_pebble_ready("alertmanager-configurer")

        assert self.harness.charm.unit.status == BlockedStatus(
            "Waiting for alertmanager relation to be created"
        )

    @patch("charm.AlertmanagerConfigDirWatcher", Mock())
    def test_given_alertmanager_relation_created_but_alertmanager_configurer_container_not_yet_ready_when_pebble_ready_then_charm_goes_to_waiting_state(  # noqa: E501
        self,
    ):
        self.harness.add_relation("alertmanager", "alertmanager-k8s")
        testing.SIMULATE_CAN_CONNECT = False
        self.harness.container_pebble_ready("alertmanager-configurer")

        assert self.harness.charm.unit.status == WaitingStatus(
            "Waiting for alertmanager-configurer container to be ready"
        )

    @patch("charm.AlertmanagerConfigDirWatcher", Mock())
    def test_given_alertmanager_relation_created_and_alertmanager_configurer_container_ready_but_dummy_http_server_not_yet_ready_when_pebble_ready_then_charm_goes_to_waiting_state(  # noqa: E501
        self,
    ):
        self.harness.add_relation("alertmanager", "alertmanager-k8s")
        self.harness.set_can_connect("dummy-http-server", True)
        self.harness.container_pebble_ready("alertmanager-configurer")

        assert self.harness.charm.unit.status == WaitingStatus(
            "Waiting for the dummy HTTP server to be ready"
        )

    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_CONFIG_FILE",
        new_callable=PropertyMock,
    )
    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_CONFIGURER_PORT",
        new_callable=PropertyMock,
    )
    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.DUMMY_HTTP_SERVER_HOST",
        new_callable=PropertyMock,
    )
    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.DUMMY_HTTP_SERVER_PORT",
        new_callable=PropertyMock,
    )
    @patch("charm.AlertmanagerConfigDirWatcher", Mock())
    def test_given_prometheus_relation_created_and_prometheus_configurer_container_ready_when_pebble_ready_then_pebble_plan_is_updated_with_correct_pebble_layer(  # noqa: E501
        self,
        patched_dummy_http_server_port,
        patched_dummy_http_server_host,
        patched_alertmanager_configurer_port,
        patched_alertmanager_config_file,
    ):
        test_dummy_http_server_port = 4321
        test_dummy_http_server_host = "testhost"
        test_alertmanager_configurer_port = 1234
        patched_dummy_http_server_port.return_value = test_dummy_http_server_port
        patched_dummy_http_server_host.return_value = test_dummy_http_server_host
        patched_alertmanager_configurer_port.return_value = test_alertmanager_configurer_port
        patched_alertmanager_config_file.return_value = TEST_ALERTMANAGER_CONFIG_FILE
        self.harness.add_relation("alertmanager", "alertmanager-k8s")
        self.harness.container_pebble_ready("dummy-http-server")
        expected_plan = {
            "services": {
                "alertmanager-configurer": {
                    "override": "replace",
                    "startup": "enabled",
                    "command": f"alertmanager_configurer "
                    f"-port={test_alertmanager_configurer_port} "
                    f"-alertmanager-conf={TEST_ALERTMANAGER_CONFIG_FILE} "
                    "-alertmanagerURL="
                    f"{test_dummy_http_server_host}:{test_dummy_http_server_port} "
                    f"-multitenant-label={TEST_MULTITENANT_LABEL} "
                    "-delete-route-with-receiver=true ",
                }
            }
        }

        self.harness.container_pebble_ready("alertmanager-configurer")

        updated_plan = self.harness.get_container_pebble_plan("alertmanager-configurer").to_dict()
        self.assertEqual(expected_plan, updated_plan)

    def test_given_dummy_http_server_container_ready_when_pebble_ready_then_pebble_plan_is_updated_with_correct_pebble_layer(  # noqa: E501
        self,
    ):
        expected_plan = {
            "services": {
                "dummy-http-server": {
                    "override": "replace",
                    "startup": "enabled",
                    "command": "nginx",
                }
            }
        }
        self.harness.container_pebble_ready("dummy-http-server")

        updated_plan = self.harness.get_container_pebble_plan("dummy-http-server").to_dict()
        self.assertEqual(expected_plan, updated_plan)

    @patch("charm.AlertmanagerConfigDirWatcher", Mock())
    def test_given_alertmanager_relation_created_and_alertmanager_configurer_container_ready_when_pebble_ready_then_charm_goes_to_active_state(  # noqa: E501
        self,
    ):
        self.harness.add_relation("alertmanager", "alertmanager-k8s")
        self.harness.set_can_connect("dummy-http-server", True)
        self.harness.container_pebble_ready("dummy-http-server")

        self.harness.container_pebble_ready("alertmanager-configurer")

        assert self.harness.charm.unit.status == ActiveStatus()

    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_CONFIGURER_SERVICE_NAME",
        new_callable=PropertyMock,
    )
    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_CONFIGURER_PORT",
        new_callable=PropertyMock,
    )
    def test_given_alertmanager_configurer_service_when_alertmanager_configurer_relation_joined_then_alertmanager_configurer_service_name_and_port_are_pushed_to_the_relation_data_bag(  # noqa: E501
        self, patched_alertmanager_configurer_port, patched_alertmanager_configurer_service_name
    ):
        test_alertmanager_configurer_service_name = "whatever"
        test_alertmanager_configurer_port = 1234
        patched_alertmanager_configurer_service_name.return_value = (
            test_alertmanager_configurer_service_name
        )
        patched_alertmanager_configurer_port.return_value = test_alertmanager_configurer_port
        relation_id = self.harness.add_relation(
            "alertmanager-configurer", self.harness.charm.app.name
        )
        self.harness.add_relation_unit(relation_id, f"{self.harness.charm.app.name}/0")

        self.assertEqual(
            self.harness.get_relation_data(relation_id, f"{self.harness.charm.app.name}"),
            {
                "service_name": test_alertmanager_configurer_service_name,
                "port": str(test_alertmanager_configurer_port),
            },
        )

    @patch("ops.model.Container.push")
    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_DEFAULT_CONFIG",
        new_callable=PropertyMock,
    )
    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_CONFIG_FILE",
        new_callable=PropertyMock,
    )
    @patch("charm.AlertmanagerConfigDirWatcher", Mock())
    def test_given_alertmanager_default_config_when_start_then_alertmanager_config_is_created_using_default_data(  # noqa: E501
        self, patched_alertmanager_config_file, patched_alertmanager_default_config, patched_push
    ):
        patched_alertmanager_config_file.return_value = TEST_ALERTMANAGER_CONFIG_FILE
        patched_alertmanager_default_config.return_value = TEST_ALERTMANAGER_DEFAULT_CONFIG
        expected_config_push_calls = [
            call(TEST_ALERTMANAGER_CONFIG_FILE, TEST_ALERTMANAGER_DEFAULT_CONFIG)
        ]

        self.harness.charm.on.start.emit()

        patched_push.assert_has_calls(expected_config_push_calls)

    @patch(
        "charm.AlertmanagerConfigurerOperatorCharm.ALERTMANAGER_CONFIG_FILE",
        new_callable=PropertyMock,
    )
    @patch("charm.KubernetesServicePatch", lambda charm, ports: None)
    def test_given_alertmanager_config_in_config_dir_when_alertmanager_config_file_changed_then_data_bag_is_updated_with_new_config(  # noqa: E501
        self, patched_alertmanager_config_file
    ):
        test_config_file = "./tests/unit/test_config/alertmanager.yml"
        patched_alertmanager_config_file.return_value = test_config_file
        harness = testing.Harness(AlertmanagerConfigurerOperatorCharm, config=TEST_CONFIG)
        self.addCleanup(harness.cleanup)
        harness.set_leader(True)
        harness.begin()
        with open(test_config_file, "r") as config_yaml:
            expected_config = yaml.safe_load(config_yaml)
        relation_id = harness.add_relation("alertmanager", "alertmanager-k8s")
        harness.add_relation_unit(relation_id, "alertmanager-k8s/0")

        harness.charm.on.alertmanager_config_file_changed.emit()

        self.assertEqual(
            harness.get_relation_data(relation_id, "alertmanager-configurer-k8s")[
                "alertmanager_config"
            ],
            json.dumps(expected_config),
        )
