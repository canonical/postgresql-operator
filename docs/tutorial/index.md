(tutorial-index)=


# Tutorial

This section of our documentation contains comprehensive, hands-on tutorials to help you learn how to deploy Charmed PostgreSQL on machines and become familiar with its available operations.

## Prerequisites

While this tutorial intends to guide you as you deploy Charmed PostgreSQL for the first time, it will be most beneficial if:
- You have some experience using a Linux-based CLI
- You are familiar with PostgreSQL concepts such as replication and users.
- Your computer fulfils the [minimum system requirements](/reference/system-requirements)

## Tutorial contents
This Charmed PostgreSQL tutorial has the following parts:

| Step | Details |
| ------- | ---------- |
| 1. [**Set up your environment**](/tutorial/1-set-up-environment) | Set up a cloud environment for your deployment using [Multipass](https://multipass.run/) with [LXD](https://ubuntu.com/lxd) and [Juju](https://juju.is/).
| 2. [**Deploy PostgreSQL**](/tutorial/2-deploy-postgresql) | Learn to deploy Charmed PostgreSQL with Juju
| 3. [**Access PostgreSQL**](/tutorial/3-access-postgresql) |   Access a PostgreSQL instance directly
| 4. [**Scale your replicas**](/tutorial/4-scale-replicas) | Learn how to enable high availability with a [Patroni](https://patroni.readthedocs.io/en/latest/)-based cluster.
| 5. [**Manage passwords**](/tutorial/5-manage-passwords) | Learn how to request and change passwords.
| 6. [**Integrate PostgreSQL with other applications**](/tutorial/6-integrate-with-other-applications) | Learn how to integrate with other applications using the Data Integrator charm, access the integrated database, and manage users.
| 7. [**Enable TLS encryption**](/tutorial/7-enable-tls-encryption) | Learn how to enable security in your PostgreSQL deployment via TLS.
| 8. [**Clean up your environment**](/tutorial/8-clean-up-environment) | Free up your machine's resources.


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

*
