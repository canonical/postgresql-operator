# Deploy Charmed PostgreSQL VM

In this section, you will deploy Charmed PostgreSQL VM, access a unit, and interact with the PostgreSQL databases that exist inside the application.

## Deploy PostgreSQL

To deploy Charmed PostgreSQL, run 
```text
juju deploy postgresql --channel=16/stable
```

Juju will now fetch Charmed PostgreSQL VM from [Charmhub](https://charmhub.io/postgresql?channel=14/stable) and deploy it to the LXD cloud. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. 

You can track the progress by running:
```text
juju status --watch 1s
```

This command is useful for checking the real-time information about the state of a charm and the machines hosting it. Check the [`juju status` documentation](https://juju.is/docs/juju/juju-status) for more information about its usage.

When the application is ready, `juju status` will show something similar to the sample output below:
```text
TODO
```

You can also watch juju logs with the [`juju debug-log`](https://juju.is/docs/juju/juju-debug-log) command.
More info on logging in the [juju logs documentation](https://juju.is/docs/olm/juju-logs).

