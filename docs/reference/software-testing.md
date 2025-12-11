# Software testing for charms

Most types of standard [software tests](https://en.wikipedia.org/wiki/Software_testing) are applicable to Charmed PostgreSQL.

## Smoke test

This type of test ensures that basic functionality works over a short amount of time. 

One way to do this is by integrating your PostgreSQL application with the [PostgreSQL Test Appplication](https://charmhub.io/postgresql-test-app), and running the "continuous writes" test:

```shell
juju run postgresql-test-app/leader start-continuous-writes
```

The expected behavior is:
* `postgresql-test-app` continuously inserts records into the database received through the integration (the table `continuous_writes`).
* The counters (amount of records in table) are growing on all cluster members

To stop the "continuous write" test, run

```shell
juju run postgresql-test-app/leader stop-continuous-writes
```

To truncate the "continuous write" table (i.e. delete all records from database), run

```shell
juju run postgresql-test-app/leader clear-continuous-writes
```

## Unit test

Check the [Contributing guide](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md#testing) on GitHub and follow `tox run -e unit` examples there.

## Integration test

Check the [Contributing guide](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md#testing) on GitHub and follow `tox run -e integration` examples there.

## System test

To perform a system test, deploy  [`postgresql-bundle`](https://charmhub.io/postgresql-bundle). 

This charm bundle automatically deploys and tests all the necessary parts at once.

