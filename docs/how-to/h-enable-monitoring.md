# How to enable monitoring

[note type="positive"]
**Warning:** The feature is currently being developed and will be available in the `14/stable` channel soon.
Please contact Canonical Data team to test it from your side as experimental feature.
[/note]

Creating and listing backups requires that you:
* [Have a Charmed Postgresql deployed](/t/charmed-postgresql-tutorial-deploy-postgresql/9697?channel=14/edge)
* [Deploy `cos-lite` bundle in a Kubernetes environment](https://charmhub.io/topics/canonical-observability-stack/tutorials/install-microk8s)

Switch to COS K8s environment and offer COS interfaces to be cross-model related with Charmed Postgresql VM model:
```shell
# Switch to Kubernetes controller, for the cos model.
juju switch <k8s_controller>:<cos_model_name>

juju offer grafana.grafana-dashboards grafana-dashboards
juju offer loki:logging loki-logging
juju offer prometheus:receive-remote-write prometheus-receive-remote-write
```

Switch to Charmed Postgresql VM model, find offers and relate with them:
```shell
# We are on the Kubernetes controller, for the cos model. Switch to postgresql model
juju switch <machine_controller_name>:<postgresql_model_name>

juju find-offers <k8s_controller>:
```

A similar output should appear, if `k8s` is the k8s controller name and `cos` the model where `cos-lite` has been deployed:
```shell
Store      URL                                        Access  Interfaces
k8s        admin/cos.grafana-dashboards               admin   grafana_dashboard:grafana-dashboard
k8s        admin/cos.loki-logging                     admin   loki_push_api:logging
k8s        admin/cos.prometheus-receive-remote-write  admin   prometheus-receive-remote-write:receive-remote-write
...
```

Consume offers to be reachable in the current model:
```shell
juju consume k8s:admin/cos.prometheus-receive-remote-write
juju consume k8s:admin/cos.loki-logging
juju consume k8s:admin/cos.grafana-dashboards
```

Now, deploy `grafana-agent` (subordinate charm) and relate it with Charmed Postgresql, later relate `grafana-agent` with consumed COS offers:
```shell
juju deploy grafana-agent
juju relate postgresql:cos-agent grafana-agent
juju relate grafana-agent grafana-dashboards
juju relate grafana-agent loki-logging
juju relate grafana-agent prometheus-receive-remote-write
```

After this is complete, Grafana will show the new dashboards: `Postgresql Exporter` and allows access for Charmed Postgresql logs on Loki.

The example of `juju status` on Charmed Postgresql VM model:
```shell
ubuntu@localhost:~$ juju status
Model      Controller  Cloud/Region         Version  SLA          Timestamp
vmmodel    local       localhost/localhost  2.9.42   unsupported  00:12:18+02:00

SAAS                             Status  Store    URL
grafana-dashboards               active  k8s      admin/cos.grafana-dashboards
loki-logging                     active  k8s      admin/cos.loki-logging
prometheus-receive-remote-write  active  k8s      admin/cos.prometheus-receive-remote-write

App                   Version      Status  Scale  Charm               Channel   Rev  Exposed  Message
grafana-agent                      active      1  grafana-agent       edge        5  no
postgresql              14.7       active      1  postgresql          14/edge   296  no       Primary

Unit                          Workload  Agent  Machine  Public address  Ports               Message
postgresql/3*                 active    idle   4        10.85.186.140                       Primary
  grafana-agent/0*            active    idle            10.85.186.140

Machine  State    Address        Inst id        Series  AZ  Message
4        started  10.85.186.140  juju-fcde9e-4  jammy       Running
```

The example of `juju status` on COS K8s model:
```shell
ubuntu@localhost:~$ juju status
Model  Controller   Cloud/Region        Version  SLA          Timestamp
cos    k8s          microk8s/localhost  2.9.42   unsupported  00:15:31+02:00

App           Version  Status  Scale  Charm             Channel  Rev  Address         Exposed  Message
alertmanager  0.23.0   active      1  alertmanager-k8s  stable    47  10.152.183.206  no
catalogue              active      1  catalogue-k8s     stable    13  10.152.183.183  no
grafana       9.2.1    active      1  grafana-k8s       stable    64  10.152.183.140  no
loki          2.4.1    active      1  loki-k8s          stable    60  10.152.183.241  no
prometheus    2.33.5   active      1  prometheus-k8s    stable   103  10.152.183.240  no
traefik       2.9.6    active      1  traefik-k8s       stable   110  10.76.203.178   no

Unit             Workload  Agent  Address      Ports  Message
alertmanager/0*  active    idle   10.1.84.125
catalogue/0*     active    idle   10.1.84.127
grafana/0*       active    idle   10.1.84.83
loki/0*          active    idle   10.1.84.79
prometheus/0*    active    idle   10.1.84.96
traefik/0*       active    idle   10.1.84.119

Offer                            Application  Charm           Rev  Connected  Endpoint              Interface                Role
grafana-dashboards               grafana      grafana-k8s     64   1/1        grafana-dashboard     grafana_dashboard        requirer
loki-logging                     loki         loki-k8s        60   1/1        logging               loki_push_api            provider
prometheus-receive-remote-write  prometheus   prometheus-k8s  103  1/1        receive-remote-write  prometheus_remote_write  provider
```