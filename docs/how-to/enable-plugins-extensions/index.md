# How to enable plugins/extensions

This guide outlines the steps for enabling an extension in a Charmed PostgreSQL deployment. 

For a list of available extensions, see the page [Supported plugins/extensions](/reference/plugins-extensions). The words "plugin" and "extension" will be used interchangeably.

## Enable extension

Enable the extension by setting `True` as the value of its respective config option, like in the following example:

```text
juju config postgresql plugin_<plugin name>_enable=True
```
## Integrate your application

```text
juju integrate <application charm> postgresql 
```

If your application charm requests extensions through `db` or `db-admin` relation data, but the extension is not enabled yet, you'll see that the PostgreSQL application goes into a blocked state with the following message:
```text
postgresql/0*  blocked   idle   10.1.123.30            extensions requested through relation
```
In the [Juju debug logs](https://juju.is/docs/juju/juju-debug-log) we can see the list of extensions that need to be enabled:

```text
unit-postgresql-0: 18:04:51 ERROR unit.postgresql/0.juju-log db:5: ERROR - `extensions` (pg_trgm, unaccent) cannot be requested through relations - Please enable extensions through `juju config` and add the relation again.
```

After enabling the needed extensions through the config options, the charm will unblock. If you have removed the relation, you can add it back again.

If the application charm uses the new `postgresql_client` interface, it can use the [is_postgresql_plugin_enabled](https://charmhub.io/data-platform-libs/libraries/data_interfaces) helper method from the data interfaces library to check whether the plugin/extension is already enabled in the database.

```{note}
Not all PostgreSQL extensions are available. The list of supported extensions is available in [](/reference/plugins-extensions).
```


```{toctree}
:titlesonly:
:maxdepth: 2
:glob:
:hidden:

Enable TimescaleDB <enable-timescaledb>
```