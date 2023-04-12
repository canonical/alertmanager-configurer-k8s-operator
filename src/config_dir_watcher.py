#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Alertmanager configuration dir watcher module.

This module implements custom Juju event (alertmanager_config_changed) fired upon any change
in a given directory mounted to the workload container. It is based on `watchdog`.
In this particular case, it is used by the alertmanager-configurer-k8s-operator charm to detect
changes of the Alertmanager's configuration. Thanks to this mechanism, Alertmanager Configurer
knows when to update the configuration of the Alertmanager.
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from ops.charm import CharmBase, CharmEvents
from ops.framework import EventBase, EventSource, Object
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class AlertmanagerConfigFileChangedEvent(EventBase):
    """Event emitted when Alertmanager configuration file changes."""

    pass


class AlertmanagerConfigFileChangedCharmEvents(CharmEvents):
    """Event descriptor for events emitted when Alertmanager config file changes."""

    alertmanager_config_file_changed = EventSource(AlertmanagerConfigFileChangedEvent)


LOG_FILE_PATH = "/var/log/alertmanager-configurer-watchdog.log"


class AlertmanagerConfigDirWatcher(Object):
    """Alertmanager Config Dir Watcher."""

    def __init__(self, charm: CharmBase, config_dir: str):
        super().__init__(charm, None)
        self._charm = charm
        self._config_dir = config_dir

    def start_watchdog(self):
        """Wraps watchdog in a new background process."""
        logger.info("Starting alert rules watchdog.")

        # We need to trick Juju into thinking that we are not running
        # in a hook context, as Juju will disallow use of juju-run.
        new_env = os.environ.copy()
        if "JUJU_CONTEXT_ID" in new_env:
            new_env.pop("JUJU_CONTEXT_ID")

        juju_bin = (
            "/usr/bin/juju-exec" if Path("/usr/bin/juju-exec").exists() else "/usr/bin/juju-run"
        )
        pid = subprocess.Popen(
            args=[
                "/usr/bin/python3",
                "src/config_dir_watcher.py",
                self._config_dir,
                juju_bin,
                self._charm.unit.name,
                self._charm.charm_dir,
            ],
            stdout=open(LOG_FILE_PATH, "a"),
            stderr=subprocess.STDOUT,
            env=new_env,
        ).pid

        logger.info(f"Started Alertmanager's config watchdog process with PID {pid}.")


def dispatch(run_cmd: str, unit: str, charm_dir: str):
    """Fires alert_rules_changed Juju event."""
    dispatch_sub_cmd = "JUJU_DISPATCH_PATH=hooks/alertmanager_config_file_changed {}/dispatch"
    subprocess.run([run_cmd, "-u", unit, dispatch_sub_cmd.format(charm_dir)])


class Handler(FileSystemEventHandler):
    """Handler for changes in the watched directory."""

    def __init__(self, run_cmd: str, unit: str, charm_dir: str):
        self.run_cmd = run_cmd
        self.unit = unit
        self.charm_dir = charm_dir

    def on_any_event(self, event):
        """Watchdog's callback ran on any change in the watched directory."""
        dispatch(self.run_cmd, self.unit, self.charm_dir)


def main():
    """Starts watchdog."""
    config_dir, run_cmd, unit, charm_dir = sys.argv[1:]

    observer = Observer()
    event_handler = Handler(run_cmd, unit, charm_dir)
    observer.schedule(event_handler, config_dir, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(5)
    except Exception:
        observer.stop()
        logger.error("Watchdog error! Watchdog stopped!")


if __name__ == "__main__":
    main()
