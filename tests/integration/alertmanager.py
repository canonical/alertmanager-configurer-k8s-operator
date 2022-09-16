#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import requests


class Alertmanager:
    def __init__(self, host="localhost", port=9093):
        """Utility to manage a Prometheus application.

        Args:
            host: Optional; host address of Alertmanager application.
            port: Optional; port on which Alertmanager service is exposed.
        """
        self.base_url = f"http://{host}:{port}"

        # Set a timeout of 5 second - should be sufficient for all the checks here.
        # The default (5 min) prolongs itests unnecessarily.
        self.timeout = 5

    async def is_ready(self) -> bool:
        """Send a GET request to check readiness.

        Returns:
          True if Alertmanager is ready (returned 200 OK); False otherwise.
        """
        url = f"{self.base_url}/-/ready"

        async with requests.get(url) as response:
            return response.status_code == 200

    async def config(self) -> str:
        """Send a GET request to get Alertmanager configuration.

        Returns:
          str: YAML config in string format or empty string
        """
        url = f"{self.base_url}/api/v2/status"
        # Response looks like this:
        # {
        #   "cluster": {
        #     "peers": [],
        #     "status": "disabled"
        #   },
        #   "config": {
        #     "original": "global:\n  resolve_timeout: 5m\n  http_config:\n    tls_config:\n      insecure_skip_verify: true\n    follow_redirects: true\n  smtp_hello: localhost\n  smtp_require_tls: true\n  pagerduty_url: https://events.pagerduty.com/v2/enqueue\n  opsgenie_api_url: https://api.opsgenie.com/\n  wechat_api_url: https://qyapi.weixin.qq.com/cgi-bin/\n  victorops_api_url: https://alert.victorops.com/integrations/generic/20131114/alert/\nroute:\n  receiver: dummy\n  group_by:\n  - juju_model\n  - juju_application\n  - juju_model_uuid\n  continue: false\n  group_wait: 30s\n  group_interval: 5m\n  repeat_interval: 1h\nreceivers:\n- name: dummy\n  webhook_configs:\n  - send_resolved: true\n    http_config:\n      tls_config:\n        insecure_skip_verify: true\n      follow_redirects: true\n    url: http://127.0.0.1:5001/\n    max_alerts: 0\ntemplates: []\n"  # noqa: E501, W505
        #   },
        #   "uptime": "2022-08-19T10:12:20.523Z",
        #   "versionInfo": {
        #     "branch": "HEAD",
        #     "buildDate": "20210916-15:51:04",
        #     "buildUser": "root@whatever",
        #     "goVersion": "go1.14.15",
        #     "revision": "61046b17771a57cfd4c4a51be370ab930a4d7d54",
        #     "version": "0.23.0"
        #   }
        # }
        response = requests.get(url)
        result = response.json()
        return result["config"]["original"] if response.status_code == 200 else ""
