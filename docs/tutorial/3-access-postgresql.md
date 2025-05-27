# Access PostgreSQL

In this section, you will learn how to get the credentials of your deployment, connect to the PostgreSQL instance, view its default databases, and finally, create your own new database. 
```{caution}
This part of the tutorial accesses PostgreSQL via the `operator` user. 

**Do not directly interface with the `operator` user in a production environment.**

In a later section about [integrations,](/tutorial/6-integrate-with-other-applications) we will cover how to safely access PostgreSQL by creating a separate user via the [Data Integrator charm](https://charmhub.io/data-integrator)
```

## Retrieve credentials

Connecting to the database requires that you know three pieces of information: The internal postgreSQL database's username and password, and the host machine's IP address. 

The IP addresses associated with each application unit can be found using the `juju status` command. Since we will use the leader unit to connect to PostgreSQL, we are interested in the IP address for the unit marked with `*`, like shown in the output below:
```shell
Unit           	  Workload  Agent  Address   Ports  Message
postgresql/0*     active	idle   10.1.110.80     	Primary
```

The user we will connect to in this tutorial will be 'operator'. To retrieve its associated password, run the Charmed PostgreSQL action get-password:
```shell
juju run postgresql/leader get-password
```
The command above should output something like this:
```shell
Running operation 1 with 1 task
  - task 2 on unit-postgresql-0

Waiting for task 2...
password: 66hDfCMm3ofT0yrG
```
In order to retrieve the password of a user other than 'operator', use the option username:
```shell
juju run postgresql/leader get-password username=replication
```

At this point, we have all the information required to access PostgreSQL. Run the command below to enter the leader unit's shell as root:

```shell
juju ssh --container postgresql postgresql/leader bash
```
which should bring you to a prompt like this: 

```shell
 root@postgresql-0:/#
```
The following commands should be executed from this remote shell you just logged into. 

>If you’d like to leave the unit's shell and return to your local terminal, enter `Ctrl+D` or type `exit`.

## Access PostgreSQL via `psql`

The easiest way to interact with PostgreSQL is via [PostgreSQL interactive terminal `psql`](https://www.postgresql.org/docs/14/app-psql.html), which is already installed on the host you're connected to.

For example, to list all databases currently available, run the command below. When requested, enter the password that you obtained earlier.
```shell
psql --host=10.1.110.80 --username=operator --password --list
```

You can see below the output for the list of databases. `postgres` is the default database we are connected to and is used for administrative tasks and for creating other databases.  
```shell
   Name    |  Owner   | Encoding |   Collate   |    Ctype    |   Access privileges
-----------+----------+----------+-------------+-------------+-----------------------
 postgres  | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 |
 template0 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
 template1 | operator | UTF8     | en_US.UTF-8 | en_US.UTF-8 | =c/operator          +
           |          |          |             |             | operator=CTc/operator
(3 rows)
```

In order to execute queries, we should enter psql's interactive terminal by running the following command, again typing password when requested:
```shell
 psql --host=10.1.110.80 --username=operator --password postgres
```

The output should be something like this:

```shell
psql (14.10 (Ubuntu 14.10-0ubuntu0.22.04.1))
Type "help" for help.

postgres=# 
```
Now you are successfully logged in the interactive terminal. Here it is possible to execute commands to PostgreSQL directly using PostgreSQL SQL Queries. For example, to show which version of PostgreSQL is installed, run the following command:

```shell
postgres=# SELECT version();
                                                             version
---------------------------------------------------------------------------------------------------------------------------------
 PostgreSQL 14.10 (Ubuntu 14.10-0ubuntu0.22.04.1) on x86_64-pc-linux-gnu, compiled by gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0, 64-bit
(1 row)
```

We can see that PostgreSQL version 14.10 is installed. From this prompt, to print the list of available databases, we can simply run this command:

```shell
postgres=# \l
```

The output should be the same as the one obtained before with `psql`, but this time we did not need to specify any parameters since we are already connected to the PostgreSQL application.

### Create a new database
For creating and connecting to a new sample database, we can run the following commands:
```shell
postgres=# CREATE DATABASE mynewdatabase;
postgres=# \c mynewdatabase

You are now connected to database "mynewdatabase" as user "operator".
```

We can now create a new table inside this database:

```shell
postgres=# CREATE TABLE mytable (
	id SERIAL PRIMARY KEY,
	name VARCHAR(50),
	age INT
);
```

And insert an element into it:

```shell
postgres=# INSERT INTO mytable (name, age) VALUES ('John', 30);
```

We can see our new table element by submitting a query:

```shell
postgres=# SELECT * FROM mytable;

 id | name | age
----+------+-----
  1 | John |  30
(1 row)
```

You can try multiple SQL commands inside this environment. Once you're ready, reconnect to the default postgres database and drop the sample database we created:

```shell
postgres=# \c postgres

You are now connected to database "postgres" as user "operator".
postgres=# DROP DATABASE mynewdatabase;
```

When you’re ready to leave the PostgreSQL shell, you can just type `exit`. This will take you back to the host of Charmed PostgreSQL (`postgresql/0`). Exit this host by once again typing exit. Now you will be in your original shell where you first started the tutorial. Here you can interact with Juju and LXD.