# How to enable plugins/extensions

## Prerequisites
* A deployed [Charmed PostgreSQL operator](/t/charmed-postgresql-tutorial-deploy-postgresql/9697?channel=14/edge)

## Enable plugin/extension
Enable the plugin/extension by setting `True` as the value of its respective config option, like in the following example:
```shell
juju config postgresql plugin_<plugin name>_enable=True
```
## Integrate your application
Integrate (formerly known as "relate" in `juju v.2.9`) your application charm with the PostgreSQL charm:

```shell
juju integrate <application charm> postgresql 
```

If your application charm requests extensions through `db` or `db-admin` relation data, but the extension is not enabled yet, you'll see that the PostgreSQL application goes into a blocked state with the following message:
```shell
postgresql/0*  blocked   idle   10.1.123.30            extensions requested through relation
```
In the [Juju debug logs](https://juju.is/docs/juju/juju-debug-log) we can see the list of extensions that need to be enabled:

```shell
unit-postgresql-0: 18:04:51 ERROR unit.postgresql/0.juju-log db:5: ERROR - `extensions` (pg_trgm, unaccent) cannot be requested through relations - Please enable extensions through `juju config` and add the relation again.
```

After enabling the needed extensions through the config options, the charm will unblock. If you have removed the relation, you can add it back again.

If the application charm uses the new `postgresql_client` interface, it can use the [is_postgresql_plugin_enabled](https://charmhub.io/data-platform-libs/libraries/data_interfaces#databaserequires-is_postgresql_plugin_enabled) helper method from the data interfaces library to check whether the plugin/extension is already enabled in the database.

[note]
**Note:** Not all PostgreSQL extensions are available. The list of supported extensions is available at [ Supported plugins/extensions](/t/charmed-postgresql-k8s-reference-supported-plugins-extensions/10946).
[/note]