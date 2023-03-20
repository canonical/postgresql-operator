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

## Minimum requirements
Before we start, make sure your machine meets the following requirements:
- Ubuntu 20.04 (Focal) or later.
- 8GB of RAM.
- 2 CPU threads.
- At least 20GB of available storage.
- Access to the internet for downloading the required snaps and charms.


## Multipass environment
[Multipass](https://multipass.run/) is a quick and easy way to launch virtual machines running Ubuntu. It uses "[cloud-init](https://cloud-init.io/)" standard to install and configure all the necessary parts automatically.

Let's install Multipass from [Snap](https://snapcraft.io/multipass) and launch a new VM using "[charm-dev](https://github.com/canonical/multipass-blueprints/blob/main/v1/charm-dev.yaml)" cloud-init config:
```shell
sudo snap install multipass && \
multipass launch --cpus 4 --memory 8G --disk 30G --name my-vm charm-dev # tune CPU/RAM/HDD accordingly to your needs 
```
*Note: all 'multipass launch' params are [described here](https://multipass.run/docs/launch-command)*.

Multipass [list of commands](https://multipass.run/docs/multipass-cli-commands) is short and self-explanatory, e.g. show all running VMs:
```shell
multipass list
```

As soon as new VM started, enter inside using:
```shell
multipass shell my-vm
```
*Note: if at any point you'd like to leave Multipass VM, enter `Ctrl+d` or type `exit`*.

All the parts have been pre-installed inside VM already, like LXD and Juju (the files '/var/log/cloud-init.log' and '/var/log/cloud-init-output.log' contain all low-level installation details). Let's bootstrap Juju to use local LXD:
```shell
juju bootstrap localhost overlord
```

The controller can work with different models; models host applications such as Charmed PostgreSQL. Set up a specific model for Charmed PostgreSQL named ‘tutorial’:
```shell
juju add-model tutorial
```

You can now view the model you created above by entering the command `juju status` into the command line. You should see the following:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  09:38:32+01:00

Model "admin/tutorial" is empty.
```


## Deploy Charmed PostgreSQL
To deploy Charmed PostgreSQL, all you need to do is run the following command, which will fetch the charm from [Charmhub](https://charmhub.io/postgresql?channel=edge) and deploy it to your model:
```shell
juju deploy postgresql --channel edge
```

Juju will now fetch Charmed PostgreSQL and begin deploying it to the LXD cloud. This process can take several minutes depending on how provisioned (RAM, CPU, etc) your machine is. You can track the progress by running:
```shell
juju status --watch 1s
```

This command is useful for checking the status of Charmed PostgreSQL and gathering information about the machines hosting Charmed PostgreSQL. Some of the helpful information it displays include IP addresses, ports, state, etc. The command updates the status of Charmed PostgreSQL every second and as the application starts you can watch the status and messages of Charmed PostgreSQL change. Wait until the application is ready - when it is ready, `juju status` will show:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  09:41:53+01:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql           active      1  postgresql  edge     281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
```
To exit the screen with `juju status --watch 1s`, enter `Ctrl+c`.
If you want to further inspect juju logs, can watch for logs with `juju debug-log`.
More info on logging at [juju logs](https://juju.is/docs/olm/juju-logs).

## Access PostgreSQL
> **!** *Disclaimer: this part of the tutorial accesses PostgreSQL via the `operator` user. **Do not** directly interface with this user in a production environment. In a production environment always create a separate user using [Data Integrator](https://charmhub.io/data-integrator) and connect to PostgreSQL with that user instead. Later in the section covering Relations we will cover how to access PostgreSQL without the `operator` user.*

The first action most users take after installing PostgreSQL is accessing PostgreSQL. The easiest way to do this is via the [PostgreSQL interactive terminal](https://www.postgresql.org/docs/14/app-psql.html) `psql`. Connecting to the database requires that you know the values for `host`, `username` and `password`. To retrieve the necessary fields please run Charmed PostgreSQL action `get-password`:
```shell
juju run-action postgresql/leader get-password --wait
```
Running the command should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "2"
  results:
    operator-password: <password>
  status: completed
  timing:
    completed: 2023-03-20 08:42:22 +0000 UTC
    enqueued: 2023-03-20 08:42:19 +0000 UTC
    started: 2023-03-20 08:42:21 +0000 UTC
```

*Note: to request a password for a different user, use an option `username`:*
```shell
juju run-action postgresql/leader get-password username=replication --wait
```

The host’s IP address can be found with `juju status` (the unit hosting the PostgreSQL application):
```
...
Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129       
...
```

To access the units hosting Charmed PostgreSQL use:
```shell
juju ssh postgresql/leader
```
*Note: if at any point you'd like to leave the unit hosting Charmed PostgreSQL, enter* `Ctrl+d` or type `exit`*.

The `psql` tool is already installed here. To show list of all available databases use:
```shell
psql --host=10.89.49.129 --username=operator --password --list
```
*Note: when requested, enter the `<password>` for charm user `operator` from the output above.*

The example of the output:
```
                              List of databases
   Name    |  Owner   | Encoding | Collate |  Ctype  |   Access privileges   
-----------+----------+----------+---------+---------+-----------------------
 postgres  | operator | UTF8     | C.UTF-8 | C.UTF-8 | 
 template0 | operator | UTF8     | C.UTF-8 | C.UTF-8 | =c/operator          +
           |          |          |         |         | operator=CTc/operator
 template1 | operator | UTF8     | C.UTF-8 | C.UTF-8 | =c/operator          +
           |          |          |         |         | operator=CTc/operator
(3 rows)
```

You can now interact with PostgreSQL directly using any [PostgreSQL SQL Queries](https://www.postgresql.org/docs/14/queries.html). For example entering `SELECT version();` should output something like:
```
> psql --host=10.89.49.129 --username=operator --password postgres
Password: 
psql (14.7 (Ubuntu 14.7-0ubuntu0.22.04.1))
Type "help" for help.

postgres=# SELECT version();
                                                               version                                                                
--------------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 14.7 (Ubuntu 14.7-0ubuntu0.22.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 11.3.0-1ubuntu1~22.04) 11.3.0, 64-bit
(1 row)
```
*Note: if at any point you'd like to leave the PostgreSQL client, enter `Ctrl+d` or type `exit`*.

Feel free to test out any other PostgreSQL queries. When you’re ready to leave the PostgreSQL shell you can just type `exit`. Once you've typed `exit` you will be back in the host of Charmed PostgreSQL (`postgresql/0`). Exit this host by once again typing `exit`. Now you will be in your original shell where you first started the tutorial; here you can interact with Juju and LXD.

## Scale Charmed PostgreSQL
Charmed PostgreSQL operator uses [PostgreSQL Patroni-based cluster](https://patroni.readthedocs.io/en/latest/) for scaling. It provides features such as automatic membership management, fault tolerance, automatic failover, and so on. The charm uses Postgres’s [Synchronous replication](https://patroni.readthedocs.io/en/latest/replication_modes.html#postgresql-synchronous-replication) with Patroni.

> **!** *Disclaimer: this tutorial hosts replicas all on the same machine, this should not be done in a production environment. To enable high availability in a production environment, replicas should be hosted on different servers to [maintain isolation](https://canonical.com/blog/database-high-availability).*


### Add cluster members (replicas)
You can add two replicas to your deployed PostgreSQL application with:
```shell
juju add-unit postgresql -n 2
```

You can now watch the scaling process in live using: `juju status --watch 1s`. It usually takes several minutes for new cluster members to be added. You’ll know that all three nodes are in sync when `juju status` reports `Workload=active` and `Agent=idle`:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:16:44+01:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql           active      3  postgresql  edge     281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           Primary
postgresql/1   active    idle   1        10.89.49.197           
postgresql/2   active    idle   2        10.89.49.175           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
2        started  10.89.49.175  juju-a8a31d-2  jammy       Running
```

### Remove cluster members (replicas)
Removing a unit from the application, scales the replicas down. Before we scale down the replicas, list all the units with `juju status`, here you will see three units `postgresql/0`, `postgresql/1`, and `postgresql/2`. Each of these units hosts a PostgreSQL replica. To remove the replica hosted on the unit `postgresql/2` enter:
```shell
juju remove-unit postgresql/2
```

You’ll know that the replica was successfully removed when `juju status --watch 1s` reports:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:17:14+01:00

App         Version  Status  Scale  Charm       Channel  Rev  Exposed  Message
postgresql           active      2  postgresql  edge     281  no       

Unit           Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*  active    idle   0        10.89.49.129           
postgresql/1   active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
```

## Passwords
When we accessed PostgreSQL earlier in this tutorial, we needed to use a password manually. Passwords help to secure our database and are essential for security. Over time it is a good practice to change the password frequently. Here we will go through setting and changing the password for the admin user.

### Retrieve the password
As previously mentioned, the operator's password can be retrieved by running the `get-password` action on the Charmed PostgreSQL application:
```shell
juju run-action postgresql/leader get-password --wait
```
Running the command should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "14"
  results:
    operator-password: eItxBiOYeMf7seSv
  status: completed
  timing:
    completed: 2023-03-20 09:17:51 +0000 UTC
    enqueued: 2023-03-20 09:17:49 +0000 UTC
    started: 2023-03-20 09:17:50 +0000 UTC
```

### Rotate the password
You can change the operator's password to a new random password by entering:
```shell
juju run-action postgresql/leader set-password --wait
```
Running the command should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "16"
  results:
    operator-password: npGdNGNGVtu7SO50
  status: completed
  timing:
    completed: 2023-03-20 09:18:11 +0000 UTC
    enqueued: 2023-03-20 09:18:08 +0000 UTC
    started: 2023-03-20 09:18:10 +0000 UTC
```
Please notice the `status: completed` above which means the password has been successfully updated. The password should be different from the previous password.

### Set the new password
You can change the password to a specific password by entering:
```shell
juju run-action postgresql/leader set-password password=my-password --wait
```
Running the command should output:
```yaml
unit-postgresql-0:
  UnitId: postgresql/0
  id: "18"
  results:
    operator-password: my-password
  status: completed
  timing:
    completed: 2023-03-20 09:20:06 +0000 UTC
    enqueued: 2023-03-20 09:20:04 +0000 UTC
    started: 2023-03-20 09:20:05 +0000 UTC
```
The password should match whatever you passed in when you entered the command.

## Integrations (Relations for Juju 2.9)
Relations, or what Juju 3.0+ documentation [describes as an Integration](https://juju.is/docs/sdk/integration), are the easiest way to create a user for PostgreSQL in Charmed PostgreSQL. Relations automatically create a username, password, and database for the desired user/application. As mentioned earlier in the [Access PostgreSQL section](#access-PostgreSQL) it is a better practice to connect to PostgreSQL via a specific user rather than the admin user.

### Data Integrator Charm
Before relating to a charmed application, we must first deploy our charmed application. In this tutorial we will relate to the [Data Integrator Charm](https://charmhub.io/data-integrator). This is a bare-bones charm that allows for central management of database users, providing support for different kinds of data platforms (e.g. PostgreSQL, MySQL, MongoDB, Kafka, etc) with a consistent, opinionated and robust user experience. In order to deploy the Data Integrator Charm we can use the command `juju deploy` we have learned above:

```shell
juju deploy data-integrator --channel edge --config database-name=test-database
```
The expected output:
```
Located charm "data-integrator" in charm-hub, revision 6
Deploying "data-integrator" from charm-hub charm "data-integrator", revision 6 in channel edge on jammy
```

Checking the deployment progress using `juju status` will show you the `blocked` state for newly deployed charm:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:22:13+01:00

App              Version  Status   Scale  Charm            Channel  Rev  Exposed  Message
data-integrator           blocked      1  data-integrator  edge       6  no       Please relate the data-integrator with the desired product
postgresql                active       2  postgresql       edge     281  no       

Unit                Workload  Agent  Machine  Public address  Ports  Message
data-integrator/0*  blocked   idle   3        10.89.49.179           Please relate the data-integrator with the desired product
postgresql/0*       active    idle   0        10.89.49.129           
postgresql/1        active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
3        started  10.89.49.179  juju-a8a31d-3  jammy       Running
```
The `blocked` state is expected due to not-yet established relation (integration) between applications.

### Relate to PostgreSQL
Now that the Database Integrator Charm has been set up, we can relate it to PostgreSQL. This will automatically create a username, password, and database for the Database Integrator Charm. Relate the two applications with:
```shell
juju relate data-integrator postgresql
```
Wait for `juju status --watch 1s` to show all applications/units as `active`:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:22:31+01:00

App              Version  Status  Scale  Charm            Channel  Rev  Exposed  Message
data-integrator           active      1  data-integrator  edge       6  no       
postgresql                active      2  postgresql       edge     281  no       

Unit                Workload  Agent  Machine  Public address  Ports  Message
data-integrator/0*  active    idle   3        10.89.49.179           
postgresql/0*       active    idle   0        10.89.49.129           Primary
postgresql/1        active    idle   1        10.89.49.197           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
3        started  10.89.49.179  juju-a8a31d-3  jammy       Running
```

To retrieve information such as the username, password, and database. Enter:
```shell
juju run-action data-integrator/leader get-credentials --wait
```
This should output something like:
```yaml
unit-data-integrator-0:
  UnitId: data-integrator/0
  id: "20"
  results:
    ok: "True"
    postgresql:
      database: test-database
      endpoints: 10.89.49.129:5432
      password: 136bvw0s7FjJ6mxZ
      read-only-endpoints: 10.89.49.197:5432
      username: relation-3
      version: "14.7"
  status: completed
  timing:
    completed: 2023-03-20 09:22:50 +0000 UTC
    enqueued: 2023-03-20 09:22:46 +0000 UTC
    started: 2023-03-20 09:22:50 +0000 UTC
```
*Note: your hostnames, usernames, and passwords will likely be different.*

### Access the related database
Use `endpoints`, `username`, `password` from above to connect newly created database `test-database` on PostgreSQL server:
```shell
> psql --host=10.89.49.129 --username=relation-3 --password test-database
...
test-database=> \l
...
 test-database | operator | UTF8     | C.UTF-8 | C.UTF-8 | =Tc/operator             +
               |          |          |         |         | operator=CTc/operator    +
               |          |          |         |         | "relation-3"=CTc/operator
...
```

The newly created database `test-database` is also available on all other PostgreSQL cluster members:
```shell
> psql --host=10.89.49.197 --username=relation-3 --password --list
...
 test-database | operator | UTF8     | C.UTF-8 | C.UTF-8 | =Tc/operator             +
               |          |          |         |         | operator=CTc/operator    +
               |          |          |         |         | "relation-3"=CTc/operator
...
```

When you relate two applications Charmed PostgreSQL automatically sets up a new user and database for you.
Please note the database name we specified when we first deployed the `data-integrator` charm: `--config database-name=test-database`.

### Remove the user
To remove the user, remove the relation. Removing the relation automatically removes the user that was created when the relation was created. Enter the following to remove the relation:
```shell
juju remove-relation postgresql data-integrator
```

Now try again to connect to the same PostgreSQL you just used in [Access the related database](#access-the-related-database):
```shell
> psql --host=10.89.49.129 --username=relation-3 --password --list
```

This will output an error message:
```
psql: error: connection to server at "10.89.49.129", port 5432 failed: FATAL:  password authentication failed for user "relation-3"
```
As this user no longer exists. This is expected as `juju remove-relation postgresql data-integrator` also removes the user.
Note: data stay remain on the server at this stage!

Relate the the two applications again if you wanted to recreate the user:
```shell
juju relate data-integrator postgresql
```
Re-relating generates a new user and password:
```shell
juju run-action data-integrator/leader get-credentials --wait
```
You can connect to the database with this new credentials.
From here you will see all of your data is still present in the database.

## Transport Layer Security (TLS)
[TLS](https://en.wikipedia.org/wiki/Transport_Layer_Security) is used to encrypt data exchanged between two applications; it secures data transmitted over the network. Typically, enabling TLS within a highly available database, and between a highly available database and client/server applications, requires domain-specific knowledge and a high level of expertise. Fortunately, the domain-specific knowledge has been encoded into Charmed PostgreSQL. This means (re-)configuring TLS on Charmed PostgreSQL is readily available and requires minimal effort on your end.

Again, relations come in handy here as TLS is enabled via relations; i.e. by relating Charmed PostgreSQL to the [TLS Certificates Charm](https://charmhub.io/tls-certificates-operator). The TLS Certificates Charm centralises TLS certificate management in a consistent manner and handles providing, requesting, and renewing TLS certificates.


### Configure TLS
Before enabling TLS on Charmed PostgreSQL we must first deploy the `tls-certificates-operator` charm:
```shell
juju deploy tls-certificates-operator --channel=edge --config generate-self-signed-certificates="true" --config ca-common-name="Tutorial CA"
```

Wait until the `tls-certificates-operator` is up and active, use `juju status --watch 1s` to monitor the progress:
```
Model     Controller  Cloud/Region         Version  SLA          Timestamp
tutorial  overlord    localhost/localhost  2.9.42   unsupported  10:31:40+01:00

App                        Version  Status  Scale  Charm                      Channel  Rev  Exposed  Message
postgresql                          active      2  postgresql                 edge     281  no       
tls-certificates-operator           active      1  tls-certificates-operator  edge      22  no       

Unit                          Workload  Agent  Machine  Public address  Ports  Message
postgresql/0*                 active    idle   0        10.89.49.129           Primary
postgresql/1                  active    idle   1        10.89.49.197           
tls-certificates-operator/0*  active    idle   4        10.89.49.185           

Machine  State    Address       Inst id        Series  AZ  Message
0        started  10.89.49.129  juju-a8a31d-0  jammy       Running
1        started  10.89.49.197  juju-a8a31d-1  jammy       Running
4        started  10.89.49.185  juju-a8a31d-4  jammy       Running
```
*Note: this tutorial uses [self-signed certificates](https://en.wikipedia.org/wiki/Self-signed_certificate); self-signed certificates should not be used in a production cluster.*

To enable TLS on Charmed PostgreSQL, relate the two applications:
```shell
juju relate postgresql tls-certificates-operator
```

### Add external TLS certificate
Use `openssl` to connect to the PostgreSQL and check the TLS certificate in use:
```shell
> openssl s_client -starttls postgres -connect 10.89.49.129:5432 | grep Issuer
...
depth=1 C = US, CN = Tutorial CA
verify error:num=19:self-signed certificate in certificate chain
...
```
Congratulations! PostgreSQL is now using TLS certificate generated by the external application `tls-certificates-operator`.


### Remove external TLS certificate
To remove the external TLS and return to the locally generate one, unrelate applications:
```shell
juju remove-relation postgresql tls-certificates-operator
```

Check the TLS certificate in use:
```shell
> openssl s_client -starttls postgres -connect 10.89.49.129:5432
...
no peer certificate available
---
No client certificate CA names sent
...
```
The Charmed PostgreSQL application is not using TLS anymore.

## Next Steps
In this tutorial we've successfully deployed PostgreSQL, added/removed cluster members, added/removed users to/from the database, and even enabled and disabled TLS. You may now keep your Charmed PostgreSQL deployment running and write to the database or remove it entirely using the steps in [Remove Charmed PostgreSQL and Juju](#remove-charmed-postgresql-and-juju). If you're looking for what to do next you can:
- Run [Charmed PostgreSQL on Kubernetes](https://github.com/canonical/postgresql-k8s-operator).
- Check out our Charmed offerings of [MySQL](https://charmhub.io/mysql?channel=edge) and [Kafka](https://charmhub.io/kafka?channel=edge).
- Read about [High Availability Best Practices](https://canonical.com/blog/database-high-availability)
- [Report](https://github.com/canonical/postgresql-operator/issues) any problems you encountered.
- [Give us your feedback](https://chat.charmhub.io/charmhub/channels/data-platform).
- [Contribute to the code base](https://github.com/canonical/postgresql-operator)

## Remove Multipass VM
If you're done with testing and would like to free up resources on your machine, just remove Multipass VM.
*Warning: when you remove VM as shown below you will lose all the data in PostgreSQL and any other applications inside Multipass VM!*
```shell
multipass delete --purge my-vm
```

# License:
The Charmed PostgreSQL Operator [is distributed](https://github.com/canonical/postgresql-operator/blob/main/LICENSE) under the Apache Software License, version 2.0. It installs/operates/depends on [PostgreSQL](https://www.postgresql.org/ftp/source/), which [is licensed](https://www.postgresql.org/about/licence/) under PostgreSQL License, a liberal Open Source license, similar to the BSD or MIT licenses..

## Trademark Notice
PostgreSQL is a trademark or registered trademark of PostgreSQL Global Development Group. Other trademarks are property of their respective owners.
