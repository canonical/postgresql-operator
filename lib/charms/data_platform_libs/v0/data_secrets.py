"""Secrets related helper classes/functions."""
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

from typing import Dict, Literal, Optional

from ops import Secret, SecretInfo
from ops.charm import CharmBase
from ops.model import SecretNotFoundError

# The unique Charmhub library identifier, never change it
LIBID = "d77fb3d01aba41ed88e837d0beab6be5"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


APP_SCOPE = "app"
UNIT_SCOPE = "unit"
Scopes = Literal["app", "unit"]


class DataSecretsError(Exception):
    """A secret that we want to create already exists."""


class SecretAlreadyExistsError(DataSecretsError):
    """A secret that we want to create already exists."""


def generate_secret_label(charm: CharmBase, scope: Scopes) -> str:
    """Generate unique group_mappings for secrets within a relation context.

    Defined as a standalone function, as the choice on secret labels definition belongs to the
    Application Logic. To be kept separate from classes below, which are simply to provide a
    (smart) abstraction layer above Juju Secrets.
    """
    members = [charm.app.name, scope]
    return f"{'.'.join(members)}"


# Secret cache


class CachedSecret:
    """Abstraction layer above direct Juju access with caching.

    The data structure is precisely re-using/simulating Juju Secrets behavior, while
    also making sure not to fetch a secret multiple times within the same event scope.
    """

    def __init__(self, charm: CharmBase, label: str, secret_uri: Optional[str] = None):
        self._secret_meta = None
        self._secret_content = {}
        self._secret_uri = secret_uri
        self.label = label
        self.charm = charm

    def add_secret(self, content: Dict[str, str], scope: Scopes) -> Secret:
        """Create a new secret."""
        if self._secret_uri:
            raise SecretAlreadyExistsError(
                "Secret is already defined with uri %s", self._secret_uri
            )

        if scope == APP_SCOPE:
            secret = self.charm.app.add_secret(content, label=self.label)
        else:
            secret = self.charm.unit.add_secret(content, label=self.label)
        self._secret_uri = secret.id
        self._secret_meta = secret
        return self._secret_meta

    @property
    def meta(self) -> Optional[Secret]:
        """Getting cached secret meta-information."""
        if self._secret_meta:
            return self._secret_meta

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
                self._secret_content = self.meta.get_content()
        return self._secret_content

    def set_content(self, content: Dict[str, str]) -> None:
        """Setting cached secret content."""
        if self.meta:
            self.meta.set_content(content)
            self._secret_content = content

    def get_info(self) -> Optional[SecretInfo]:
        """Wrapper function for get the corresponding call on the Secret object if any."""
        if self.meta:
            return self.meta.get_info()


class SecretCache:
    """A data structure storing CachedSecret objects."""

    def __init__(self, charm):
        self.charm = charm
        self._secrets: Dict[str, CachedSecret] = {}

    def get(self, label: str, uri: Optional[str] = None) -> Optional[CachedSecret]:
        """Getting a secret from Juju Secret store or cache."""
        if not self._secrets.get(label):
            secret = CachedSecret(self.charm, label, uri)

            # Checking if the secret exists, otherwise we don't register it in the cache
            if secret.meta:
                self._secrets[label] = secret
        return self._secrets.get(label)

    def add(self, label: str, content: Dict[str, str], scope: Scopes) -> CachedSecret:
        """Adding a secret to Juju Secret."""
        if self._secrets.get(label):
            raise SecretAlreadyExistsError(f"Secret {label} already exists")

        secret = CachedSecret(self.charm, label)
        secret.add_secret(content, scope)
        self._secrets[label] = secret
        return self._secrets[label]


# END: Secret cache
