# How to deploy on LXD

This guide assumes you have a running Juju and LXD environment. 

For a detailed walkthrough of setting up an environment and deploying the charm on LXD, refer to the [Tutorial](/t/9707).

## Prerequisites
* Canonical LXD 5.12+
* Fulfil the general [system requirements](/t/11743)

---

[Bootstrap](https://juju.is/docs/juju/juju-bootstrap) a juju controller and create a [model](https://juju.is/docs/juju/juju-add-model) if you haven't already:
```shell
juju bootstrap localhost <controller name>
juju add-model <model name>
```

Deploy PostgreSQL:
```shell
juju deploy postgresql
```
> See the [`juju deploy` documentation](https://juju.is/docs/juju/juju-deploy) for all available options at deploy time.
> 
> See the [Configurations tab](https://charmhub.io/postgresql/configurations) for specific PostgreSQL parameters.

Sample output of `juju status --watch 1s`:
```shell
Model       Controller  Cloud/Region         Version  SLA          Timestamp
postgresql  overlord    localhost/localhost  2.9.42   unsupported  09:41:53+01:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql           active      1  postgresql  14/stable  281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
```

[note]
If you expect having several concurrent connections frequently, it is highly recommended to deploy [PgBouncer](https://charmhub.io/pgbouncer?channel=1/stable) alongside PostgreSQL. For more information, read our explanation about [Connection pooling](/t/15777).
[/note]