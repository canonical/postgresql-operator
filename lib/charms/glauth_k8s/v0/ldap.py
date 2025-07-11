# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""# Juju Charm Library for the `ldap` Juju Interface.

This juju charm library contains the Provider and Requirer classes for handling
the `ldap` interface.

## Requirer Charm

The requirer charm is expected to:

- Provide information for the provider charm to deliver LDAP related
information in the juju integration, in order to communicate with the LDAP
server and authenticate LDAP operations
- Listen to the custom juju event `LdapReadyEvent` to obtain the LDAP
related information from the integration
- Listen to the custom juju event `LdapUnavailableEvent` to handle the
situation when the LDAP integration is broken

```python

from charms.glauth_k8s.v0.ldap import (
    LdapRequirer,
    LdapReadyEvent,
    LdapUnavailableEvent,
)

class RequirerCharm(CharmBase):
    # LDAP requirer charm that integrates with an LDAP provider charm.

    def __init__(self, *args):
        super().__init__(*args)

        self.ldap_requirer = LdapRequirer(self)
        self.framework.observe(
            self.ldap_requirer.on.ldap_ready,
            self._on_ldap_ready,
        )
        self.framework.observe(
            self.ldap_requirer.on.ldap_unavailable,
            self._on_ldap_unavailable,
        )

    def _on_ldap_ready(self, event: LdapReadyEvent) -> None:
        # Consume the LDAP related information
        ldap_data = self.ldap_requirer.consume_ldap_relation_data(
            relation=event.relation,
        )

        # Configure the LDAP requirer charm
        ...

    def _on_ldap_unavailable(self, event: LdapUnavailableEvent) -> None:
        # Handle the situation where the LDAP integration is broken
        ...
```

As shown above, the library offers custom juju events to handle specific
situations, which are listed below:

- ldap_ready: event emitted when the LDAP related information is ready for
requirer charm to use.
- ldap_unavailable: event emitted when the LDAP integration is broken.

Additionally, the requirer charmed operator needs to declare the `ldap`
interface in the `metadata.yaml`:

```yaml
requires:
  ldap:
    interface: ldap
```

## Provider Charm

The provider charm is expected to:

- Use the information provided by the requirer charm to provide LDAP related
information for the requirer charm to connect and authenticate to the LDAP
server
- Listen to the custom juju event `LdapRequestedEvent` to offer LDAP related
information in the integration

```python

from charms.glauth_k8s.v0.ldap import (
    LdapProvider,
    LdapRequestedEvent,
)

class ProviderCharm(CharmBase):
    # LDAP provider charm.

    def __init__(self, *args):
        super().__init__(*args)

        self.ldap_provider = LdapProvider(self)
        self.framework.observe(
            self.ldap_provider.on.ldap_requested,
            self._on_ldap_requested,
        )

    def _on_ldap_requested(self, event: LdapRequestedEvent) -> None:
        # Consume the information provided by the requirer charm
        requirer_data = event.data

        # Prepare the LDAP related information using the requirer's data
        ldap_data = ...

        # Update the integration data
        self.ldap_provider.update_relations_app_data(
            relation.id,
            ldap_data,
        )
```

As shown above, the library offers custom juju events to handle specific
situations, which are listed below:

-  ldap_requested: event emitted when the requirer charm is requesting the
LDAP related information in order to connect and authenticate to the LDAP server
"""

import json
from functools import wraps
from string import Template
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

import ops
from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationEvent,
)
from ops.framework import EventSource, Handle, Object, ObjectEvents
from ops.model import Relation, SecretNotFoundError
from pydantic import StrictBool, ValidationError, version

# The unique CharmHub library identifier, never change it
LIBID = "5a535b3c4d0b40da98e29867128e57b9"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 11

PYDEPS = ["pydantic"]

DEFAULT_RELATION_NAME = "ldap"
BIND_ACCOUNT_SECRET_LABEL_TEMPLATE = Template("relation-$relation_id-bind-account-secret")

PYDANTIC_IS_V1 = int(version.VERSION.split(".")[0]) < 2
if PYDANTIC_IS_V1:
    # Pydantic v1 backwards compatibility logic,
    # see https://docs.pydantic.dev/latest/migration/ for more info.
    # This does not offer complete backwards compatibility

    from pydantic import BaseModel as BaseModelV1
    from pydantic import Field as FieldV1
    from pydantic import validator
    from pydantic.main import ModelMetaclass

    def Field(*args: Any, **kwargs: Any) -> FieldV1:  # noqa N802
        if frozen := kwargs.pop("frozen", None):
            kwargs["allow_mutations"] = not frozen
        return FieldV1(*args, **kwargs)

    def field_validator(*args: Any, **kwargs: Any) -> Callable:
        if kwargs.get("mode") == "before":
            kwargs.pop("mode")
            kwargs["pre"] = True
        return validator(*args, **kwargs)

    encoders_config = {}

    def field_serializer(*fields: str, mode: Optional[str] = None) -> Callable:
        def _field_serializer(f: Callable, *args: Any, **kwargs: Any) -> Callable:
            @wraps(f)
            def wrapper(self: object, *args: Any, **kwargs: Any) -> Any:
                return f(self, *args, **kwargs)

            encoders_config[wrapper] = fields
            return wrapper

        return _field_serializer

    class ModelCompatibilityMeta(ModelMetaclass):
        def __init__(self, name: str, bases: Tuple[object], attrs: Dict) -> None:
            if not hasattr(self, "_encoders"):
                self._encoders = {}

            self._encoders.update({
                encoder: func
                for func in attrs.values()
                if callable(func) and func in encoders_config
                for encoder in encoders_config[func]
            })

            super().__init__(name, bases, attrs)

    class BaseModel(BaseModelV1, metaclass=ModelCompatibilityMeta):
        def model_dump(self, *args: Any, **kwargs: Any) -> Dict:
            d = self.dict(*args, **kwargs)
            for name, f in self._encoders.items():
                d[name] = f(self, d[name])
            return d

else:
    from pydantic import (  # type: ignore[no-redef]
        BaseModel,
        Field,
        field_serializer,
        field_validator,
    )


def leader_unit(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(
        obj: Union["LdapProvider", "LdapRequirer"], *args: Any, **kwargs: Any
    ) -> Optional[Any]:
        if not obj.unit.is_leader():
            return None

        return func(obj, *args, **kwargs)

    return wrapper


@leader_unit
def _update_relation_app_databag(
    ldap: Union["LdapProvider", "LdapRequirer"], relation: Relation, data: dict
) -> None:
    if relation is None:
        return

    data = {k: str(v) if v else "" for k, v in data.items()}
    relation.data[ldap.app].update(data)


class Secret:
    def __init__(self, secret: ops.Secret = None) -> None:
        self._secret: ops.Secret = secret

    @property
    def uri(self) -> str:
        return self._secret.id if self._secret else ""

    @classmethod
    def load(
        cls,
        charm: CharmBase,
        label: str,
    ) -> Optional["Secret"]:
        try:
            secret = charm.model.get_secret(label=label)
        except SecretNotFoundError:
            return None

        return Secret(secret)

    @classmethod
    def create_or_update(cls, charm: CharmBase, label: str, content: dict[str, str]) -> "Secret":
        try:
            secret = charm.model.get_secret(label=label)
            secret.set_content(content=content)
        except SecretNotFoundError:
            secret = charm.app.add_secret(label=label, content=content)

        return Secret(secret)

    def grant(self, relation: Relation) -> None:
        self._secret.grant(relation)

    def remove(self) -> None:
        self._secret.remove_all_revisions()


class LdapProviderBaseData(BaseModel):
    urls: List[str] = Field(frozen=True)
    ldaps_urls: List[str] = Field(frozen=True)
    base_dn: str = Field(frozen=True)
    starttls: StrictBool = Field(frozen=True)

    @field_validator("urls", mode="before")
    @classmethod
    def validate_ldap_urls(cls, vs: List[str] | str) -> List[str]:
        if isinstance(vs, str):
            vs = json.loads(vs)
            if isinstance(vs, str):
                vs = [vs]

        for v in vs:
            if not v.startswith("ldap://"):
                raise ValidationError.from_exception_data("Invalid LDAP URL scheme.")

        return vs

    @field_validator("ldaps_urls", mode="before")
    @classmethod
    def validate_ldaps_urls(cls, vs: List[str] | str) -> List[str]:
        if isinstance(vs, str):
            vs = json.loads(vs)
            if isinstance(vs, str):
                vs = [vs]

        for v in vs:
            if not v.startswith("ldaps://"):
                raise ValidationError.from_exception_data("Invalid LDAPS URL scheme.")

        return vs

    @field_serializer("urls", "ldaps_urls")
    def serialize_list(self, urls: List[str]) -> str:
        return str(json.dumps(urls))

    @field_validator("starttls", mode="before")
    @classmethod
    def deserialize_bool(cls, v: str | bool) -> bool:
        if isinstance(v, str):
            return True if v.casefold() == "true" else False

        return v

    @field_serializer("starttls")
    def serialize_bool(self, starttls: bool) -> str:
        return str(starttls)


class LdapProviderData(LdapProviderBaseData):
    bind_dn: str = Field(frozen=True)
    bind_password: str = Field(exclude=True)
    bind_password_secret: Optional[str] = None
    auth_method: Literal["simple"] = Field(frozen=True)


class LdapRequirerData(BaseModel):
    user: str = Field(frozen=True)
    group: str = Field(frozen=True)


class LdapRequestedEvent(RelationEvent):
    """An event emitted when the LDAP integration is built."""

    def __init__(self, handle: Handle, relation: Relation) -> None:
        super().__init__(handle, relation, relation.app)

    @property
    def data(self) -> Optional[LdapRequirerData]:
        relation_data = self.relation.data.get(self.relation.app)
        return LdapRequirerData(**relation_data) if relation_data else None


class LdapProviderEvents(ObjectEvents):
    ldap_requested = EventSource(LdapRequestedEvent)


class LdapReadyEvent(RelationEvent):
    """An event when the LDAP related information is ready."""


class LdapUnavailableEvent(RelationEvent):
    """An event when the LDAP integration is unavailable."""


class LdapRequirerEvents(ObjectEvents):
    ldap_ready = EventSource(LdapReadyEvent)
    ldap_unavailable = EventSource(LdapUnavailableEvent)


class LdapProvider(Object):
    on = LdapProviderEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
    ) -> None:
        super().__init__(charm, relation_name)

        self.charm = charm
        self.app = charm.app
        self.unit = charm.unit
        self._relation_name = relation_name

        self.framework.observe(
            self.charm.on[self._relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            self.charm.on[self._relation_name].relation_broken,
            self._on_relation_broken,
        )

    @leader_unit
    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the event emitted when the requirer charm provides the necessary data."""
        self.on.ldap_requested.emit(event.relation)

    @leader_unit
    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle the event emitted when the LDAP integration is broken."""
        secret = Secret.load(
            self.charm,
            label=BIND_ACCOUNT_SECRET_LABEL_TEMPLATE.substitute(relation_id=event.relation.id),
        )
        if secret:
            secret.remove()

    def get_bind_password(self, relation_id: int) -> Optional[str]:
        """Retrieve the bind account password for a given integration."""
        try:
            secret = self.charm.model.get_secret(
                label=BIND_ACCOUNT_SECRET_LABEL_TEMPLATE.substitute(relation_id=relation_id)
            )
        except SecretNotFoundError:
            return None
        return secret.get_content().get("password")

    def update_relations_app_data(
        self,
        data: Union[LdapProviderBaseData, LdapProviderData],
        /,
        relation_id: Optional[int] = None,
    ) -> None:
        """An API for the provider charm to provide the LDAP related information."""
        if not (relations := self.charm.model.relations.get(self._relation_name)):
            return

        if relation_id is not None and isinstance(data, LdapProviderData):
            relations = [relation for relation in relations if relation.id == relation_id]
            secret = Secret.create_or_update(
                self.charm,
                BIND_ACCOUNT_SECRET_LABEL_TEMPLATE.substitute(relation_id=relation_id),
                {"password": data.bind_password},
            )
            secret.grant(relations[0])
            data.bind_password_secret = secret.uri

        for relation in relations:
            _update_relation_app_databag(self.charm, relation, data.model_dump())


class LdapRequirer(Object):
    """An LDAP requirer to consume data delivered by an LDAP provider charm."""

    on = LdapRequirerEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
        *,
        data: Optional[LdapRequirerData] = None,
    ) -> None:
        super().__init__(charm, relation_name)

        self.charm = charm
        self.app = charm.app
        self.unit = charm.unit
        self._relation_name = relation_name
        self._data = data

        self.framework.observe(
            self.charm.on[self._relation_name].relation_created,
            self._on_ldap_relation_created,
        )
        self.framework.observe(
            self.charm.on[self._relation_name].relation_changed,
            self._on_ldap_relation_changed,
        )
        self.framework.observe(
            self.charm.on[self._relation_name].relation_broken,
            self._on_ldap_relation_broken,
        )

    def _on_ldap_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle the event emitted when an LDAP integration is created."""
        user = self._data.user if self._data else self.app.name
        group = self._data.group if self._data else self.model.name
        _update_relation_app_databag(self.charm, event.relation, {"user": user, "group": group})

    def _on_ldap_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle the event emitted when the LDAP related information is ready."""
        provider_app = event.relation.app

        if not (provider_data := event.relation.data.get(provider_app)):
            return

        provider_data = dict(provider_data)
        if self._load_provider_data(provider_data):
            self.on.ldap_ready.emit(event.relation)

    def _on_ldap_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle the event emitted when the LDAP integration is broken."""
        self.on.ldap_unavailable.emit(event.relation)

    def _load_provider_data(self, provider_data: dict) -> Optional[LdapProviderData]:
        if secret_id := provider_data.get("bind_password_secret"):
            secret = self.charm.model.get_secret(id=secret_id)
            provider_data["bind_password"] = secret.get_content().get("password")

        try:
            return LdapProviderData(**provider_data)
        except ValidationError:
            return None

    def consume_ldap_relation_data(
        self,
        /,
        relation: Optional[Relation] = None,
        relation_id: Optional[int] = None,
    ) -> Optional[LdapProviderData]:
        """An API for the requirer charm to consume the LDAP related information in the application databag."""
        if not relation:
            relation = self.charm.model.get_relation(self._relation_name, relation_id)

        if not relation:
            return None

        provider_data = dict(relation.data.get(relation.app))
        if not provider_data:
            return None

        return self._load_provider_data(provider_data)

    def _is_relation_active(self, relation: Relation) -> bool:
        """Whether the relation is active based on contained data."""
        try:
            _ = repr(relation.data)
            return True
        except (RuntimeError, ops.ModelError):
            return False

    @property
    def relations(self) -> List[Relation]:
        """The list of Relation instances associated with this relation_name."""
        return [
            relation
            for relation in self.charm.model.relations[self._relation_name]
            if self._is_relation_active(relation)
        ]

    def _ready_for_relation(self, relation: Relation) -> bool:
        if not relation.app:
            return False

        return "urls" in relation.data[relation.app] and "bind_dn" in relation.data[relation.app]

    def ready(self, relation_id: Optional[int] = None) -> bool:
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
        if relation_id is None:
            return (
                all(self._ready_for_relation(relation) for relation in self.relations)
                if self.relations
                else False
            )

        try:
            relation = [relation for relation in self.relations if relation.id == relation_id][0]
            return self._ready_for_relation(relation)
        except IndexError:
            raise IndexError(f"relation id {relation_id} cannot be accessed")
