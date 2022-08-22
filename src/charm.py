#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import os
from pathlib import Path
from typing import Union

import charms.alertmanager_k8s.v0.alertmanager_remote_configurer as remote_config_write
import yaml
from charms.observability_libs.v1.kubernetes_service_patch import (
    KubernetesServicePatch,
    ServicePort,
)
from ops.charm import (
    CharmBase,
    ConfigChangedEvent,
    PebbleReadyEvent,
    RelationJoinedEvent,
)
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    WaitingStatus,
)
from ops.pebble import Layer

from config_dir_watcher import (
    AlertmanagerConfigDirWatcher,
    AlertmanagerConfigFileChangedCharmEvents,
    AlertmanagerConfigFileChangedEvent,
)

logger = logging.getLogger(__name__)


class AlertmanagerConfigurerOperatorCharm(CharmBase):
    ALERTMANAGER_CONFIG_DIR = "/etc/alertmanager/"
    ALERTMANAGER_CONFIG_FILE = os.path.join(ALERTMANAGER_CONFIG_DIR, "alertmanager.yml")
    DUMMY_HTTP_SERVER_SERVICE_NAME = "dummy-http-server"
    DUMMY_HTTP_SERVER_HOST = "localhost"
    DUMMY_HTTP_SERVER_PORT = 80
    ALERTMANAGER_CONFIGURER_SERVICE_NAME = "alertmanager-configurer"
    ALERTMANAGER_CONFIGURER_PORT = 9101
    ALERTMANAGER_DEFAULT_CONFIG = """route:
  receiver: null_receiver
  group_by:
  - alertname
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
receivers:
- name: null_receiver
    """

    on = AlertmanagerConfigFileChangedCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)
        self._alertmanager_configurer_container_name = (
            self._alertmanager_configurer_layer_name
        ) = self._alertmanager_configurer_service_name = self.ALERTMANAGER_CONFIGURER_SERVICE_NAME
        self._dummy_http_server_container_name = (
            self._dummy_http_server_layer_name
        ) = self._dummy_http_server_service_name = self.DUMMY_HTTP_SERVER_SERVICE_NAME
        self._alertmanager_configurer_container = self.unit.get_container(
            self._alertmanager_configurer_container_name
        )
        self._dummy_http_server_container = self.unit.get_container(
            self._dummy_http_server_container_name
        )

        self.service_patch = KubernetesServicePatch(
            charm=self,
            ports=[
                ServicePort(name="alertmanager-config", port=self.ALERTMANAGER_CONFIGURER_PORT),
                ServicePort(name="dummy-http-server", port=self.DUMMY_HTTP_SERVER_PORT),
            ],
        )

        self.framework.observe(
            self.on.alertmanager_configurer_pebble_ready,
            self._on_alertmanager_configurer_pebble_ready,
        )
        self.framework.observe(
            self.on.dummy_http_server_pebble_ready, self._on_dummy_http_server_pebble_ready
        )
        self.framework.observe(
            self.on.alertmanager_relation_joined, self._on_alertmanager_relation_joined
        )
        self.framework.observe(self.on.config_changed, self._on_alertmanager_config_changed)
        self.framework.observe(
            self.on.alertmanager_config_file_changed, self._on_alertmanager_config_changed
        )
        self.framework.observe(
            self.on.alertmanager_configurer_relation_joined,
            self._on_alertmanager_configurer_relation_joined,
        )

    def _on_alertmanager_configurer_pebble_ready(self, event: PebbleReadyEvent) -> None:
        """Checks whether all conditions to start Alertmanager Configurer are met and, if yes,
        triggers start of the alertmanager-configurer service.

        Args:
            event: Juju PebbleReadyEvent event

        Returns:
            None
        """
        watchdog = AlertmanagerConfigDirWatcher(self, self.ALERTMANAGER_CONFIG_DIR)
        watchdog.start_watchdog()
        if not self.model.get_relation("alertmanager"):
            self.unit.status = BlockedStatus("Waiting for alertmanager relation to be created")
            event.defer()
            return
        if not self._alertmanager_configurer_container.can_connect():
            self.unit.status = WaitingStatus(
                f"Waiting for {self._alertmanager_configurer_container_name} container to be ready"
            )
            event.defer()
            return
        if not self._dummy_http_server_running:
            self.unit.status = WaitingStatus("Waiting for the dummy HTTP server to be ready")
            event.defer()
            return
        self._start_alertmanager_configurer()
        self.unit.status = ActiveStatus()

    def _on_dummy_http_server_pebble_ready(self, event: PebbleReadyEvent) -> None:
        """When dummy HTTP server Pebble is ready and the container is accessible, starts the
        dummy HTTP server.

        Args:
            event: Juju PebbleReadyEvent event

        Returns:
             None
        """
        if self._dummy_http_server_container.can_connect():
            self._start_dummy_http_server()
        else:
            self.unit.status = WaitingStatus(
                f"Waiting for {self._dummy_http_server_container_name} container to be ready"
            )
            event.defer()

    def _start_alertmanager_configurer(self) -> None:
        """Starts Alertmanager Configurer service.

        Returns:
            None
        """
        plan = self._alertmanager_configurer_container.get_plan()
        layer = self._alertmanager_configurer_layer
        if plan.services != layer.services:
            self.unit.status = MaintenanceStatus(
                f"Configuring pebble layer for {self._alertmanager_configurer_service_name}"
            )
            self._alertmanager_configurer_container.add_layer(
                self._alertmanager_configurer_container_name, layer, combine=True
            )
            self._alertmanager_configurer_container.restart(
                self._alertmanager_configurer_container_name
            )
            logger.info(f"Restarted container {self._alertmanager_configurer_service_name}")

    def _start_dummy_http_server(self) -> None:
        """Starts dummy HTTP server service.

        Returns:
            None
        """
        plan = self._dummy_http_server_container.get_plan()
        layer = self._dummy_http_server_layer
        if plan.services != layer.services:
            self.unit.status = MaintenanceStatus(
                f"Configuring pebble layer for {self._dummy_http_server_service_name}"
            )
            self._dummy_http_server_container.add_layer(
                self._dummy_http_server_container_name, layer, combine=True
            )
            self._dummy_http_server_container.restart(self._dummy_http_server_service_name)
            logger.info(f"Restarted container {self._dummy_http_server_service_name}")

    def _on_alertmanager_config_changed(
        self, event: Union[ConfigChangedEvent, AlertmanagerConfigFileChangedEvent]
    ) -> None:
        """Pushes configuration to Alertmanager through the relation data bag.

        Args:
            event: Juju ConfigChangedEvent or AlertmanagerConfigFileChangedEvent event

        Returns:
            None
        """
        if not self.model.unit.is_leader():
            return
        if not self.model.get_relation("alertmanager"):
            self.unit.status = BlockedStatus("Waiting for alertmanager relation to be created")
            event.defer()
            return
        if not self._alertmanager_config_file_exists:
            self.unit.status = WaitingStatus(
                "Waiting for Alertmanager config file to be available"
            )
            event.defer()
            return
        config = remote_config_write.load_config_file(self.ALERTMANAGER_CONFIG_FILE)
        templates = self._get_templates(config)
        self._update_alertmanager_relation_data_bag_with_new_alertmanager_config(config, templates)

    def _get_templates(self, config: dict) -> list:
        """Prepares templates data to be put in a relation data bag.
        If the main config file contains templates section, content of the files specified in this
        section will be concatenated. At the same time, templates section will be removed from
        the main config, as alertmanager-k8s-operator charm doesn't tolerate it.
        It is also possible to configure templates through the charm's templates_file config
        option.
        In case templates are specified in both the main config file and the charm's config, config
        file will take precedence.

        Args:
            config: Alertmanager config

        Returns:
            list: List of templates
        """
        templates = []
        if config.get("templates", []):
            for file in config.pop("templates"):
                try:
                    templates.append(remote_config_write.load_templates_file(file))
                except FileNotFoundError:
                    continue
        elif self.config["templates_file"]:
            templates.append(self.config["templates_file"])
        return templates

    def _update_alertmanager_relation_data_bag_with_new_alertmanager_config(
        self,
        config: dict,
        templates: list,
    ) -> None:
        """Updates Alertmanager config and templates inside the relation data bag.

        Args:
            config: Alertmanager config
            templates: Alertmanager templates

        Returns:
            None
        """
        alertmanager_relation = self.model.get_relation("alertmanager")
        alertmanager_relation.data[self.app]["alertmanager_config"] = json.dumps(config)  # type: ignore[union-attr]  # noqa: E501
        alertmanager_relation.data[self.app]["alertmanager_templates"] = json.dumps(templates)  # type: ignore[union-attr]  # noqa: E501

    def _on_alertmanager_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handles actions taken when Alertmanager relation joins.

        Returns:
            None
        """
        self._get_default_alertmanager_config(event)

    def _get_default_alertmanager_config(self, event: RelationJoinedEvent) -> None:
        """Pushes default Alertmanager config file to the workload container. If the provider
        of the `alertmanager` relation doesn't provide Alertmanager config in the relation data
        bag, default config will be used.

        Args:
            event: Juju RelationJoinedEvent event

        Returns:
            None
        """
        default_alertmanager_config = self._default_config
        try:
            default_alertmanager_config = event.relation.data[event.relation.app][
                "alertmanager_config"
            ]
        except (AttributeError, KeyError):
            logger.warning("Alertmanager config missing from relation data - using default.")
        self._alertmanager_configurer_container.push(
            self.ALERTMANAGER_CONFIG_FILE,
            yaml.safe_load(default_alertmanager_config),
        )

    def _on_alertmanager_configurer_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handles actions taken when Alertmanager Configurer relation joins.

        Returns:
            None
        """
        self._add_service_info_to_relation_data_bag(event)

    def _add_service_info_to_relation_data_bag(self, event: RelationJoinedEvent) -> None:
        """Adds information about Alertmanager Configurer service name and port to relation data
        bag.

        Returns:
            None
        """
        alertmanager_configurer_relation = event.relation
        alertmanager_configurer_relation.data[self.app][
            "service_name"
        ] = self.ALERTMANAGER_CONFIGURER_SERVICE_NAME
        alertmanager_configurer_relation.data[self.app]["port"] = str(
            self.ALERTMANAGER_CONFIGURER_PORT
        )

    @property
    def _alertmanager_configurer_layer(self) -> Layer:
        """Constructs the pebble layer for Alertmanager configurer.

        Returns:
            Layer: a Pebble layer specification for the Alertmanager configurer workload container.
        """
        return Layer(
            {
                "summary": "Alertmanager Configurer layer",
                "description": "Pebble config layer for Alertmanager Configurer",
                "services": {
                    self._alertmanager_configurer_service_name: {
                        "override": "replace",
                        "startup": "enabled",
                        "command": f"alertmanager_configurer "
                        f"-port={str(self.ALERTMANAGER_CONFIGURER_PORT)} "
                        f"-alertmanager-conf={self.ALERTMANAGER_CONFIG_FILE} "
                        "-alertmanagerURL="
                        f"{self.DUMMY_HTTP_SERVER_HOST}:{self.DUMMY_HTTP_SERVER_PORT} "
                        f'-multitenant-label={self.model.config.get("multitenant_label")} '
                        "-delete-route-with-receiver=true ",
                    }
                },
            }
        )

    @property
    def _dummy_http_server_layer(self) -> Layer:
        """Constructs the pebble layer for the dummy HTTP server.

        Returns:
            Layer: a Pebble layer specification for the dummy HTTP server workload container.
        """
        return Layer(
            {
                "summary": "Dummy HTTP server pebble layer",
                "description": "Pebble layer configuration for the dummy HTTP server",
                "services": {
                    self._dummy_http_server_service_name: {
                        "override": "replace",
                        "startup": "enabled",
                        "command": "nginx",
                    }
                },
            }
        )

    @property
    def _dummy_http_server_running(self) -> bool:
        """Checks the dummy HTTP server is running or not.

        Returns:
            bool: True/False.
        """
        try:
            self._dummy_http_server_container.get_service(self._dummy_http_server_service_name)
            return True
        except ModelError:
            return False

    @property
    def _alertmanager_config_file_exists(self) -> bool:
        """Checks whether Alertmanager's config file exists.

        Returns:
            bool: True/False
        """
        return Path(self.ALERTMANAGER_CONFIG_FILE).is_file()

    @property
    def _default_config(self) -> str:
        """Provides default alertmanager.yml content in case it's not passed from the Alertmanager.

        Returns:
            str: default Alertmanager config
        """
        return self.ALERTMANAGER_DEFAULT_CONFIG


if __name__ == "__main__":
    main(AlertmanagerConfigurerOperatorCharm)
