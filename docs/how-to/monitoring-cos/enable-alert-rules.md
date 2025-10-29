# How to enable COS alert rules

This guide will show how to set up [Pushover](https://pushover.net/) to receive alert notifications from the COS Alert Manager with [Awesome Alert Rules](https://samber.github.io/awesome-prometheus-alerts/).

Charmed PostgreSQL VM ships a pre-configured and pre-enabled [list of Awesome Alert Rules].

<details><summary>Screenshot of alert rules in the Grafana web interface</summary>

![Alert rules Grafana web UI|690x439](alert-rules-grafana-ui.png)
</details>

For information about accessing and managing COS Alert Rules, refer to the [COS documentation](https://charmhub.io/cos-lite).

## Prerequisites
* A deployed Charmed PostgreSQL VM operator
  * See: {ref}`How to deploy <deploy>`
* A deployed [`cos-lite` bundle in a Kubernetes environment](https://charmhub.io/topics/canonical-observability-stack/tutorials/install-microk8s)
* Fully configured [COS Monitoring]

## Enable COS alerts for Pushover
The following section is an example of the [Pushover](https://pushover.net/) alerts aggregator.

The first step is to create a new account on Pushover (or use an existing one). The goal is to have the 'user key' and 'token' to authorise alerts for the Pushover application. Follow this straightforward [Pushover guide](https://support.pushover.net/i175-how-to-get-a-pushover-api-or-pushover-application-token).

Next, create a new [COS Alert Manager](https://charmhub.io/alertmanager-k8s) config (replace `user_key` and `token` with yours):
```text
cat > myalert.yaml << EOF
```
```yaml
global:
  resolve_timeout: 5m
  http_config:
    follow_redirects: true
    enable_http2: true
route:
  receiver: placeholder
  group_by:
  - juju_model_uuid
  - juju_application
  - juju_model
  continue: false
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 1h
receivers:
- name: placeholder
  pushover_configs:
    - user_key: <relace_with_your_user_key>
      token: <relace_with_your_token>
      url: http://<relace_with_grafana_public_ip>/cos-grafana/alerting/list
      title: "{{ range .Alerts }}{{ .Labels.severity }} - {{ if .Labels.juju_unit }}{{ .Labels.juju_unit }}{{ else }}{{ .Labels.juju_application }}{{ end }} in model {{ .Labels.juju_model }}: {{ .Labels.alertname }} {{ end }}"
      message: "{{ range .Alerts }} Job: {{ .Labels.job }} Instance: {{ .Labels.instance }} {{ end }}"
templates: []
EOF
```
Upload and apply newly the created alert manager config:
```
juju switch <k8s_cos_controller>:<cos_model_name>
juju config alertmanager config_file=@myalert.yaml
```

At this stage, the COS Alert Manager will start sending alert notifications to Pushover. Users can receive them on all supported [Pushover clients/apps](https://pushover.net/clients). 

The image below shows an example of the Pushover web client:

![Pushover web client|690x439](pushover-client.jpeg)

## Alert receivers

The similar way as above, COS alerts can be send to the long [list of supported receivers](https://prometheus.io/docs/alerting/latest/configuration/#receiver-integration-settings).

Do you have questions? [Contact us]!

<!-- Links -->

[Contact us]: /reference/contacts
[COS Monitoring]: /how-to/monitoring-cos/enable-monitoring
[list of Awesome Alert Rules]: /reference/alert-rules

