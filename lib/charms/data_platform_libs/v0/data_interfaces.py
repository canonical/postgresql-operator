# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

r"""Library to manage the relation for the data-platform products.

This library contains the Requires and Provides classes for handling the relation
between an application and multiple managed application supported by the data-team:
MySQL, Postgresql, MongoDB, Redis, and Kafka.

### Database (MySQL, Postgresql, MongoDB, and Redis)

#### Requires Charm
This library is a uniform interface to a selection of common database
metadata, with added custom events that add convenience to database management,
and methods to consume the application related data.


Following an example of using the DatabaseCreatedEvent, in the context of the
application charm code:

```python

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseRequires,
)

class ApplicationCharm(CharmBase):
    # Application charm that connects to database charms.

    def __init__(self, *args):
        super().__init__(*args)

        # Charm events defined in the database requires charm library.
        self.database = DatabaseRequires(self, relation_name="database", database_name="database")
        self.framework.observe(self.database.on.database_created, self._on_database_created)

    def _on_database_created(self, event: DatabaseCreatedEvent) -> None:
        # Handle the created database

        # Create configuration file for app
        config_file = self._render_app_config_file(
            event.username,
            event.password,
            event.endpoints,
        )

        # Start application with rendered configuration
        self._start_application(config_file)

        # Set active status
        self.unit.status = ActiveStatus("received database credentials")
```

As shown above, the library provides some custom events to handle specific situations,
which are listed below:

-  database_created: event emitted when the requested database is created.
-  endpoints_changed: event emitted when the read/write endpoints of the database have changed.
-  read_only_endpoints_changed: event emitted when the read-only endpoints of the database
  have changed. Event is not triggered if read/write endpoints changed too.

If it is needed to connect multiple database clusters to the same relation endpoint
the application charm can implement the same code as if it would connect to only
one database cluster (like the above code example).

To differentiate multiple clusters connected to the same relation endpoint
the application charm can use the name of the remote application:

```python

def _on_database_created(self, event: DatabaseCreatedEvent) -> None:
    # Get the remote app name of the cluster that triggered this event
    cluster = event.relation.app.name
```

It is also possible to provide an alias for each different database cluster/relation.

So, it is possible to differentiate the clusters in two ways.
The first is to use the remote application name, i.e., `event.relation.app.name`, as above.

The second way is to use different event handlers to handle each cluster events.
The implementation would be something like the following code:

```python

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseRequires,
)

class ApplicationCharm(CharmBase):
    # Application charm that connects to database charms.

    def __init__(self, *args):
        super().__init__(*args)

        # Define the cluster aliases and one handler for each cluster database created event.
        self.database = DatabaseRequires(
            self,
            relation_name="database",
            database_name="database",
            relations_aliases = ["cluster1", "cluster2"],
        )
        self.framework.observe(
            self.database.on.cluster1_database_created, self._on_cluster1_database_created
        )
        self.framework.observe(
            self.database.on.cluster2_database_created, self._on_cluster2_database_created
        )

    def _on_cluster1_database_created(self, event: DatabaseCreatedEvent) -> None:
        # Handle the created database on the cluster named cluster1

        # Create configuration file for app
        config_file = self._render_app_config_file(
            event.username,
            event.password,
            event.endpoints,
        )
        ...

    def _on_cluster2_database_created(self, event: DatabaseCreatedEvent) -> None:
        # Handle the created database on the cluster named cluster2

        # Create configuration file for app
        config_file = self._render_app_config_file(
            event.username,
            event.password,
            event.endpoints,
        )
        ...

```

When it's needed to check whether a plugin (extension) is enabled on the PostgreSQL
charm, you can use the is_postgresql_plugin_enabled method. To use that, you need to
add the following dependency to your charmcraft.yaml file:

```yaml

parts:
  charm:
    charm-binary-python-packages:
      - psycopg[binary]

```

### Provider Charm

Following an example of using the DatabaseRequestedEvent, in the context of the
database charm code:

```python
from charms.data_platform_libs.v0.data_interfaces import DatabaseProvides

class SampleCharm(CharmBase):

    def __init__(self, *args):
        super().__init__(*args)
        # Charm events defined in the database provides charm library.
        self.provided_database = DatabaseProvides(self, relation_name="database")
        self.framework.observe(self.provided_database.on.database_requested,
            self._on_database_requested)
        # Database generic helper
        self.database = DatabaseHelper()

    def _on_database_requested(self, event: DatabaseRequestedEvent) -> None:
        # Handle the event triggered by a new database requested in the relation
        # Retrieve the database name using the charm library.
        db_name = event.database
        # generate a new user credential
        username = self.database.generate_user()
        password = self.database.generate_password()
        # set the credentials for the relation
        self.provided_database.set_credentials(event.relation.id, username, password)
        # set other variables for the relation event.set_tls("False")
```
As shown above, the library provides a custom event (database_requested) to handle
the situation when an application charm requests a new database to be created.
It's preferred to subscribe to this event instead of relation changed event to avoid
creating a new database when other information other than a database name is
exchanged in the relation databag.

### Kafka

This library is the interface to use and interact with the Kafka charm. This library contains
custom events that add convenience to manage Kafka, and provides methods to consume the
application related data.

#### Requirer Charm

```python

from charms.data_platform_libs.v0.data_interfaces import (
    BootstrapServerChangedEvent,
    KafkaRequires,
    TopicCreatedEvent,
)

class ApplicationCharm(CharmBase):

    def __init__(self, *args):
        super().__init__(*args)
        self.kafka = KafkaRequires(self, "kafka_client", "test-topic")
        self.framework.observe(
            self.kafka.on.bootstrap_server_changed, self._on_kafka_bootstrap_server_changed
        )
        self.framework.observe(
            self.kafka.on.topic_created, self._on_kafka_topic_created
        )

    def _on_kafka_bootstrap_server_changed(self, event: BootstrapServerChangedEvent):
        # Event triggered when a bootstrap server was changed for this application

        new_bootstrap_server = event.bootstrap_server
        ...

    def _on_kafka_topic_created(self, event: TopicCreatedEvent):
        # Event triggered when a topic was created for this application
        username = event.username
        password = event.password
        tls = event.tls
        tls_ca= event.tls_ca
        bootstrap_server event.bootstrap_server
        consumer_group_prefic = event.consumer_group_prefix
        zookeeper_uris = event.zookeeper_uris
        ...

```

As shown above, the library provides some custom events to handle specific situations,
which are listed below:

- topic_created: event emitted when the requested topic is created.
- bootstrap_server_changed: event emitted when the bootstrap server have changed.
- credential_changed: event emitted when the credentials of Kafka changed.

### Provider Charm

Following the previous example, this is an example of the provider charm.

```python
class SampleCharm(CharmBase):

from charms.data_platform_libs.v0.data_interfaces import (
    KafkaProvides,
    TopicRequestedEvent,
)

    def __init__(self, *args):
        super().__init__(*args)

        # Default charm events.
        self.framework.observe(self.on.start, self._on_start)

        # Charm events defined in the Kafka Provides charm library.
        self.kafka_provider = KafkaProvides(self, relation_name="kafka_client")
        self.framework.observe(self.kafka_provider.on.topic_requested, self._on_topic_requested)
        # Kafka generic helper
        self.kafka = KafkaHelper()

    def _on_topic_requested(self, event: TopicRequestedEvent):
        # Handle the on_topic_requested event.

        topic = event.topic
        relation_id = event.relation.id
        # set connection info in the databag relation
        self.kafka_provider.set_bootstrap_server(relation_id, self.kafka.get_bootstrap_server())
        self.kafka_provider.set_credentials(relation_id, username=username, password=password)
        self.kafka_provider.set_consumer_group_prefix(relation_id, ...)
        self.kafka_provider.set_tls(relation_id, "False")
        self.kafka_provider.set_zookeeper_uris(relation_id, ...)

```
As shown above, the library provides a custom event (topic_requested) to handle
the situation when an application charm requests a new topic to be created.
It is preferred to subscribe to this event instead of relation changed event to avoid
creating a new topic when other information other than a topic name is
exchanged in the relation databag.
"""

import copy
import json
import logging
from abc import ABC, abstractmethod
from collections import namedtuple
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from ops import JujuVersion, Secret, SecretInfo, SecretNotFoundError
from ops.charm import (
    CharmBase,
    CharmEvents,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationEvent,
    SecretChangedEvent,
)
from ops.framework import EventSource, Object
from ops.model import Application, ModelError, Relation, Unit

# The unique Charmhub library identifier, never change it
LIBID = "6c3e6b6680d64e9c89e611d1a15f65be"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 27

PYDEPS = ["ops>=2.0.0"]

logger = logging.getLogger(__name__)

Diff = namedtuple("Diff", "added changed deleted")
Diff.__doc__ = """
A tuple for storing the diff between two data mappings.

added - keys that were added
changed - keys that still exist but have new values
deleted - key that were deleted"""


PROV_SECRET_PREFIX = "secret-"
REQ_SECRET_FIELDS = "requested-secrets"


class SecretGroup(Enum):
    """Secret groups as constants."""

    USER = "user"
    TLS = "tls"
    EXTRA = "extra"


class DataInterfacesError(Exception):
    """Common ancestor for DataInterfaces related exceptions."""


class SecretError(Exception):
    """Common ancestor for Secrets related exceptions."""


class SecretAlreadyExistsError(SecretError):
    """A secret that was to be added already exists."""


class SecretsUnavailableError(SecretError):
    """Secrets aren't yet available for Juju version used."""


class SecretsIllegalUpdateError(SecretError):
    """Secrets aren't yet available for Juju version used."""


def get_encoded_dict(
    relation: Relation, member: Union[Unit, Application], field: str
) -> Optional[Dict[str, str]]:
    """Retrieve and decode an encoded field from relation data."""
    data = json.loads(relation.data[member].get(field, "{}"))
    if isinstance(data, dict):
        return data
    logger.error("Unexpected datatype for %s instead of dict.", str(data))


def get_encoded_list(
    relation: Relation, member: Union[Unit, Application], field: str
) -> Optional[List[str]]:
    """Retrieve and decode an encoded field from relation data."""
    data = json.loads(relation.data[member].get(field, "[]"))
    if isinstance(data, list):
        return data
    logger.error("Unexpected datatype for %s instead of list.", str(data))


def set_encoded_field(
    relation: Relation,
    member: Union[Unit, Application],
    field: str,
    value: Union[str, list, Dict[str, str]],
) -> None:
    """Set an encoded field from relation data."""
    relation.data[member].update({field: json.dumps(value)})


def diff(event: RelationChangedEvent, bucket: Union[Unit, Application]) -> Diff:
    """Retrieves the diff of the data in the relation changed databag.

    Args:
        event: relation changed event.
        bucket: bucket of the databag (app or unit)

    Returns:
        a Diff instance containing the added, deleted and changed
            keys from the event relation databag.
    """
    # Retrieve the old data from the data key in the application relation databag.
    old_data = get_encoded_dict(event.relation, bucket, "data")

    if not old_data:
        old_data = {}

    # Retrieve the new data from the event relation databag.
    new_data = (
        {key: value for key, value in event.relation.data[event.app].items() if key != "data"}
        if event.app
        else {}
    )

    # These are the keys that were added to the databag and triggered this event.
    added = new_data.keys() - old_data.keys()  # pyright: ignore [reportAssignmentType]
    # These are the keys that were removed from the databag and triggered this event.
    deleted = old_data.keys() - new_data.keys()  # pyright: ignore [reportAssignmentType]
    # These are the keys that already existed in the databag,
    # but had their values changed.
    changed = {
        key
        for key in old_data.keys() & new_data.keys()  # pyright: ignore [reportAssignmentType]
        if old_data[key] != new_data[key]  # pyright: ignore [reportAssignmentType]
    }
    # Convert the new_data to a serializable format and save it for a next diff check.
    set_encoded_field(event.relation, bucket, "data", new_data)

    # Return the diff with all possible changes.
    return Diff(added, changed, deleted)


def leader_only(f):
    """Decorator to ensure that only leader can perform given operation."""

    def wrapper(self, *args, **kwargs):
        if self.component == self.local_app and not self.local_unit.is_leader():
            logger.error(
                "This operation (%s()) can only be performed by the leader unit", f.__name__
            )
            return
        return f(self, *args, **kwargs)

    return wrapper


def juju_secrets_only(f):
    """Decorator to ensure that certain operations would be only executed on Juju3."""

    def wrapper(self, *args, **kwargs):
        if not self.secrets_enabled:
            raise SecretsUnavailableError("Secrets unavailable on current Juju version")
        return f(self, *args, **kwargs)

    return wrapper


class Scope(Enum):
    """Peer relations scope."""

    APP = "app"
    UNIT = "unit"


class CachedSecret:
    """Locally cache a secret.

    The data structure is precisely re-using/simulating as in the actual Secret Storage
    """

    def __init__(
        self,
        charm: CharmBase,
        component: Union[Application, Unit],
        label: str,
        secret_uri: Optional[str] = None,
    ):
        self._secret_meta = None
        self._secret_content = {}
        self._secret_uri = secret_uri
        self.label = label
        self.charm = charm
        self.component = component

    def add_secret(self, content: Dict[str, str], relation: Relation) -> Secret:
        """Create a new secret."""
        if self._secret_uri:
            raise SecretAlreadyExistsError(
                "Secret is already defined with uri %s", self._secret_uri
            )

        secret = self.component.add_secret(content, label=self.label)
        if relation.app != self.charm.app:
            # If it's not a peer relation, grant is to be applied
            secret.grant(relation)
        self._secret_uri = secret.id
        self._secret_meta = secret
        return self._secret_meta

    @property
    def meta(self) -> Optional[Secret]:
        """Getting cached secret meta-information."""
        if not self._secret_meta:
            if not (self._secret_uri or self.label):
                return
            try:
                self._secret_meta = self.charm.model.get_secret(label=self.label)
            except SecretNotFoundError:
                if self._secret_uri:
                    self._secret_meta = self.charm.model.get_secret(
                        id=self._secret_uri, label=self.label
                    )
        return self._secret_meta

    def get_content(self) -> Dict[str, str]:
        """Getting cached secret content."""
        if not self._secret_content:
            if self.meta:
                try:
                    self._secret_content = self.meta.get_content(refresh=True)
                except (ValueError, ModelError) as err:
                    # https://bugs.launchpad.net/juju/+bug/2042596
                    # Only triggered when 'refresh' is set
                    known_model_errors = [
                        "ERROR either URI or label should be used for getting an owned secret but not both",
                        "ERROR secret owner cannot use --refresh",
                    ]
                    if isinstance(err, ModelError) and not any(
                        msg in str(err) for msg in known_model_errors
                    ):
                        raise
                    # Due to: ValueError: Secret owner cannot use refresh=True
                    self._secret_content = self.meta.get_content()
        return self._secret_content

    def set_content(self, content: Dict[str, str]) -> None:
        """Setting cached secret content."""
        if not self.meta:
            return

        if content:
            self.meta.set_content(content)
            self._secret_content = content
        else:
            self.meta.remove_all_revisions()

    def get_info(self) -> Optional[SecretInfo]:
        """Wrapper function to apply the corresponding call on the Secret object within CachedSecret if any."""
        if self.meta:
            return self.meta.get_info()


class SecretCache:
    """A data structure storing CachedSecret objects."""

    def __init__(self, charm: CharmBase, component: Union[Application, Unit]):
        self.charm = charm
        self.component = component
        self._secrets: Dict[str, CachedSecret] = {}

    def get(self, label: str, uri: Optional[str] = None) -> Optional[CachedSecret]:
        """Getting a secret from Juju Secret store or cache."""
        if not self._secrets.get(label):
            secret = CachedSecret(self.charm, self.component, label, uri)
            if secret.meta:
                self._secrets[label] = secret
        return self._secrets.get(label)

    def add(self, label: str, content: Dict[str, str], relation: Relation) -> CachedSecret:
        """Adding a secret to Juju Secret."""
        if self._secrets.get(label):
            raise SecretAlreadyExistsError(f"Secret {label} already exists")

        secret = CachedSecret(self.charm, self.component, label)
        secret.add_secret(content, relation)
        self._secrets[label] = secret
        return self._secrets[label]


# Base DataRelation


class DataRelation(Object, ABC):
    """Base relation data mainpulation (abstract) class."""

    SCOPE = Scope.APP

    # Local map to associate mappings with secrets potentially as a group
    SECRET_LABEL_MAP = {
        "username": SecretGroup.USER,
        "password": SecretGroup.USER,
        "uris": SecretGroup.USER,
        "tls": SecretGroup.TLS,
        "tls-ca": SecretGroup.TLS,
    }

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)
        self.charm = charm
        self.local_app = self.charm.model.app
        self.local_unit = self.charm.unit
        self.relation_name = relation_name
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self._on_relation_changed_event,
        )
        self._jujuversion = None
        self.component = self.local_app if self.SCOPE == Scope.APP else self.local_unit
        self.secrets = SecretCache(self.charm, self.component)

    @property
    def relations(self) -> List[Relation]:
        """The list of Relation instances associated with this relation_name."""
        return [
            relation
            for relation in self.charm.model.relations[self.relation_name]
            if self._is_relation_active(relation)
        ]

    @property
    def secrets_enabled(self):
        """Is this Juju version allowing for Secrets usage?"""
        if not self._jujuversion:
            self._jujuversion = JujuVersion.from_environ()
        return self._jujuversion.has_secrets

    # Mandatory overrides for internal/helper methods

    @abstractmethod
    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation data has changed."""
        raise NotImplementedError

    @abstractmethod
    def _get_relation_secret(
        self, relation_id: int, group_mapping: SecretGroup, relation_name: Optional[str] = None
    ) -> Optional[CachedSecret]:
        """Retrieve a Juju Secret that's been stored in the relation databag."""
        raise NotImplementedError

    @abstractmethod
    def _fetch_specific_relation_data(
        self, relation: Relation, fields: Optional[List[str]]
    ) -> Dict[str, str]:
        """Fetch data available (directily or indirectly -- i.e. secrets) from the relation."""
        raise NotImplementedError

    @abstractmethod
    def _fetch_my_specific_relation_data(
        self, relation: Relation, fields: Optional[List[str]]
    ) -> Dict[str, str]:
        """Fetch data available (directily or indirectly -- i.e. secrets) from the relation for owner/this_app."""
        raise NotImplementedError

    @abstractmethod
    def _update_relation_data(self, relation: Relation, data: Dict[str, str]) -> None:
        """Update data available (directily or indirectly -- i.e. secrets) from the relation for owner/this_app."""
        raise NotImplementedError

    @abstractmethod
    def _delete_relation_data(self, relation: Relation, fields: List[str]) -> None:
        """Delete data available (directily or indirectly -- i.e. secrets) from the relation for owner/this_app."""
        raise NotImplementedError

    # Internal helper methods

    @staticmethod
    def _is_relation_active(relation: Relation):
        """Whether the relation is active based on contained data."""
        try:
            _ = repr(relation.data)
            return True
        except (RuntimeError, ModelError):
            return False

    @staticmethod
    def _is_secret_field(field: str) -> bool:
        """Is the field in question a secret reference (URI) field or not?"""
        return field.startswith(PROV_SECRET_PREFIX)

    @staticmethod
    def _generate_secret_label(
        relation_name: str, relation_id: int, group_mapping: SecretGroup
    ) -> str:
        """Generate unique group_mappings for secrets within a relation context."""
        return f"{relation_name}.{relation_id}.{group_mapping.value}.secret"

    def _generate_secret_field_name(self, group_mapping: SecretGroup) -> str:
        """Generate unique group_mappings for secrets within a relation context."""
        return f"{PROV_SECRET_PREFIX}{group_mapping.value}"

    def _relation_from_secret_label(self, secret_label: str) -> Optional[Relation]:
        """Retrieve the relation that belongs to a secret label."""
        contents = secret_label.split(".")

        if not (contents and len(contents) >= 3):
            return

        contents.pop()  # ".secret" at the end
        contents.pop()  # Group mapping
        relation_id = contents.pop()
        try:
            relation_id = int(relation_id)
        except ValueError:
            return

        # In case '.' character appeared in relation name
        relation_name = ".".join(contents)

        try:
            return self.get_relation(relation_name, relation_id)
        except ModelError:
            return

    @classmethod
    def _group_secret_fields(cls, secret_fields: List[str]) -> Dict[SecretGroup, List[str]]:
        """Helper function to arrange secret mappings under their group.

        NOTE: All unrecognized items end up in the 'extra' secret bucket.
        Make sure only secret fields are passed!
        """
        secret_fieldnames_grouped = {}
        for key in secret_fields:
            if group := cls.SECRET_LABEL_MAP.get(key):
                secret_fieldnames_grouped.setdefault(group, []).append(key)
            else:
                secret_fieldnames_grouped.setdefault(SecretGroup.EXTRA, []).append(key)
        return secret_fieldnames_grouped

    def _get_group_secret_contents(
        self,
        relation: Relation,
        group: SecretGroup,
        secret_fields: Optional[Union[Set[str], List[str]]] = None,
    ) -> Dict[str, str]:
        """Helper function to retrieve collective, requested contents of a secret."""
        if not secret_fields:
            secret_fields = []

        if (secret := self._get_relation_secret(relation.id, group)) and (
            secret_data := secret.get_content()
        ):
            return {k: v for k, v in secret_data.items() if k in secret_fields}
        return {}

    @classmethod
    def _content_for_secret_group(
        cls, content: Dict[str, str], secret_fields: Set[str], group_mapping: SecretGroup
    ) -> Dict[str, str]:
        """Select <field>: <value> pairs from input, that belong to this particular Secret group."""
        if group_mapping == SecretGroup.EXTRA:
            return {
                k: v
                for k, v in content.items()
                if k in secret_fields and k not in cls.SECRET_LABEL_MAP.keys()
            }

        return {
            k: v
            for k, v in content.items()
            if k in secret_fields and cls.SECRET_LABEL_MAP.get(k) == group_mapping
        }

    @juju_secrets_only
    def _get_relation_secret_data(
        self, relation_id: int, group_mapping: SecretGroup, relation_name: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """Retrieve contents of a Juju Secret that's been stored in the relation databag."""
        secret = self._get_relation_secret(relation_id, group_mapping, relation_name)
        if secret:
            return secret.get_content()

    # Core operations on Relation Fields manipulations (regardless whether the field is in the databag or in a secret)
    # Internal functions to be called directly from transparent public interface functions (+closely related helpers)

    def _process_secret_fields(
        self,
        relation: Relation,
        req_secret_fields: Optional[List[str]],
        impacted_rel_fields: List[str],
        operation: Callable,
        *args,
        **kwargs,
    ) -> Tuple[Dict[str, str], Set[str]]:
        """Isolate target secret fields of manipulation, and execute requested operation by Secret Group."""
        result = {}

        # If the relation started on a databag, we just stay on the databag
        # (Rolling upgrades may result in a relation starting on databag, getting secrets enabled on-the-fly)
        # self.local_app is sufficient to check (ignored if Requires, never has secrets -- works if Provides)
        fallback_to_databag = (
            req_secret_fields
            and self.local_unit.is_leader()
            and set(req_secret_fields) & set(relation.data[self.component])
        )

        normal_fields = set(impacted_rel_fields)
        if req_secret_fields and self.secrets_enabled and not fallback_to_databag:
            normal_fields = normal_fields - set(req_secret_fields)
            secret_fields = set(impacted_rel_fields) - set(normal_fields)

            secret_fieldnames_grouped = self._group_secret_fields(list(secret_fields))

            for group in secret_fieldnames_grouped:
                # operation() should return nothing when all goes well
                if group_result := operation(relation, group, secret_fields, *args, **kwargs):
                    # If "meaningful" data was returned, we take it. (Some 'operation'-s only return success/failure.)
                    if isinstance(group_result, dict):
                        result.update(group_result)
                else:
                    # If it wasn't found as a secret, let's give it a 2nd chance as "normal" field
                    # Needed when Juju3 Requires meets Juju2 Provider
                    normal_fields |= set(secret_fieldnames_grouped[group])
        return (result, normal_fields)

    def _fetch_relation_data_without_secrets(
        self, component: Union[Application, Unit], relation: Relation, fields: Optional[List[str]]
    ) -> Dict[str, str]:
        """Fetching databag contents when no secrets are involved.

        Since the Provider's databag is the only one holding secrest, we can apply
        a simplified workflow to read the Require's side's databag.
        This is used typically when the Provides side wants to read the Requires side's data,
        or when the Requires side may want to read its own data.
        """
        if component not in relation.data or not relation.data[component]:
            return {}

        if fields:
            return {
                k: relation.data[component][k] for k in fields if k in relation.data[component]
            }
        else:
            return dict(relation.data[component])

    def _fetch_relation_data_with_secrets(
        self,
        component: Union[Application, Unit],
        req_secret_fields: Optional[List[str]],
        relation: Relation,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Fetching databag contents when secrets may be involved.

        This function has internal logic to resolve if a requested field may be "hidden"
        within a Relation Secret, or directly available as a databag field. Typically
        used to read the Provides side's databag (eigher by the Requires side, or by
        Provides side itself).
        """
        result = {}
        normal_fields = []

        if not fields:
            if component not in relation.data or not relation.data[component]:
                return {}

            all_fields = list(relation.data[component].keys())
            normal_fields = [field for field in all_fields if not self._is_secret_field(field)]

            # There must have been secrets there
            if all_fields != normal_fields and req_secret_fields:
                # So we assemble the full fields list (without 'secret-<X>' fields)
                fields = normal_fields + req_secret_fields

        if fields:
            result, normal_fields = self._process_secret_fields(
                relation, req_secret_fields, fields, self._get_group_secret_contents
            )

        # Processing "normal" fields. May include leftover from what we couldn't retrieve as a secret.
        # (Typically when Juju3 Requires meets Juju2 Provides)
        if normal_fields:
            result.update(
                self._fetch_relation_data_without_secrets(component, relation, list(normal_fields))
            )
        return result

    def _update_relation_data_without_secrets(
        self, component: Union[Application, Unit], relation: Relation, data: Dict[str, str]
    ) -> None:
        """Updating databag contents when no secrets are involved."""
        if component not in relation.data or relation.data[component] is None:
            return

        if relation:
            relation.data[component].update(data)

    def _delete_relation_data_without_secrets(
        self, component: Union[Application, Unit], relation: Relation, fields: List[str]
    ) -> None:
        """Remove databag fields 'fields' from Relation."""
        if component not in relation.data or relation.data[component] is None:
            return

        for field in fields:
            try:
                relation.data[component].pop(field)
            except KeyError:
                logger.error(
                    "Non-existing field '%s' was attempted to be removed from the databag (relation ID: %s)",
                    str(field),
                    str(relation.id),
                )
                pass

    # Public interface methods
    # Handling Relation Fields seamlessly, regardless if in databag or a Juju Secret

    def get_relation(self, relation_name, relation_id) -> Relation:
        """Safe way of retrieving a relation."""
        relation = self.charm.model.get_relation(relation_name, relation_id)

        if not relation:
            raise DataInterfacesError(
                "Relation %s %s couldn't be retrieved", relation_name, relation_id
            )

        return relation

    def fetch_relation_data(
        self,
        relation_ids: Optional[List[int]] = None,
        fields: Optional[List[str]] = None,
        relation_name: Optional[str] = None,
    ) -> Dict[int, Dict[str, str]]:
        """Retrieves data from relation.

        This function can be used to retrieve data from a relation
        in the charm code when outside an event callback.
        Function cannot be used in `*-relation-broken` events and will raise an exception.

        Returns:
            a dict of the values stored in the relation data bag
                for all relation instances (indexed by the relation ID).
        """
        if not relation_name:
            relation_name = self.relation_name

        relations = []
        if relation_ids:
            relations = [
                self.get_relation(relation_name, relation_id) for relation_id in relation_ids
            ]
        else:
            relations = self.relations

        data = {}
        for relation in relations:
            if not relation_ids or (relation_ids and relation.id in relation_ids):
                data[relation.id] = self._fetch_specific_relation_data(relation, fields)
        return data

    def fetch_relation_field(
        self, relation_id: int, field: str, relation_name: Optional[str] = None
    ) -> Optional[str]:
        """Get a single field from the relation data."""
        return (
            self.fetch_relation_data([relation_id], [field], relation_name)
            .get(relation_id, {})
            .get(field)
        )

    def fetch_my_relation_data(
        self,
        relation_ids: Optional[List[int]] = None,
        fields: Optional[List[str]] = None,
        relation_name: Optional[str] = None,
    ) -> Optional[Dict[int, Dict[str, str]]]:
        """Fetch data of the 'owner' (or 'this app') side of the relation.

        NOTE: Since only the leader can read the relation's 'this_app'-side
        Application databag, the functionality is limited to leaders
        """
        if not relation_name:
            relation_name = self.relation_name

        relations = []
        if relation_ids:
            relations = [
                self.get_relation(relation_name, relation_id) for relation_id in relation_ids
            ]
        else:
            relations = self.relations

        data = {}
        for relation in relations:
            if not relation_ids or relation.id in relation_ids:
                data[relation.id] = self._fetch_my_specific_relation_data(relation, fields)
        return data

    def fetch_my_relation_field(
        self, relation_id: int, field: str, relation_name: Optional[str] = None
    ) -> Optional[str]:
        """Get a single field from the relation data -- owner side.

        NOTE: Since only the leader can read the relation's 'this_app'-side
        Application databag, the functionality is limited to leaders
        """
        if relation_data := self.fetch_my_relation_data([relation_id], [field], relation_name):
            return relation_data.get(relation_id, {}).get(field)

    @leader_only
    def update_relation_data(self, relation_id: int, data: dict) -> None:
        """Update the data within the relation."""
        relation_name = self.relation_name
        relation = self.get_relation(relation_name, relation_id)
        return self._update_relation_data(relation, data)

    @leader_only
    def delete_relation_data(self, relation_id: int, fields: List[str]) -> None:
        """Remove field from the relation."""
        relation_name = self.relation_name
        relation = self.get_relation(relation_name, relation_id)
        return self._delete_relation_data(relation, fields)


# Base DataProvides and DataRequires


class DataProvides(DataRelation):
    """Base provides-side of the data products relation."""

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)

    def _diff(self, event: RelationChangedEvent) -> Diff:
        """Retrieves the diff of the data in the relation changed databag.

        Args:
            event: relation changed event.

        Returns:
            a Diff instance containing the added, deleted and changed
                keys from the event relation databag.
        """
        return diff(event, self.local_app)

    # Private methods handling secrets

    @juju_secrets_only
    def _add_relation_secret(
        self,
        relation: Relation,
        group_mapping: SecretGroup,
        secret_fields: Set[str],
        data: Dict[str, str],
        uri_to_databag=True,
    ) -> bool:
        """Add a new Juju Secret that will be registered in the relation databag."""
        secret_field = self._generate_secret_field_name(group_mapping)
        if uri_to_databag and relation.data[self.component].get(secret_field):
            logging.error("Secret for relation %s already exists, not adding again", relation.id)
            return False

        content = self._content_for_secret_group(data, secret_fields, group_mapping)

        label = self._generate_secret_label(self.relation_name, relation.id, group_mapping)
        secret = self.secrets.add(label, content, relation)

        # According to lint we may not have a Secret ID
        if uri_to_databag and secret.meta and secret.meta.id:
            relation.data[self.component][secret_field] = secret.meta.id

        # Return the content that was added
        return True

    @juju_secrets_only
    def _update_relation_secret(
        self,
        relation: Relation,
        group_mapping: SecretGroup,
        secret_fields: Set[str],
        data: Dict[str, str],
    ) -> bool:
        """Update the contents of an existing Juju Secret, referred in the relation databag."""
        secret = self._get_relation_secret(relation.id, group_mapping)

        if not secret:
            logging.error("Can't update secret for relation %s", relation.id)
            return False

        content = self._content_for_secret_group(data, secret_fields, group_mapping)

        old_content = secret.get_content()
        full_content = copy.deepcopy(old_content)
        full_content.update(content)
        secret.set_content(full_content)

        # Return True on success
        return True

    def _add_or_update_relation_secrets(
        self,
        relation: Relation,
        group: SecretGroup,
        secret_fields: Set[str],
        data: Dict[str, str],
        uri_to_databag=True,
    ) -> bool:
        """Update contents for Secret group. If the Secret doesn't exist, create it."""
        if self._get_relation_secret(relation.id, group):
            return self._update_relation_secret(relation, group, secret_fields, data)
        else:
            return self._add_relation_secret(relation, group, secret_fields, data, uri_to_databag)

    @juju_secrets_only
    def _delete_relation_secret(
        self, relation: Relation, group: SecretGroup, secret_fields: List[str], fields: List[str]
    ) -> bool:
        """Update the contents of an existing Juju Secret, referred in the relation databag."""
        secret = self._get_relation_secret(relation.id, group)

        if not secret:
            logging.error("Can't delete secret for relation %s", str(relation.id))
            return False

        old_content = secret.get_content()
        new_content = copy.deepcopy(old_content)
        for field in fields:
            try:
                new_content.pop(field)
            except KeyError:
                logging.error(
                    "Non-existing secret was attempted to be removed %s, %s",
                    str(relation.id),
                    str(field),
                )
                return False

        secret.set_content(new_content)

        # Remove secret from the relation if it's fully gone
        if not new_content:
            field = self._generate_secret_field_name(group)
            try:
                relation.data[self.component].pop(field)
            except KeyError:
                pass

        # Return the content that was removed
        return True

    # Mandatory internal overrides

    @juju_secrets_only
    def _get_relation_secret(
        self, relation_id: int, group_mapping: SecretGroup, relation_name: Optional[str] = None
    ) -> Optional[CachedSecret]:
        """Retrieve a Juju Secret that's been stored in the relation databag."""
        if not relation_name:
            relation_name = self.relation_name

        label = self._generate_secret_label(relation_name, relation_id, group_mapping)
        if secret := self.secrets.get(label):
            return secret

        relation = self.charm.model.get_relation(relation_name, relation_id)
        if not relation:
            return

        secret_field = self._generate_secret_field_name(group_mapping)
        if secret_uri := relation.data[self.local_app].get(secret_field):
            return self.secrets.get(label, secret_uri)

    def _fetch_specific_relation_data(
        self, relation: Relation, fields: Optional[List[str]]
    ) -> Dict[str, str]:
        """Fetching relation data for Provides.

        NOTE: Since all secret fields are in the Provides side of the databag, we don't need to worry about that
        """
        if not relation.app:
            return {}

        return self._fetch_relation_data_without_secrets(relation.app, relation, fields)

    def _fetch_my_specific_relation_data(
        self, relation: Relation, fields: Optional[List[str]]
    ) -> dict:
        """Fetching our own relation data."""
        secret_fields = None
        if relation.app:
            secret_fields = get_encoded_list(relation, relation.app, REQ_SECRET_FIELDS)

        return self._fetch_relation_data_with_secrets(
            self.local_app,
            secret_fields,
            relation,
            fields,
        )

    def _update_relation_data(self, relation: Relation, data: Dict[str, str]) -> None:
        """Set values for fields not caring whether it's a secret or not."""
        req_secret_fields = []
        if relation.app:
            req_secret_fields = get_encoded_list(relation, relation.app, REQ_SECRET_FIELDS)

        _, normal_fields = self._process_secret_fields(
            relation,
            req_secret_fields,
            list(data),
            self._add_or_update_relation_secrets,
            data=data,
        )

        normal_content = {k: v for k, v in data.items() if k in normal_fields}
        self._update_relation_data_without_secrets(self.local_app, relation, normal_content)

    def _delete_relation_data(self, relation: Relation, fields: List[str]) -> None:
        """Delete fields from the Relation not caring whether it's a secret or not."""
        req_secret_fields = []
        if relation.app:
            req_secret_fields = get_encoded_list(relation, relation.app, REQ_SECRET_FIELDS)

        _, normal_fields = self._process_secret_fields(
            relation, req_secret_fields, fields, self._delete_relation_secret, fields=fields
        )
        self._delete_relation_data_without_secrets(self.local_app, relation, list(normal_fields))

    # Public methods - "native"

    def set_credentials(self, relation_id: int, username: str, password: str) -> None:
        """Set credentials.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation_id: the identifier for a particular relation.
            username: user that was created.
            password: password of the created user.
        """
        self.update_relation_data(relation_id, {"username": username, "password": password})

    def set_tls(self, relation_id: int, tls: str) -> None:
        """Set whether TLS is enabled.

        Args:
            relation_id: the identifier for a particular relation.
            tls: whether tls is enabled (True or False).
        """
        self.update_relation_data(relation_id, {"tls": tls})

    def set_tls_ca(self, relation_id: int, tls_ca: str) -> None:
        """Set the TLS CA in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            tls_ca: TLS certification authority.
        """
        self.update_relation_data(relation_id, {"tls-ca": tls_ca})

    # Public functions -- inherited

    fetch_my_relation_data = leader_only(DataRelation.fetch_my_relation_data)
    fetch_my_relation_field = leader_only(DataRelation.fetch_my_relation_field)


class DataRequires(DataRelation):
    """Requires-side of the relation."""

    SECRET_FIELDS = ["username", "password", "tls", "tls-ca", "uris"]

    def __init__(
        self,
        charm,
        relation_name: str,
        extra_user_roles: Optional[str] = None,
        additional_secret_fields: Optional[List[str]] = [],
    ):
        """Manager of base client relations."""
        super().__init__(charm, relation_name)
        self.extra_user_roles = extra_user_roles
        self._secret_fields = list(self.SECRET_FIELDS)
        if additional_secret_fields:
            self._secret_fields += additional_secret_fields

        self.framework.observe(
            self.charm.on[relation_name].relation_created, self._on_relation_created_event
        )
        self.framework.observe(
            charm.on.secret_changed,
            self._on_secret_changed_event,
        )

    @property
    def secret_fields(self) -> Optional[List[str]]:
        """Local access to secrets field, in case they are being used."""
        if self.secrets_enabled:
            return self._secret_fields

    def _diff(self, event: RelationChangedEvent) -> Diff:
        """Retrieves the diff of the data in the relation changed databag.

        Args:
            event: relation changed event.

        Returns:
            a Diff instance containing the added, deleted and changed
                keys from the event relation databag.
        """
        return diff(event, self.local_unit)

    # Internal helper functions

    def _register_secret_to_relation(
        self, relation_name: str, relation_id: int, secret_id: str, group: SecretGroup
    ):
        """Fetch secrets and apply local label on them.

        [MAGIC HERE]
        If we fetch a secret using get_secret(id=<ID>, label=<arbitraty_label>),
        then <arbitraty_label> will be "stuck" on the Secret object, whenever it may
        appear (i.e. as an event attribute, or fetched manually) on future occasions.

        This will allow us to uniquely identify the secret on Provides side (typically on
        'secret-changed' events), and map it to the corresponding relation.
        """
        label = self._generate_secret_label(relation_name, relation_id, group)

        # Fetchin the Secret's meta information ensuring that it's locally getting registered with
        CachedSecret(self.charm, self.component, label, secret_id).meta

    def _register_secrets_to_relation(self, relation: Relation, params_name_list: List[str]):
        """Make sure that secrets of the provided list are locally 'registered' from the databag.

        More on 'locally registered' magic is described in _register_secret_to_relation() method
        """
        if not relation.app:
            return

        for group in SecretGroup:
            secret_field = self._generate_secret_field_name(group)
            if secret_field in params_name_list:
                if secret_uri := relation.data[relation.app].get(secret_field):
                    self._register_secret_to_relation(
                        relation.name, relation.id, secret_uri, group
                    )

    def _is_resource_created_for_relation(self, relation: Relation) -> bool:
        if not relation.app:
            return False

        data = self.fetch_relation_data([relation.id], ["username", "password"]).get(
            relation.id, {}
        )
        return bool(data.get("username")) and bool(data.get("password"))

    def is_resource_created(self, relation_id: Optional[int] = None) -> bool:
        """Check if the resource has been created.

        This function can be used to check if the Provider answered with data in the charm code
        when outside an event callback.

        Args:
            relation_id (int, optional): When provided the check is done only for the relation id
                provided, otherwise the check is done for all relations

        Returns:
            True or False

        Raises:
            IndexError: If relation_id is provided but that relation does not exist
        """
        if relation_id is not None:
            try:
                relation = [relation for relation in self.relations if relation.id == relation_id][
                    0
                ]
                return self._is_resource_created_for_relation(relation)
            except IndexError:
                raise IndexError(f"relation id {relation_id} cannot be accessed")
        else:
            return (
                all(
                    self._is_resource_created_for_relation(relation) for relation in self.relations
                )
                if self.relations
                else False
            )

    # Event handlers

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        """Event emitted when the relation is created."""
        if not self.local_unit.is_leader():
            return

        if self.secret_fields:
            set_encoded_field(
                event.relation, self.charm.app, REQ_SECRET_FIELDS, self.secret_fields
            )

    @abstractmethod
    def _on_secret_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation data has changed."""
        raise NotImplementedError

    # Mandatory internal overrides

    @juju_secrets_only
    def _get_relation_secret(
        self, relation_id: int, group: SecretGroup, relation_name: Optional[str] = None
    ) -> Optional[CachedSecret]:
        """Retrieve a Juju Secret that's been stored in the relation databag."""
        if not relation_name:
            relation_name = self.relation_name

        label = self._generate_secret_label(relation_name, relation_id, group)
        return self.secrets.get(label)

    def _fetch_specific_relation_data(
        self, relation, fields: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """Fetching Requires data -- that may include secrets."""
        if not relation.app:
            return {}
        return self._fetch_relation_data_with_secrets(
            relation.app, self.secret_fields, relation, fields
        )

    def _fetch_my_specific_relation_data(self, relation, fields: Optional[List[str]]) -> dict:
        """Fetching our own relation data."""
        return self._fetch_relation_data_without_secrets(self.local_app, relation, fields)

    def _update_relation_data(self, relation: Relation, data: dict) -> None:
        """Updates a set of key-value pairs in the relation.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation: the particular relation.
            data: dict containing the key-value pairs
                that should be updated in the relation.
        """
        return self._update_relation_data_without_secrets(self.local_app, relation, data)

    def _delete_relation_data(self, relation: Relation, fields: List[str]) -> None:
        """Deletes a set of fields from the relation.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation: the particular relation.
            fields: list containing the field names that should be removed from the relation.
        """
        return self._delete_relation_data_without_secrets(self.local_app, relation, fields)

    # Public functions -- inherited

    fetch_my_relation_data = leader_only(DataRelation.fetch_my_relation_data)
    fetch_my_relation_field = leader_only(DataRelation.fetch_my_relation_field)


# Base DataPeer


class DataPeer(DataRequires, DataProvides):
    """Represents peer relations."""

    SECRET_FIELDS = ["operator-password"]
    SECRET_FIELD_NAME = "internal_secret"
    SECRET_LABEL_MAP = {}

    def __init__(
        self,
        charm,
        relation_name: str,
        extra_user_roles: Optional[str] = None,
        additional_secret_fields: Optional[List[str]] = [],
        secret_field_name: Optional[str] = None,
        deleted_label: Optional[str] = None,
    ):
        """Manager of base client relations."""
        DataRequires.__init__(
            self, charm, relation_name, extra_user_roles, additional_secret_fields
        )
        self.secret_field_name = secret_field_name if secret_field_name else self.SECRET_FIELD_NAME
        self.deleted_label = deleted_label

    @property
    def scope(self) -> Optional[Scope]:
        """Turn component information into Scope."""
        if isinstance(self.component, Application):
            return Scope.APP
        if isinstance(self.component, Unit):
            return Scope.UNIT

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation has changed."""
        pass

    def _on_secret_changed_event(self, event: SecretChangedEvent) -> None:
        """Event emitted when the secret has changed."""
        pass

    def _generate_secret_label(
        self, relation_name: str, relation_id: int, group_mapping: SecretGroup
    ) -> str:
        members = [self.charm.app.name]
        if self.scope:
            members.append(self.scope.value)
        return f"{'.'.join(members)}"

    def _generate_secret_field_name(self, group_mapping: SecretGroup = SecretGroup.EXTRA) -> str:
        """Generate unique group_mappings for secrets within a relation context."""
        return f"{self.secret_field_name}"

    @juju_secrets_only
    def _get_relation_secret(
        self,
        relation_id: int,
        group_mapping: SecretGroup = SecretGroup.EXTRA,
        relation_name: Optional[str] = None,
    ) -> Optional[CachedSecret]:
        """Retrieve a Juju Secret specifically for peer relations.

        In case this code may be executed within a rolling upgrade, and we may need to
        migrate secrets from the databag to labels, we make sure to stick the correct
        label on the secret, and clean up the local databag.
        """
        if not relation_name:
            relation_name = self.relation_name

        relation = self.charm.model.get_relation(relation_name, relation_id)
        if not relation:
            return

        label = self._generate_secret_label(relation_name, relation_id, group_mapping)
        secret_uri = relation.data[self.component].get(self._generate_secret_field_name(), None)

        # Fetching the secret with fallback to URI (in case label is not yet known)
        # Label would we "stuck" on the secret in case it is found
        secret = self.secrets.get(label, secret_uri)

        # Either app scope secret with leader executing, or unit scope secret
        leader_or_unit_scope = self.component != self.local_app or self.local_unit.is_leader()
        if secret_uri and secret and leader_or_unit_scope:
            # Databag reference to the secret URI can be removed, now that it's labelled
            relation.data[self.component].pop(self._generate_secret_field_name(), None)
        return secret

    def _get_group_secret_contents(
        self,
        relation: Relation,
        group: SecretGroup,
        secret_fields: Optional[Union[Set[str], List[str]]] = None,
    ) -> Dict[str, str]:
        """Helper function to retrieve collective, requested contents of a secret."""
        result = super()._get_group_secret_contents(relation, group, secret_fields)
        if not self.deleted_label:
            return result
        return {key: result[key] for key in result if result[key] != self.deleted_label}

    def _remove_secret_from_databag(self, relation, fields: List[str]) -> None:
        """For Rolling Upgrades -- when moving from databag to secrets usage.

        Practically what happens here is to remove stuff from the databag that is
        to be stored in secrets.
        """
        if not self.secret_fields:
            return

        secret_fields_passed = set(self.secret_fields) & set(fields)
        for field in secret_fields_passed:
            if self._fetch_relation_data_without_secrets(self.component, relation, [field]):
                self._delete_relation_data_without_secrets(self.component, relation, [field])

    def _fetch_specific_relation_data(
        self, relation: Relation, fields: Optional[List[str]]
    ) -> Dict[str, str]:
        """Fetch data available (directily or indirectly -- i.e. secrets) from the relation."""
        return self._fetch_relation_data_with_secrets(
            self.component, self.secret_fields, relation, fields
        )

    def _fetch_my_specific_relation_data(
        self, relation: Relation, fields: Optional[List[str]]
    ) -> Dict[str, str]:
        """Fetch data available (directily or indirectly -- i.e. secrets) from the relation for owner/this_app."""
        return self._fetch_relation_data_with_secrets(
            self.component, self.secret_fields, relation, fields
        )

    def _update_relation_data(self, relation: Relation, data: Dict[str, str]) -> None:
        """Update data available (directily or indirectly -- i.e. secrets) from the relation for owner/this_app."""
        self._remove_secret_from_databag(relation, list(data.keys()))
        _, normal_fields = self._process_secret_fields(
            relation,
            self.secret_fields,
            list(data),
            self._add_or_update_relation_secrets,
            data=data,
            uri_to_databag=False,
        )

        normal_content = {k: v for k, v in data.items() if k in normal_fields}
        self._update_relation_data_without_secrets(self.component, relation, normal_content)

    def _delete_relation_data(self, relation: Relation, fields: List[str]) -> None:
        """Delete data available (directily or indirectly -- i.e. secrets) from the relation for owner/this_app."""
        if self.secret_fields and self.deleted_label:
            current_data = self.fetch_my_relation_data([relation.id], fields)
            if current_data is not None:
                # Check if the secret we wanna delete actually exists
                # Given the "deleted label", here we can't rely on the default mechanism (i.e. 'key not found')
                if non_existent := (set(fields) & set(self.secret_fields)) - set(
                    current_data.get(relation.id, [])
                ):
                    logger.error(
                        "Non-existing secret %s was attempted to be removed.",
                        ", ".join(non_existent),
                    )

            _, normal_fields = self._process_secret_fields(
                relation,
                self.secret_fields,
                fields,
                self._update_relation_secret,
                data={field: self.deleted_label for field in fields},
            )
        else:
            _, normal_fields = self._process_secret_fields(
                relation, self.secret_fields, fields, self._delete_relation_secret, fields=fields
            )
        self._delete_relation_data_without_secrets(self.component, relation, list(normal_fields))

    def fetch_relation_data(
        self,
        relation_ids: Optional[List[int]] = None,
        fields: Optional[List[str]] = None,
        relation_name: Optional[str] = None,
    ) -> Dict[int, Dict[str, str]]:
        """This method makes no sense for a Peer Relation."""
        raise NotImplementedError(
            "Peer Relation only supports 'self-side' fetch methods: "
            "fetch_my_relation_data() and fetch_my_relation_field()"
        )

    def fetch_relation_field(
        self, relation_id: int, field: str, relation_name: Optional[str] = None
    ) -> Optional[str]:
        """This method makes no sense for a Peer Relation."""
        raise NotImplementedError(
            "Peer Relation only supports 'self-side' fetch methods: "
            "fetch_my_relation_data() and fetch_my_relation_field()"
        )

    # Public functions -- inherited

    fetch_my_relation_data = DataRelation.fetch_my_relation_data
    fetch_my_relation_field = DataRelation.fetch_my_relation_field


class DataPeerUnit(DataPeer):
    """Unit databag representation."""

    SCOPE = Scope.UNIT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


# General events


class ExtraRoleEvent(RelationEvent):
    """Base class for data events."""

    @property
    def extra_user_roles(self) -> Optional[str]:
        """Returns the extra user roles that were requested."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("extra-user-roles")


class RelationEventWithSecret(RelationEvent):
    """Base class for Relation Events that need to handle secrets."""

    @property
    def _secrets(self) -> dict:
        """Caching secrets to avoid fetching them each time a field is referrd.

        DON'T USE the encapsulated helper variable outside of this function
        """
        if not hasattr(self, "_cached_secrets"):
            self._cached_secrets = {}
        return self._cached_secrets

    def _get_secret(self, group) -> Optional[Dict[str, str]]:
        """Retrieveing secrets."""
        if not self.app:
            return
        if not self._secrets.get(group):
            self._secrets[group] = None
            secret_field = f"{PROV_SECRET_PREFIX}{group}"
            if secret_uri := self.relation.data[self.app].get(secret_field):
                secret = self.framework.model.get_secret(id=secret_uri)
                self._secrets[group] = secret.get_content()
        return self._secrets[group]

    @property
    def secrets_enabled(self):
        """Is this Juju version allowing for Secrets usage?"""
        return JujuVersion.from_environ().has_secrets


class AuthenticationEvent(RelationEventWithSecret):
    """Base class for authentication fields for events.

    The amount of logic added here is not ideal -- but this was the only way to preserve
    the interface when moving to Juju Secrets
    """

    @property
    def username(self) -> Optional[str]:
        """Returns the created username."""
        if not self.relation.app:
            return None

        if self.secrets_enabled:
            secret = self._get_secret("user")
            if secret:
                return secret.get("username")

        return self.relation.data[self.relation.app].get("username")

    @property
    def password(self) -> Optional[str]:
        """Returns the password for the created user."""
        if not self.relation.app:
            return None

        if self.secrets_enabled:
            secret = self._get_secret("user")
            if secret:
                return secret.get("password")

        return self.relation.data[self.relation.app].get("password")

    @property
    def tls(self) -> Optional[str]:
        """Returns whether TLS is configured."""
        if not self.relation.app:
            return None

        if self.secrets_enabled:
            secret = self._get_secret("tls")
            if secret:
                return secret.get("tls")

        return self.relation.data[self.relation.app].get("tls")

    @property
    def tls_ca(self) -> Optional[str]:
        """Returns TLS CA."""
        if not self.relation.app:
            return None

        if self.secrets_enabled:
            secret = self._get_secret("tls")
            if secret:
                return secret.get("tls-ca")

        return self.relation.data[self.relation.app].get("tls-ca")


# Database related events and fields


class DatabaseProvidesEvent(RelationEvent):
    """Base class for database events."""

    @property
    def database(self) -> Optional[str]:
        """Returns the database that was requested."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("database")


class DatabaseRequestedEvent(DatabaseProvidesEvent, ExtraRoleEvent):
    """Event emitted when a new database is requested for use on this relation."""


class DatabaseProvidesEvents(CharmEvents):
    """Database events.

    This class defines the events that the database can emit.
    """

    database_requested = EventSource(DatabaseRequestedEvent)


class DatabaseRequiresEvent(RelationEventWithSecret):
    """Base class for database events."""

    @property
    def database(self) -> Optional[str]:
        """Returns the database name."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("database")

    @property
    def endpoints(self) -> Optional[str]:
        """Returns a comma separated list of read/write endpoints.

        In VM charms, this is the primary's address.
        In kubernetes charms, this is the service to the primary pod.
        """
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("endpoints")

    @property
    def read_only_endpoints(self) -> Optional[str]:
        """Returns a comma separated list of read only endpoints.

        In VM charms, this is the address of all the secondary instances.
        In kubernetes charms, this is the service to all replica pod instances.
        """
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("read-only-endpoints")

    @property
    def replset(self) -> Optional[str]:
        """Returns the replicaset name.

        MongoDB only.
        """
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("replset")

    @property
    def uris(self) -> Optional[str]:
        """Returns the connection URIs.

        MongoDB, Redis, OpenSearch.
        """
        if not self.relation.app:
            return None

        if self.secrets_enabled:
            secret = self._get_secret("user")
            if secret:
                return secret.get("uris")

        return self.relation.data[self.relation.app].get("uris")

    @property
    def version(self) -> Optional[str]:
        """Returns the version of the database.

        Version as informed by the database daemon.
        """
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("version")


class DatabaseCreatedEvent(AuthenticationEvent, DatabaseRequiresEvent):
    """Event emitted when a new database is created for use on this relation."""


class DatabaseEndpointsChangedEvent(AuthenticationEvent, DatabaseRequiresEvent):
    """Event emitted when the read/write endpoints are changed."""


class DatabaseReadOnlyEndpointsChangedEvent(AuthenticationEvent, DatabaseRequiresEvent):
    """Event emitted when the read only endpoints are changed."""


class DatabaseRequiresEvents(CharmEvents):
    """Database events.

    This class defines the events that the database can emit.
    """

    database_created = EventSource(DatabaseCreatedEvent)
    endpoints_changed = EventSource(DatabaseEndpointsChangedEvent)
    read_only_endpoints_changed = EventSource(DatabaseReadOnlyEndpointsChangedEvent)


# Database Provider and Requires


class DatabaseProvides(DataProvides):
    """Provider-side of the database relations."""

    on = DatabaseProvidesEvents()  # pyright: ignore [reportAssignmentType]

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation has changed."""
        # Leader only
        if not self.local_unit.is_leader():
            return
        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Emit a database requested event if the setup key (database name and optional
        # extra user roles) was added to the relation databag by the application.
        if "database" in diff.added:
            getattr(self.on, "database_requested").emit(
                event.relation, app=event.app, unit=event.unit
            )

    def set_database(self, relation_id: int, database_name: str) -> None:
        """Set database name.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation_id: the identifier for a particular relation.
            database_name: database name.
        """
        self.update_relation_data(relation_id, {"database": database_name})

    def set_endpoints(self, relation_id: int, connection_strings: str) -> None:
        """Set database primary connections.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        In VM charms, only the primary's address should be passed as an endpoint.
        In kubernetes charms, the service endpoint to the primary pod should be
        passed as an endpoint.

        Args:
            relation_id: the identifier for a particular relation.
            connection_strings: database hosts and ports comma separated list.
        """
        self.update_relation_data(relation_id, {"endpoints": connection_strings})

    def set_read_only_endpoints(self, relation_id: int, connection_strings: str) -> None:
        """Set database replicas connection strings.

        This function writes in the application data bag, therefore,
        only the leader unit can call it.

        Args:
            relation_id: the identifier for a particular relation.
            connection_strings: database hosts and ports comma separated list.
        """
        self.update_relation_data(relation_id, {"read-only-endpoints": connection_strings})

    def set_replset(self, relation_id: int, replset: str) -> None:
        """Set replica set name in the application relation databag.

        MongoDB only.

        Args:
            relation_id: the identifier for a particular relation.
            replset: replica set name.
        """
        self.update_relation_data(relation_id, {"replset": replset})

    def set_uris(self, relation_id: int, uris: str) -> None:
        """Set the database connection URIs in the application relation databag.

        MongoDB, Redis, and OpenSearch only.

        Args:
            relation_id: the identifier for a particular relation.
            uris: connection URIs.
        """
        self.update_relation_data(relation_id, {"uris": uris})

    def set_version(self, relation_id: int, version: str) -> None:
        """Set the database version in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            version: database version.
        """
        self.update_relation_data(relation_id, {"version": version})


class DatabaseRequires(DataRequires):
    """Requires-side of the database relation."""

    on = DatabaseRequiresEvents()  # pyright: ignore [reportAssignmentType]

    def __init__(
        self,
        charm,
        relation_name: str,
        database_name: str,
        extra_user_roles: Optional[str] = None,
        relations_aliases: Optional[List[str]] = None,
        additional_secret_fields: Optional[List[str]] = [],
    ):
        """Manager of database client relations."""
        super().__init__(charm, relation_name, extra_user_roles, additional_secret_fields)
        self.database = database_name
        self.relations_aliases = relations_aliases

        # Define custom event names for each alias.
        if relations_aliases:
            # Ensure the number of aliases does not exceed the maximum
            # of connections allowed in the specific relation.
            relation_connection_limit = self.charm.meta.requires[relation_name].limit
            if len(relations_aliases) != relation_connection_limit:
                raise ValueError(
                    f"The number of aliases must match the maximum number of connections allowed in the relation. "
                    f"Expected {relation_connection_limit}, got {len(relations_aliases)}"
                )

            for relation_alias in relations_aliases:
                self.on.define_event(f"{relation_alias}_database_created", DatabaseCreatedEvent)
                self.on.define_event(
                    f"{relation_alias}_endpoints_changed", DatabaseEndpointsChangedEvent
                )
                self.on.define_event(
                    f"{relation_alias}_read_only_endpoints_changed",
                    DatabaseReadOnlyEndpointsChangedEvent,
                )

    def _on_secret_changed_event(self, event: SecretChangedEvent):
        """Event notifying about a new value of a secret."""
        pass

    def _assign_relation_alias(self, relation_id: int) -> None:
        """Assigns an alias to a relation.

        This function writes in the unit data bag.

        Args:
            relation_id: the identifier for a particular relation.
        """
        # If no aliases were provided, return immediately.
        if not self.relations_aliases:
            return

        # Return if an alias was already assigned to this relation
        # (like when there are more than one unit joining the relation).
        relation = self.charm.model.get_relation(self.relation_name, relation_id)
        if relation and relation.data[self.local_unit].get("alias"):
            return

        # Retrieve the available aliases (the ones that weren't assigned to any relation).
        available_aliases = self.relations_aliases[:]
        for relation in self.charm.model.relations[self.relation_name]:
            alias = relation.data[self.local_unit].get("alias")
            if alias:
                logger.debug("Alias %s was already assigned to relation %d", alias, relation.id)
                available_aliases.remove(alias)

        # Set the alias in the unit relation databag of the specific relation.
        relation = self.charm.model.get_relation(self.relation_name, relation_id)
        if relation:
            relation.data[self.local_unit].update({"alias": available_aliases[0]})

        # We need to set relation alias also on the application level so,
        # it will be accessible in show-unit juju command, executed for a consumer application unit
        if self.local_unit.is_leader():
            self.update_relation_data(relation_id, {"alias": available_aliases[0]})

    def _emit_aliased_event(self, event: RelationChangedEvent, event_name: str) -> None:
        """Emit an aliased event to a particular relation if it has an alias.

        Args:
            event: the relation changed event that was received.
            event_name: the name of the event to emit.
        """
        alias = self._get_relation_alias(event.relation.id)
        if alias:
            getattr(self.on, f"{alias}_{event_name}").emit(
                event.relation, app=event.app, unit=event.unit
            )

    def _get_relation_alias(self, relation_id: int) -> Optional[str]:
        """Returns the relation alias.

        Args:
            relation_id: the identifier for a particular relation.

        Returns:
            the relation alias or None if the relation was not found.
        """
        for relation in self.charm.model.relations[self.relation_name]:
            if relation.id == relation_id:
                return relation.data[self.local_unit].get("alias")
        return None

    def is_postgresql_plugin_enabled(self, plugin: str, relation_index: int = 0) -> bool:
        """Returns whether a plugin is enabled in the database.

        Args:
            plugin: name of the plugin to check.
            relation_index: optional relation index to check the database
                (default: 0 - first relation).

        PostgreSQL only.
        """
        # Psycopg 3 is imported locally to avoid the need of its package installation
        # when relating to a database charm other than PostgreSQL.
        import psycopg

        # Return False if no relation is established.
        if len(self.relations) == 0:
            return False

        relation_id = self.relations[relation_index].id
        host = self.fetch_relation_field(relation_id, "endpoints")

        # Return False if there is no endpoint available.
        if host is None:
            return False

        host = host.split(":")[0]

        content = self.fetch_relation_data([relation_id], ["username", "password"]).get(
            relation_id, {}
        )
        user = content.get("username")
        password = content.get("password")

        connection_string = (
            f"host='{host}' dbname='{self.database}' user='{user}' password='{password}'"
        )
        try:
            with psycopg.connect(connection_string) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT TRUE FROM pg_extension WHERE extname=%s::text;", (plugin,)
                    )
                    return cursor.fetchone() is not None
        except psycopg.Error as e:
            logger.exception(
                f"failed to check whether {plugin} plugin is enabled in the database: %s", str(e)
            )
            return False

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        """Event emitted when the database relation is created."""
        super()._on_relation_created_event(event)

        # If relations aliases were provided, assign one to the relation.
        self._assign_relation_alias(event.relation.id)

        # Sets both database and extra user roles in the relation
        # if the roles are provided. Otherwise, sets only the database.
        if not self.local_unit.is_leader():
            return

        if self.extra_user_roles:
            self.update_relation_data(
                event.relation.id,
                {
                    "database": self.database,
                    "extra-user-roles": self.extra_user_roles,
                },
            )
        else:
            self.update_relation_data(event.relation.id, {"database": self.database})

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the database relation has changed."""
        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Register all new secrets with their labels
        if any(newval for newval in diff.added if self._is_secret_field(newval)):
            self._register_secrets_to_relation(event.relation, diff.added)

        # Check if the database is created
        # (the database charm shared the credentials).
        secret_field_user = self._generate_secret_field_name(SecretGroup.USER)
        if (
            "username" in diff.added and "password" in diff.added
        ) or secret_field_user in diff.added:
            # Emit the default event (the one without an alias).
            logger.info("database created at %s", datetime.now())
            getattr(self.on, "database_created").emit(
                event.relation, app=event.app, unit=event.unit
            )

            # Emit the aliased event (if any).
            self._emit_aliased_event(event, "database_created")

            # To avoid unnecessary application restarts do not trigger
            # “endpoints_changed“ event if “database_created“ is triggered.
            return

        # Emit an endpoints changed event if the database
        # added or changed this info in the relation databag.
        if "endpoints" in diff.added or "endpoints" in diff.changed:
            # Emit the default event (the one without an alias).
            logger.info("endpoints changed on %s", datetime.now())
            getattr(self.on, "endpoints_changed").emit(
                event.relation, app=event.app, unit=event.unit
            )

            # Emit the aliased event (if any).
            self._emit_aliased_event(event, "endpoints_changed")

            # To avoid unnecessary application restarts do not trigger
            # “read_only_endpoints_changed“ event if “endpoints_changed“ is triggered.
            return

        # Emit a read only endpoints changed event if the database
        # added or changed this info in the relation databag.
        if "read-only-endpoints" in diff.added or "read-only-endpoints" in diff.changed:
            # Emit the default event (the one without an alias).
            logger.info("read-only-endpoints changed on %s", datetime.now())
            getattr(self.on, "read_only_endpoints_changed").emit(
                event.relation, app=event.app, unit=event.unit
            )

            # Emit the aliased event (if any).
            self._emit_aliased_event(event, "read_only_endpoints_changed")


# Kafka related events


class KafkaProvidesEvent(RelationEvent):
    """Base class for Kafka events."""

    @property
    def topic(self) -> Optional[str]:
        """Returns the topic that was requested."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("topic")

    @property
    def consumer_group_prefix(self) -> Optional[str]:
        """Returns the consumer-group-prefix that was requested."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("consumer-group-prefix")


class TopicRequestedEvent(KafkaProvidesEvent, ExtraRoleEvent):
    """Event emitted when a new topic is requested for use on this relation."""


class KafkaProvidesEvents(CharmEvents):
    """Kafka events.

    This class defines the events that the Kafka can emit.
    """

    topic_requested = EventSource(TopicRequestedEvent)


class KafkaRequiresEvent(RelationEvent):
    """Base class for Kafka events."""

    @property
    def topic(self) -> Optional[str]:
        """Returns the topic."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("topic")

    @property
    def bootstrap_server(self) -> Optional[str]:
        """Returns a comma-separated list of broker uris."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("endpoints")

    @property
    def consumer_group_prefix(self) -> Optional[str]:
        """Returns the consumer-group-prefix."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("consumer-group-prefix")

    @property
    def zookeeper_uris(self) -> Optional[str]:
        """Returns a comma separated list of Zookeeper uris."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("zookeeper-uris")


class TopicCreatedEvent(AuthenticationEvent, KafkaRequiresEvent):
    """Event emitted when a new topic is created for use on this relation."""


class BootstrapServerChangedEvent(AuthenticationEvent, KafkaRequiresEvent):
    """Event emitted when the bootstrap server is changed."""


class KafkaRequiresEvents(CharmEvents):
    """Kafka events.

    This class defines the events that the Kafka can emit.
    """

    topic_created = EventSource(TopicCreatedEvent)
    bootstrap_server_changed = EventSource(BootstrapServerChangedEvent)


# Kafka Provides and Requires


class KafkaProvides(DataProvides):
    """Provider-side of the Kafka relation."""

    on = KafkaProvidesEvents()  # pyright: ignore [reportAssignmentType]

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation has changed."""
        # Leader only
        if not self.local_unit.is_leader():
            return

        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Emit a topic requested event if the setup key (topic name and optional
        # extra user roles) was added to the relation databag by the application.
        if "topic" in diff.added:
            getattr(self.on, "topic_requested").emit(
                event.relation, app=event.app, unit=event.unit
            )

    def set_topic(self, relation_id: int, topic: str) -> None:
        """Set topic name in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            topic: the topic name.
        """
        self.update_relation_data(relation_id, {"topic": topic})

    def set_bootstrap_server(self, relation_id: int, bootstrap_server: str) -> None:
        """Set the bootstrap server in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            bootstrap_server: the bootstrap server address.
        """
        self.update_relation_data(relation_id, {"endpoints": bootstrap_server})

    def set_consumer_group_prefix(self, relation_id: int, consumer_group_prefix: str) -> None:
        """Set the consumer group prefix in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            consumer_group_prefix: the consumer group prefix string.
        """
        self.update_relation_data(relation_id, {"consumer-group-prefix": consumer_group_prefix})

    def set_zookeeper_uris(self, relation_id: int, zookeeper_uris: str) -> None:
        """Set the zookeeper uris in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            zookeeper_uris: comma-separated list of ZooKeeper server uris.
        """
        self.update_relation_data(relation_id, {"zookeeper-uris": zookeeper_uris})


class KafkaRequires(DataRequires):
    """Requires-side of the Kafka relation."""

    on = KafkaRequiresEvents()  # pyright: ignore [reportAssignmentType]

    def __init__(
        self,
        charm,
        relation_name: str,
        topic: str,
        extra_user_roles: Optional[str] = None,
        consumer_group_prefix: Optional[str] = None,
        additional_secret_fields: Optional[List[str]] = [],
    ):
        """Manager of Kafka client relations."""
        # super().__init__(charm, relation_name)
        super().__init__(charm, relation_name, extra_user_roles, additional_secret_fields)
        self.charm = charm
        self.topic = topic
        self.consumer_group_prefix = consumer_group_prefix or ""

    @property
    def topic(self):
        """Topic to use in Kafka."""
        return self._topic

    @topic.setter
    def topic(self, value):
        # Avoid wildcards
        if value == "*":
            raise ValueError(f"Error on topic '{value}', cannot be a wildcard.")
        self._topic = value

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        """Event emitted when the Kafka relation is created."""
        super()._on_relation_created_event(event)

        if not self.local_unit.is_leader():
            return

        # Sets topic, extra user roles, and "consumer-group-prefix" in the relation
        relation_data = {
            f: getattr(self, f.replace("-", "_"), "")
            for f in ["consumer-group-prefix", "extra-user-roles", "topic"]
        }

        self.update_relation_data(event.relation.id, relation_data)

    def _on_secret_changed_event(self, event: SecretChangedEvent):
        """Event notifying about a new value of a secret."""
        pass

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the Kafka relation has changed."""
        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Check if the topic is created
        # (the Kafka charm shared the credentials).

        # Register all new secrets with their labels
        if any(newval for newval in diff.added if self._is_secret_field(newval)):
            self._register_secrets_to_relation(event.relation, diff.added)

        secret_field_user = self._generate_secret_field_name(SecretGroup.USER)
        if (
            "username" in diff.added and "password" in diff.added
        ) or secret_field_user in diff.added:
            # Emit the default event (the one without an alias).
            logger.info("topic created at %s", datetime.now())
            getattr(self.on, "topic_created").emit(event.relation, app=event.app, unit=event.unit)

            # To avoid unnecessary application restarts do not trigger
            # “endpoints_changed“ event if “topic_created“ is triggered.
            return

        # Emit an endpoints (bootstrap-server) changed event if the Kafka endpoints
        # added or changed this info in the relation databag.
        if "endpoints" in diff.added or "endpoints" in diff.changed:
            # Emit the default event (the one without an alias).
            logger.info("endpoints changed on %s", datetime.now())
            getattr(self.on, "bootstrap_server_changed").emit(
                event.relation, app=event.app, unit=event.unit
            )  # here check if this is the right design
            return


# Opensearch related events


class OpenSearchProvidesEvent(RelationEvent):
    """Base class for OpenSearch events."""

    @property
    def index(self) -> Optional[str]:
        """Returns the index that was requested."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("index")


class IndexRequestedEvent(OpenSearchProvidesEvent, ExtraRoleEvent):
    """Event emitted when a new index is requested for use on this relation."""


class OpenSearchProvidesEvents(CharmEvents):
    """OpenSearch events.

    This class defines the events that OpenSearch can emit.
    """

    index_requested = EventSource(IndexRequestedEvent)


class OpenSearchRequiresEvent(DatabaseRequiresEvent):
    """Base class for OpenSearch requirer events."""


class IndexCreatedEvent(AuthenticationEvent, OpenSearchRequiresEvent):
    """Event emitted when a new index is created for use on this relation."""


class OpenSearchRequiresEvents(CharmEvents):
    """OpenSearch events.

    This class defines the events that the opensearch requirer can emit.
    """

    index_created = EventSource(IndexCreatedEvent)
    endpoints_changed = EventSource(DatabaseEndpointsChangedEvent)
    authentication_updated = EventSource(AuthenticationEvent)


# OpenSearch Provides and Requires Objects


class OpenSearchProvides(DataProvides):
    """Provider-side of the OpenSearch relation."""

    on = OpenSearchProvidesEvents()  # pyright: ignore[reportAssignmentType]

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation has changed."""
        # Leader only
        if not self.local_unit.is_leader():
            return
        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Emit an index requested event if the setup key (index name and optional extra user roles)
        # have been added to the relation databag by the application.
        if "index" in diff.added:
            getattr(self.on, "index_requested").emit(
                event.relation, app=event.app, unit=event.unit
            )

    def set_index(self, relation_id: int, index: str) -> None:
        """Set the index in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            index: the index as it is _created_ on the provider charm. This needn't match the
                requested index, and can be used to present a different index name if, for example,
                the requested index is invalid.
        """
        self.update_relation_data(relation_id, {"index": index})

    def set_endpoints(self, relation_id: int, endpoints: str) -> None:
        """Set the endpoints in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            endpoints: the endpoint addresses for opensearch nodes.
        """
        self.update_relation_data(relation_id, {"endpoints": endpoints})

    def set_version(self, relation_id: int, version: str) -> None:
        """Set the opensearch version in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            version: database version.
        """
        self.update_relation_data(relation_id, {"version": version})


class OpenSearchRequires(DataRequires):
    """Requires-side of the OpenSearch relation."""

    on = OpenSearchRequiresEvents()  # pyright: ignore[reportAssignmentType]

    def __init__(
        self,
        charm,
        relation_name: str,
        index: str,
        extra_user_roles: Optional[str] = None,
        additional_secret_fields: Optional[List[str]] = [],
    ):
        """Manager of OpenSearch client relations."""
        super().__init__(charm, relation_name, extra_user_roles, additional_secret_fields)
        self.charm = charm
        self.index = index

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        """Event emitted when the OpenSearch relation is created."""
        super()._on_relation_created_event(event)

        if not self.local_unit.is_leader():
            return

        # Sets both index and extra user roles in the relation if the roles are provided.
        # Otherwise, sets only the index.
        data = {"index": self.index}
        if self.extra_user_roles:
            data["extra-user-roles"] = self.extra_user_roles

        self.update_relation_data(event.relation.id, data)

    def _on_secret_changed_event(self, event: SecretChangedEvent):
        """Event notifying about a new value of a secret."""
        if not event.secret.label:
            return

        relation = self._relation_from_secret_label(event.secret.label)
        if not relation:
            logging.info(
                f"Received secret {event.secret.label} but couldn't parse, seems irrelevant"
            )
            return

        if relation.app == self.charm.app:
            logging.info("Secret changed event ignored for Secret Owner")

        remote_unit = None
        for unit in relation.units:
            if unit.app != self.charm.app:
                remote_unit = unit

        logger.info("authentication updated")
        getattr(self.on, "authentication_updated").emit(
            relation, app=relation.app, unit=remote_unit
        )

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the OpenSearch relation has changed.

        This event triggers individual custom events depending on the changing relation.
        """
        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Register all new secrets with their labels
        if any(newval for newval in diff.added if self._is_secret_field(newval)):
            self._register_secrets_to_relation(event.relation, diff.added)

        secret_field_user = self._generate_secret_field_name(SecretGroup.USER)
        secret_field_tls = self._generate_secret_field_name(SecretGroup.TLS)
        updates = {"username", "password", "tls", "tls-ca", secret_field_user, secret_field_tls}
        if len(set(diff._asdict().keys()) - updates) < len(diff):
            logger.info("authentication updated at: %s", datetime.now())
            getattr(self.on, "authentication_updated").emit(
                event.relation, app=event.app, unit=event.unit
            )

        # Check if the index is created
        # (the OpenSearch charm shares the credentials).
        if (
            "username" in diff.added and "password" in diff.added
        ) or secret_field_user in diff.added:
            # Emit the default event (the one without an alias).
            logger.info("index created at: %s", datetime.now())
            getattr(self.on, "index_created").emit(event.relation, app=event.app, unit=event.unit)

            # To avoid unnecessary application restarts do not trigger
            # “endpoints_changed“ event if “index_created“ is triggered.
            return

        # Emit a endpoints changed event if the OpenSearch application added or changed this info
        # in the relation databag.
        if "endpoints" in diff.added or "endpoints" in diff.changed:
            # Emit the default event (the one without an alias).
            logger.info("endpoints changed on %s", datetime.now())
            getattr(self.on, "endpoints_changed").emit(
                event.relation, app=event.app, unit=event.unit
            )  # here check if this is the right design
            return
