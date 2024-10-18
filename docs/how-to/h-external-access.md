# How to connect DB from outside of DB LAN

## External application (non-Juju)

[u]Use case[/u]: the client application is a non-Juju application outside of Juju / DB LAN.

There are many possible ways to connect the Charmed PostgreSQL database from outside of the LAN the DB cluster is located. The available options are heavily depend on the cloud/hardware/virtualization in use. One of the possible options is to use [virtual IP addresses (VIP)](https://en.wikipedia.org/wiki/Virtual_IP_address) which the charm PgBouncer provides with assist of the charm/interface `hacluster`. Please follow the [PgBouncer documentation](https://charmhub.io/pgbouncer/docs/h-external-access?channel=1/stable) for such configuration.

## External relation (Juju)

[u]Use case[/u]: the client application is a Juju application outside of DB deployment (e.g. hybrid Juju deployment with different VM clouds/controllers).

In this case the the cross-controllers-relation is necessary. Please [contact](/t/11863) Data team to discuss the possible option for your use case.