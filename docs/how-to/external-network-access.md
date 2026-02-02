(external-network-access)=
# How to connect from outside the local network

This page summarises resources for setting up deployments where an external application must connect to a PostgreSQL database from outside the local area network. 

## External application (non-Juju)

**Use case**: The client application is a non-Juju application outside of the local area network where Juju and the database are running. 

There are many possible ways to connect the Charmed PostgreSQL database from outside of the LAN where the database cluster is located. The available options are heavily dependent on the cloud/hardware/virtualisation in use. 

One of the possible options is to use [virtual IP addresses (VIP)](https://en.wikipedia.org/wiki/Virtual_IP_address) which the charm PgBouncer provides with assistance from the charm/interface `hacluster`. Please follow the [PgBouncer documentation](https://charmhub.io/pgbouncer/docs/h-external-access) for such configuration.

> See also: [](/how-to/deploy/tls-vip-access).

## External relation (Juju)

**Use case**: The client application is a Juju application outside the database deployment (e.g. hybrid Juju deployment with different VM clouds/controllers).

In this case, a cross-controller relation is necessary. Please [contact](/reference/contacts) the Data team to discuss possible options for your use case.

