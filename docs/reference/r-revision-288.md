>Reference > Release Notes > [All revisions](/t/11875) > [Revision 288](/t/11876)
# Revision 288
<sub>Thursday, April 20, 2023</sub>

Dear community,

We'd like to announce that Canonical's newest Charmed PostgreSQL operator for IAAS/VM has been published in the `14/stable` [channel](https://charmhub.io/postgresql?channel=14/stable). :tada: 

## Features you can start using today
* Deploying on VM (tested with LXD, MAAS)
* Scaling up/down in one simple juju command
* HA using [Patroni](https://github.com/zalando/patroni)
* Full backups and restores are supported when using any S3-compatible storage
* TLS support (using “[tls-certificates](https://charmhub.io/tls-certificates-operator)” operator)
* DB access outside of Juju using “[data-integrator](https://charmhub.io/data-integrator)”
* Data import using standard tools e.g. “psql”.
* Documentation:

<!--
|Charm|Version|Charm channel|Documentation|License|
| --- | --- | --- | --- | --- |
|[PostgreSQL](https://github.com/canonical/postgresql-operator)|14.7|[14/stable](https://charmhub.io/postgresql?channel=14/stable) (r288)|[Tutorial](https://charmhub.io/postgresql/docs/t-overview?channel=14/edge), [Readme](https://github.com/canonical/postgresql-operator/blob/main/README.md), [Contributing](https://github.com/canonical/postgresql-operator/blob/main/CONTRIBUTING.md)|[Apache 2.0](https://github.com/canonical/postgresql-operator/blob/main/LICENSE)|
-->
## Inside the charms:

* Charmed PostgreSQL charm ships the latest PostgreSQL “14.7-0ubuntu0.22.04.1”
* VM charms [based on our](https://snapcraft.io/publisher/dataplatformbot) SNAP (Ubuntu LTS “22.04” - core22-based)
* Principal charms supports the latest LTS series “22.04” only.
* Subordinate charms support LTS “22.04” and “20.04” only.

## Technical notes

  * The new PostgreSQL charm is also a juju interface-compatible replacement for legacy PostgreSQL charms (using legacy interface `pgsql`, via endpoints `db` and `db-admin`).
However, **it is highly recommended to migrate to the modern interface [`postgresql_client`](https://github.com/canonical/charm-relation-interfaces)** (endpoint `database`).
    * Please [contact us](#heading--contact) if you are considering migrating from other “legacy” charms not mentioned above. 
* Charmed PostgreSQL follows SNAP track “14”.
* No “latest” track in use (no surprises in tracking “latest/stable”)!
  * PostgreSQL charm provide [legacy charm](/t/10690) through “latest/stable”.
* You can find charm lifecycle flowchart diagrams [here](https://github.com/canonical/postgresql-k8s-operator/tree/main/docs/reference).
* Modern interfaces are well described in the [Interfaces catalogue](https://github.com/canonical/charm-relation-interfaces) and implemented by [`data-platform-libs`](https://github.com/canonical/data-platform-libs/).
* Known limitation: PostgreSQL extensions are not yet supported.

<a href="#heading--contact"><h2 id="heading--contact"> Contact us </h2></a>
Charmed PostgreSQL is an open source project that warmly welcomes community contributions, suggestions, fixes, and constructive feedback.

* Raise software issues or feature requests on [**GitHub**](https://github.com/canonical/postgresql-operator/issues/new/choose)
* Report security issues through [**Launchpad**](https://wiki.ubuntu.com/DebuggingSecurity#How%20to%20File)
* Contact the Canonical Data Platform team through our [Matrix](https://matrix.to/#/#charmhub-data-platform:ubuntu.com) channel!

<!--The document was originally posted [here](https://discourse.charmhub.io/t/juju-operators-for-postgresql-and-mysql-are-now-stable/10223).-->