# How to integrate a database with your charm

Charmed PostgreSQL can be integrated with any charmed application that supports its interfaces. This page provides some guidance and resources for charm developers to develop, integrate, and troubleshoot their charm so that it may connect with PostgreSQL.

## Check supported interfaces
First, we recommend that you check [the supported interfaces](/explanation/interfaces-and-endpoints) of the current charm. You have the option to use modern (preferred) or legacy interfaces. 

Most existing charms currently use [ops-lib-pgsql](https://github.com/canonical/ops-lib-pgsql) interface (legacy). For new charms, **Canonical recommends using [data-platform-libs](https://github.com/canonical/data-platform-libs).**

Legacy charm details are described [here](/explanation/legacy-charm).

## Integrate your charm with PostgreSQL
> See also: [Juju documentation | Integration](https://juju.is/docs/juju/integration)

**For a detailed tutorial** about integrating your charm with the PostgreSQL charm, refer to [Juju | Integrate your charm with PostgreSQL](https://juju.is/docs/sdk/integrate-your-charm-with-postgresql). 

**For some practical examples**, take a look at the following:
* [postgresql-test-app](https://github.com/canonical/postgresql-test-app) GitHub repository
*  [juju-sdk-tutorial-k8s](https://github.com/canonical/juju-sdk-tutorial-k8s/tree/04_integrate_with_psql) - the branch `04_integrate_with_psql` describes integration with Charmed PostgreSQL K8s 
* [How to migrate Nextcloud to new PostgreSQL (vm-charms)](https://discourse.charmhub.io/t/nextcloud-postgresql-how-to-migrate-nextcloud-to-new-postgresql-vm-charms/10969) guide

## Troubleshooting & testing
* To learn the basics of charm debugging, start with [Juju | How to debug a charm](https://juju.is/docs/sdk/debug-a-charm)
* To troubleshoot PostgreSQL, check the [Troubleshooting](/reference/troubleshooting/index) reference
* To test PostgreSQL and other charms, check the [Testing](/reference/software-testing) reference

## FAQ
**Does the requirer need to set anything in relation data?**
>It depends on the interface. Check the `postgresql_client` [interface requirements](https://github.com/canonical/charm-relation-interfaces/blob/main/interfaces/postgresql_client/v0/README.md).

**Is there a charm library available, or does my charm need to compile the postgresql relation data on its own?**
>Yes, a library is available: [data-platform-libs](https://github.com/canonical/data-platform-libs). The integration is trivial: [example](https://github.com/nextcloud-charmers/nextcloud-charms/pull/78).

**How do I obtain the database url/uri?**
>This feature is [planned](https://warthogs.atlassian.net/browse/DPE-2278) but currently missing.
>
>Meanwhile, use [this](https://github.com/nextcloud-charmers/nextcloud-charms/blob/91f9eebb4d40eaaff9c2f7513f66980df75c2a3b/operator-nextcloud/src/charm.py#L610-L631) example or refer to the function below.
>
>```python
>def _db_connection_string(self) -> str:
>    """Report database connection string using info from relation databag."""
>    relation = self.model.get_relation("database")
>    if not relation:
>        return ""
>
>    data = self._database.fetch_relation_data()[relation.id]
>    username = data.get("username")
>    password = data.get("password")
>    endpoints = data.get("endpoints")
>    
>    return f"postgres://{username}:{password}@{endpoints}/ratings"
> ```


## Success stories

* [How to migrate Nextcloud to new PostgreSQL (vm-charms)](https://discourse.charmhub.io/t/nextcloud-postgresql-how-to-migrate-nextcloud-to-new-postgresql-vm-charms/10969).

[Contact us](/reference/contacts) if you have any questions, issues, or ideas!

