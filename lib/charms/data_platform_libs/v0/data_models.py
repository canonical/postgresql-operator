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

r"""Library to provide simple API for promoting typed, validated and structured dataclass in charms.

Dict-like data structure are often used in charms. They are used for config, action parameters
and databag. This library aims at providing simple API for using pydantic BaseModel-derived class
in charms, in order to enhance:
* Validation, by embedding custom business logic to validate single parameters or even have
  validators that acts across different fields
* Parsing, by loading data into pydantic object we can both allow for other types (e.g. float) to
  be used in configuration/parameters as well as specify even nested complex objects for databags
* Static typing checks, by moving from dict-like object to classes with typed-annotated properties,
  that can be statically checked using mypy to ensure that the code is correct.

Pydantic models can be used on:

* Charm Configuration (as defined in config.yaml)
* Actions parameters (as defined in actions.yaml)
* Application/Unit Databag Information (thus making it more structured and encoded)


## Creating models

Any data-structure can be modeled using dataclasses instead of dict-like objects (e.g.  storing
config, action parameters and databags). Within pydantic, we can define dataclasses that provides
also parsing and validation on standard dataclass implementation:

```python

from charms.data_platform_libs.v0.data_models import BaseConfigModel

class MyConfig(BaseConfigModel):

    my_key: int

    @validator("my_key")
     def is_lower_than_100(cls, v: int):
         if v > 100:
             raise ValueError("Too high")

```

This should allow to collapse both parsing and validation as the dataclass object is parsed and
created:

```python
dataclass = MyConfig(my_key="1")

dataclass.my_key # this returns 1 (int)
dataclass["my_key"] # this returns 1 (int)

dataclass = MyConfig(my_key="102") # this returns a ValueError("Too High")
```

## Charm Configuration Model

Using the class above, we can implement parsing and validation of configuration by simply
extending our charms using the `TypedCharmBase` class, as shown below.

```python
class MyCharm(TypedCharmBase[MyConfig]):
    config_type = MyConfig

     # everywhere in the code you will have config property already parsed and validate
     def my_method(self):
         self.config: MyConfig
```

## Action parameters

In order to parse action parameters, we can use a decorator to be applied to action event
callbacks, as shown below.

```python
@validate_params(PullActionModel)
def _pull_site_action(
    self, event: ActionEvent,
    params: Optional[Union[PullActionModel, ValidationError]] = None
):
    if isinstance(params, ValidationError):
        # handle errors
    else:
        # do stuff
```

Note that this changes the signature of the callbacks by adding an extra parameter with the parsed
counterpart of the `event.params` dict-like field. If validation fails, we return (not throw!) the
exception, to be handled (or raised) in the callback.

## Databag

In order to parse databag fields, we define a decorator to be applied to base relation event
callbacks.

```python
@parse_relation_data(app_model=AppDataModel, unit_model=UnitDataModel)
def _on_cluster_relation_joined(
        self, event: RelationEvent,
        app_data: Optional[Union[AppDataModel, ValidationError]] = None,
        unit_data: Optional[Union[UnitDataModel, ValidationError]] = None
) -> None:
    ...
```

The parameters `app_data` and `unit_data` refers to the databag of the entity which fired the
RelationEvent.

When we want to access to a relation databag outsides of an action, it can be useful also to
compact multiple databags into a single object (if there are no conflicting fields), e.g.

```python

class ProviderDataBag(BaseClass):
    provider_key: str

class RequirerDataBag(BaseClass):
    requirer_key: str

class MergedDataBag(ProviderDataBag, RequirerDataBag):
    pass

merged_data = get_relation_data_as(
    MergedDataBag, relation.data[self.app], relation.data[relation.app]
)

merged_data.requirer_key
merged_data.provider_key

```

The above code can be generalized to other kinds of merged objects, e.g. application and unit, and
it can be extended to multiple sources beyond 2:

```python
merged_data = get_relation_data_as(
    MergedDataBag, relation.data[self.app], relation.data[relation.app], ...
)
```

"""

import json
from functools import reduce, wraps
from typing import Callable, Generic, MutableMapping, Optional, Type, TypeVar, Union

import pydantic
from ops.charm import ActionEvent, CharmBase, RelationEvent
from ops.model import RelationDataContent
from pydantic import BaseModel, ValidationError

# The unique Charmhub library identifier, never change it
LIBID = "cb2094c5b07d47e1bf346aaee0fcfcfe"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 4

PYDEPS = ["ops>=2.0.0", "pydantic>=1.10,<2"]

G = TypeVar("G")
T = TypeVar("T", bound=BaseModel)
AppModel = TypeVar("AppModel", bound=BaseModel)
UnitModel = TypeVar("UnitModel", bound=BaseModel)

DataBagNativeTypes = (int, str, float)


class BaseConfigModel(BaseModel):
    """Class to be used for defining the structured configuration options."""

    def __getitem__(self, x):
        """Return the item using the notation instance[key]."""
        return getattr(self, x.replace("-", "_"))


class TypedCharmBase(CharmBase, Generic[T]):
    """Class to be used for extending config-typed charms."""

    config_type: Type[T]

    @property
    def config(self) -> T:
        """Return a config instance validated and parsed using the provided pydantic class."""
        translated_keys = {k.replace("-", "_"): v for k, v in self.model.config.items()}
        return self.config_type(**translated_keys)


def validate_params(cls: Type[T]):
    """Return a decorator to allow pydantic parsing of action parameters.

    Args:
        cls: Pydantic class representing the model to be used for parsing the content of the
             action parameter
    """

    def decorator(
        f: Callable[[CharmBase, ActionEvent, Union[T, ValidationError]], G]
    ) -> Callable[[CharmBase, ActionEvent], G]:
        @wraps(f)
        def event_wrapper(self: CharmBase, event: ActionEvent):
            try:
                params = cls(
                    **{key.replace("-", "_"): value for key, value in event.params.items()}
                )
            except ValidationError as e:
                params = e
            return f(self, event, params)

        return event_wrapper

    return decorator


def write(relation_data: RelationDataContent, model: BaseModel):
    """Write the data contained in a domain object to the relation databag.

    Args:
        relation_data: pointer to the relation databag
        model: instance of pydantic model to be written
    """
    for key, value in model.dict(exclude_none=False).items():
        if value:
            relation_data[key.replace("_", "-")] = (
                str(value)
                if any(isinstance(value, _type) for _type in DataBagNativeTypes)
                else json.dumps(value)
            )
        else:
            relation_data[key.replace("_", "-")] = ""


def read(relation_data: MutableMapping[str, str], obj: Type[T]) -> T:
    """Read data from a relation databag and parse it into a domain object.

    Args:
        relation_data: pointer to the relation databag
        obj: pydantic class representing the model to be used for parsing
    """
    return obj(
        **{
            field_name: (
                relation_data[parsed_key]
                if field.outer_type_ in DataBagNativeTypes
                else json.loads(relation_data[parsed_key])
            )
            for field_name, field in obj.__fields__.items()
            # pyright: ignore[reportGeneralTypeIssues]
            if (parsed_key := field_name.replace("_", "-")) in relation_data
            if relation_data[parsed_key]
        }
    )


def parse_relation_data(
    app_model: Optional[Type[AppModel]] = None, unit_model: Optional[Type[UnitModel]] = None
):
    """Return a decorator to allow pydantic parsing of the app and unit databags.

    Args:
        app_model: Pydantic class representing the model to be used for parsing the content of the
            app databag. None if no parsing ought to be done.
        unit_model: Pydantic class representing the model to be used for parsing the content of the
            unit databag. None if no parsing ought to be done.
    """

    def decorator(
        f: Callable[
            [
                CharmBase,
                RelationEvent,
                Optional[Union[AppModel, ValidationError]],
                Optional[Union[UnitModel, ValidationError]],
            ],
            G,
        ]
    ) -> Callable[[CharmBase, RelationEvent], G]:
        @wraps(f)
        def event_wrapper(self: CharmBase, event: RelationEvent):
            try:
                app_data = (
                    read(event.relation.data[event.app], app_model)
                    if app_model is not None and event.app
                    else None
                )
            except pydantic.ValidationError as e:
                app_data = e

            try:
                unit_data = (
                    read(event.relation.data[event.unit], unit_model)
                    if unit_model is not None and event.unit
                    else None
                )
            except pydantic.ValidationError as e:
                unit_data = e

            return f(self, event, app_data, unit_data)

        return event_wrapper

    return decorator


class RelationDataModel(BaseModel):
    """Base class to be used for creating data models to be used for relation databags."""

    def write(self, relation_data: RelationDataContent):
        """Write data to a relation databag.

        Args:
            relation_data: pointer to the relation databag
        """
        return write(relation_data, self)

    @classmethod
    def read(cls, relation_data: RelationDataContent) -> "RelationDataModel":
        """Read data from a relation databag and parse it as an instance of the pydantic class.

        Args:
            relation_data: pointer to the relation databag
        """
        return read(relation_data, cls)


def get_relation_data_as(
    model_type: Type[AppModel],
    *relation_data: RelationDataContent,
) -> Union[AppModel, ValidationError]:
    """Return a merged representation of the provider and requirer databag into a single object.

    Args:
        model_type: pydantic class representing the merged databag
        relation_data: list of RelationDataContent of provider/requirer/unit sides
    """
    try:
        app_data = read(reduce(lambda x, y: dict(x) | dict(y), relation_data, {}), model_type)
    except pydantic.ValidationError as e:
        app_data = e
    return app_data
