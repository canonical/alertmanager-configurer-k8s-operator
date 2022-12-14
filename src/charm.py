#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Alertmanager Configurer Operator Charm."""

import logging
import os
from typing import Union

import yaml
from charms.alertmanager_k8s.v0.alertmanager_remote_configuration import (
    ConfigReadError,
    RemoteConfigurationProvider,
)
from charms.observability_libs.v1.kubernetes_service_patch import (
    KubernetesServicePatch,
    ServicePort,
)
from ops.charm import CharmBase, PebbleReadyEvent, RelationJoinedEvent
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    WaitingStatus,
)
from ops.pebble import ConnectionError, Layer

from config_dir_watcher import (
    AlertmanagerConfigDirWatcher,
    AlertmanagerConfigFileChangedCharmEvents,
    AlertmanagerConfigFileChangedEvent,
)

logger = logging.getLogger(__name__)


class AlertmanagerConfigurerOperatorCharm(CharmBase):
    """Alertmanager Configurer Operator Charm."""

    ALERTMANAGER_CONFIG_DIR = "/etc/alertmanager/"
    ALERTMANAGER_CONFIG_FILE = os.path.join(ALERTMANAGER_CONFIG_DIR, "alertmanager.yml")
    DUMMY_HTTP_SERVER_SERVICE_NAME = "dummy-http-server"
    DUMMY_HTTP_SERVER_HOST = "localhost"
    DUMMY_HTTP_SERVER_PORT = 80
    ALERTMANAGER_CONFIGURER_SERVICE_NAME = "alertmanager-configurer"
    ALERTMANAGER_CONFIGURER_PORT = 9101
    with open(
        os.path.join(os.path.dirname(os.path.realpath(__file__)), "alertmanager.yml"),
        "r",
    ) as default_yaml:
        ALERTMANAGER_DEFAULT_CONFIG = default_yaml.read()

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
        self.remote_configuration_provider = RemoteConfigurationProvider(
            charm=self,
            alertmanager_config=yaml.safe_load(self.ALERTMANAGER_DEFAULT_CONFIG),
            relation_name="alertmanager",
        )

        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(
            self.on.alertmanager_configurer_pebble_ready,
            self._start_alertmanager_configurer,
        )
        self.framework.observe(
            self.on.dummy_http_server_pebble_ready, self._on_dummy_http_server_pebble_ready
        )
        self.framework.observe(
            self.on.alertmanager_configurer_relation_joined,
            self._on_alertmanager_configurer_relation_joined,
        )
        self.framework.observe(
            self.on.alertmanager_config_file_changed, self._on_alertmanager_config_changed
        )
        self.framework.observe(
            self.remote_configuration_provider.on.configuration_broken,
            self._on_configuration_broken,
        )

    def _on_start(self, event) -> None:
        """Event handler for the start event.

        Starts AlertmanagerConfigDirWatcher and pushes default Alertmanager config to the
        workload container upon unit start.
        """
        if not self._alertmanager_configurer_container.can_connect():
            self.unit.status = WaitingStatus(
                "Waiting to be able to connect to alertmanager-configurer"
            )
            event.defer()
            return
        self._push_default_config_to_workload()
        watchdog = AlertmanagerConfigDirWatcher(self, self.ALERTMANAGER_CONFIG_DIR)
        watchdog.start_watchdog()

    def _start_alertmanager_configurer(
        self, event: Union[AlertmanagerConfigFileChangedEvent, PebbleReadyEvent]
    ) -> None:
        """Event handler for AlertmanagerConfigFileChangedEvent and PebbleReadyEvent Juju events.

        Checks whether all conditions to start Alertmanager Configurer are met and, if yes,
        triggers start of the alertmanager-configurer service.

        Args:
            event (AlertmanagerConfigFileChangedEvent, PebbleReadyEvent): Juju event
        """
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
        self._start_alertmanager_configurer_service()
        self.unit.status = ActiveStatus()

    def _on_dummy_http_server_pebble_ready(self, event: PebbleReadyEvent) -> None:
        """Event handler for dummy-http-server pebble ready event.

        When dummy HTTP server Pebble is ready and the container is accessible, starts the
        dummy HTTP server.

        Args:
            event: Juju PebbleReadyEvent event
        """
        if self._dummy_http_server_container.can_connect():
            self._start_dummy_http_server()
        else:
            self.unit.status = WaitingStatus(
                f"Waiting for {self._dummy_http_server_container_name} container to be ready"
            )
            event.defer()

    def _on_alertmanager_config_changed(self, event: AlertmanagerConfigFileChangedEvent) -> None:
        """Updates relation data bag with updated Alertmanager config."""
        try:
            alertmanager_config = RemoteConfigurationProvider.load_config_file(
                self.ALERTMANAGER_CONFIG_FILE
            )
            self._start_alertmanager_configurer(event)
            self.remote_configuration_provider.update_relation_data_bag(alertmanager_config)
        except ConfigReadError:
            logger.error("Error reading Alertmanager config file.")
            self.model.unit.status = BlockedStatus("Error reading Alertmanager config file")

    def _on_configuration_broken(self, _) -> None:
        """Event handler for `configuration_broken` event.

        Puts the charm in `Blocked` status to indicate that the provided config is invalid.
        """
        self.model.unit.status = BlockedStatus("Invalid Alertmanager configuration")

    def _start_alertmanager_configurer_service(self) -> None:
        """Starts Alertmanager Configurer service."""
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
        """Starts dummy HTTP server service."""
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

    def _push_default_config_to_workload(self) -> None:
        """Pushes default Alertmanager config file to the workload container."""
        self._alertmanager_configurer_container.push(
            self.ALERTMANAGER_CONFIG_FILE, self._default_config
        )

    def _on_alertmanager_configurer_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handles actions taken when Alertmanager Configurer relation joins."""
        if not self.unit.is_leader():
            return
        self._add_service_info_to_relation_data_bag(event)

    def _add_service_info_to_relation_data_bag(self, event: RelationJoinedEvent) -> None:
        """Event handler for Alertmanager relation joined event.

        Adds information about Alertmanager Configurer service name and port to relation data
        bag.
        """
        alertmanager_configurer_relation = event.relation
        alertmanager_configurer_relation.data[self.app]["service_name"] = self.app.name
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
        except (ConnectionError, ModelError):
            return False

    @property
    def _default_config(self) -> str:
        """Provides default alertmanager.yml content in case it's not passed from the Alertmanager.

        Returns:
            str: default Alertmanager config
        """
        return self.ALERTMANAGER_DEFAULT_CONFIG


if __name__ == "__main__":
    main(AlertmanagerConfigurerOperatorCharm)
