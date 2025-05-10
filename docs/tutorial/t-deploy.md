> [Charmed PostgreSQL VM Tutorial](/t/9707) > 2. Deploy PostgreSQL

# Deploy Charmed PostgreSQL VM

In this section, you will deploy Charmed PostgreSQL VM, access a unit, and interact with the PostgreSQL databases that exist inside the application.

## Deploy PostgreSQL

To deploy Charmed PostgreSQL, run 
```shell
juju deploy postgresql --channel=14/stable
```

Juju will now fetch Charmed PostgreSQL VM from [Charmhub](https://charmhub.io/postgresql?channel=14/stable) and deploy it to the LXD cloud. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. 

You can track the progress by running:
```shell
juju status --watch 1s
```

This command is useful for checking the real-time information about the state of a charm and the machines hosting it. Check the [`juju status` documentation](https://juju.is/docs/juju/juju-status) for more information about its usage.

When the application is ready, `juju status` will show something similar to the sample output below:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  3.1.7    unsupported  09:41:53+01:00

App         Version  Status  Scale  Charm       Channel    Rev  Exposed  Message
postgresql           active      1  postgresql  14/stable  281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
```

> You can also watch juju logs with the [`juju debug-log`](https://juju.is/docs/juju/juju-debug-log) command.
More info on logging in the [juju logs documentation](https://juju.is/docs/olm/juju-logs).