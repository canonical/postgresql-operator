# Cleanup and extra info

This is part of the [Charmed PostgreSQL Tutorial](TODO). Please refer to this page for more information and the overview of the content.

## Remove Multipass VM
If you're done with testing and would like to free up resources on your machine, just remove Multipass VM.
*Warning: when you remove VM as shown below you will lose all the data in PostgreSQL and any other applications inside Multipass VM!*
```shell
multipass delete --purge my-vm
```

## Next Steps

In this tutorial we've successfully deployed PostgreSQL, added/removed cluster members, added/removed users to/from the database, and even enabled and disabled TLS. You may now keep your Charmed PostgreSQL deployment running and write to the database or remove it entirely using the steps in [Remove Charmed PostgreSQL and Juju](#remove-charmed-postgresql-and-juju). If you're looking for what to do next you can:
- Run [Charmed PostgreSQL on Kubernetes](https://github.com/canonical/postgresql-k8s-operator).
- Check out our Charmed offerings of [MySQL](https://charmhub.io/mysql?channel=edge) and [Kafka](https://charmhub.io/kafka?channel=edge).
- Read about [High Availability Best Practices](https://canonical.com/blog/database-high-availability)
- [Report](https://github.com/canonical/postgresql-operator/issues) any problems you encountered.
- [Give us your feedback](https://chat.charmhub.io/charmhub/channels/data-platform).
- [Contribute to the code base](https://github.com/canonical/postgresql-operator)

