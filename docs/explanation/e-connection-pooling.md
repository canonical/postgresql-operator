# Connection pooling 

Connection pooling is a strategy to reduce the amount of active connections and the costs of reopening connections. It requires maintaining a set of persistently opened connections, called a pool, that can be reused by clients.

Since PostgreSQL spawns separate processes for client connections, it can be beneficial in some use cases to maintain a client side connection pool rather than increase the database connection limits and resource consumption. 

A way to achieve this with Charmed PostgreSQL is by integrating with the [PgBouncer charm](https://charmhub.io/pgbouncer?channel=1/stable).	

## Increasing maximum allowed connections

If using PgBouncer is not enough to handle the connections load of your application, you can increase the amount of connections that PostgreSQL can open via the [`experimental_max_connections` config parameter](https://charmhub.io/postgresql/configurations#experimental_max_connections). 

[note type="caution"]
**Disclaimer:** Each connection opened by PostgreSQL spawns a new process, which is resource-intensive. Use this option as a last resort.

[Contact us](/t/11863) for more guidance about your use-case.
[/note]