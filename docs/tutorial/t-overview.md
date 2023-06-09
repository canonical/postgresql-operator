# Charmed PostgreSQL tutorial
The Charmed PostgreSQL Operator delivers automated operations management from [day 0 to day 2](https://codilime.com/blog/day-0-day-1-day-2-the-software-lifecycle-in-the-cloud-age/) on the [PostgreSQL](https://www.postgresql.org/) relational database. It is an open source, end-to-end, production-ready data platform on top of Juju. As a first step this tutorial shows you how to get Charmed PostgreSQL up and running, but the tutorial does not stop there. Through this tutorial you will learn a variety of operations, everything from adding replicas to advanced operations such as enabling Transport Layer Security (TLS). In this tutorial we will walk through how to:
- Set up an environment using [Multipass](https://multipass.run/) with [LXD](https://ubuntu.com/lxd) and [Juju](https://juju.is/).
- Deploy PostgreSQL using a single command.
- Access the database directly.
- Add high availability with PostgreSQL Patroni-based cluster.
- Request and change passwords.
- Automatically create PostgreSQL users via Juju relations.
- Reconfigure TLS certificate in one command.

While this tutorial intends to guide and teach you as you deploy Charmed PostgreSQL, it will be most beneficial if you already have a familiarity with:
- Basic terminal commands.
- PostgreSQL concepts such as replication and users.

## Step-by-step guide

Here’s an overview of the steps required with links to our separate tutorials that deal with each individual step:
* [Set up the environment](/t/charmed-postgresql-tutorial-setup-environment/9709?channel=14/stable)
* [Deploy PostgreSQL](/t/charmed-postgresql-tutorial-deploy-postgresql/9697?channel=14/stable)
* [Managing your units](/t/charmed-postgresql-tutorial-managing-units/9705?channel=14/stable)
* [Manage passwords](/t/charmed-postgresql-tutorial-manage-passwords/9703?channel=14/stable)
* [Relate your PostgreSQL to other applications](/t/charmed-postgresql-tutorial-integrations/9701?channel=14/stable)
* [Enable security](/t/charmed-postgresql-tutorial-enable-security/9699?channel=14/stable)
* [Cleanup your environment](/t/charmed-postgresql-tutorial-cleanup-environment/9695?channel=14/stable)