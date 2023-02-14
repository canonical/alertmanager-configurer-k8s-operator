# Alertmanager Configurer Charmed Operator

## Description

The Alertmanager Configurer Charmed Operator provides an HTTP-based API for managing
[Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/) configuration.

This charm has been designed to supplement the
[alertmanager-k8s] charm. It leverages the `alertmanager_remote_configuration` interface, provided 
by the [alertmanager-k8s], to send the configuration over to the Alertmanager inside the
[Juju](https://juju.is/) relation data bag.

Full description of the API is available in [github].

[alertmanager-k8s]: https://github.com/canonical/alertmanager-k8s-operator
[github]: https://github.com/facebookarchive/prometheus-configmanager/blob/main/alertmanager/docs/swagger-v1.yml

## Usage

### Deployment

> **NOTE**: This charm is only compatible with Juju 3.x!

The Alertmanager Configurer Charmed Operator may be deployed using the Juju command line as in:

```bash
juju deploy alertmanager-configurer-k8s --trust
```

### Relating to the Alertmanager

```bash
juju deploy alertmanager-k8s --channel=edge --trust
juju relate alertmanager-configurer-k8s:alertmanager alertmanager-k8s:remote-configuration
```

### Configuring Alertmanager via alertmanager-configurer

Alertmanager Configurer exposes an HTTP API which allows managing Alertmanager's configuration.
The API is available at port 9101 on the IP address of the charm unit. This unit and its IP address
may be determined using the `juju status` command.<br>
Full description of Alertmanager Configurer's API is available in
[github](https://github.com/facebookarchive/prometheus-configmanager/blob/main/alertmanager/docs/swagger-v1.yml).

Alertmanager Configurer has been designed to support multiple tenants. In a multitenant
Alertmanager Configurer setup, each alert is first routed on the tenancy label, and then
the routing tree is distinct for each tenant.

### Examples:

Get tenants:

```bash
curl -X GET http://<ALERTMANAGER CONFIGURER CHARM UNIT IP>:9101/v1/tenants
```

Create Alertmanager's global config:

```yaml
global:
  resolve_timeout: 5m
  http_config:
    tls_config:
      insecure_skip_verify: true
```

```bash
curl -X POST http://<ALERTMANAGER CONFIGURER CHARM UNIT IP>:9101/v1/global
  -H 'Content-Type: application/json'
  -d '{"resolve_timeout": "5m", "http_config": {"tls_config": {"insecure_skip_verify": true}}}'
```

Get Alertmanager's global config:

```bash
curl -X GET http://<ALERTMANAGER CONFIGURER CHARM UNIT IP>:9101/v1/global
```

Create receiver:

```yaml
receivers:
- name: <TENANT_ID>_example
  webhook_configs:
  - send_resolved: false
    url: http://receiver_example.com
```

```bash
curl -X POST http://<ALERTMANAGER CONFIGURER CHARM UNIT IP>:9101/v1/<TENANT_ID>/receiver
  -H 'Content-Type: application/json'
  -d '{"name": "example", "webhook_configs": [{"url": "http://receiver_example.com"}]}'
```

Delete receiver:

```bash
curl -X DELETE http://<ALERTMANAGER CONFIGURER CHARM UNIT IP>:9101/v1/<TENANT_ID>/receiver/<RECEIVER_NAME>
```

## OCI Images

- [facebookincubator/alertmanager-configurer](https://hub.docker.com/r/facebookincubator/alertmanager-configurer)
- [canonical/200-ok](https://github.com/canonical/200-ok/pkgs/container/200-ok)
