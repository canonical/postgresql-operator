# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Unit tests for the shared integration-test helpers.

tests/integration/helpers.py imports pytest-operator/juju/botocore, which are not
part of the unit dependency group: stub them before the import when missing so the
connection-retry behavior is still exercised by the unit suite.
"""

import sys
import types
from pathlib import Path

import psycopg2
import pytest

_STUBS = {
    "botocore": ["__all__"],
    "juju": ["__path__"],
    "juju.model": ["Model"],
    "juju.unit": ["Unit"],
    "pytest_operator": ["__path__"],
    "pytest_operator.plugin": ["OpsTest"],
}


sys.path.insert(0, str(Path(__file__).parents[1]))


def _ensure_importable() -> None:
    try:
        import botocore  # noqa: F401
        import juju.model  # noqa: F401
        import juju.unit  # noqa: F401
        import pytest_operator.plugin  # noqa: F401

        return
    except ImportError:
        pass
    for module_name, attrs in _STUBS.items():
        if module_name in sys.modules:
            continue
        module = types.ModuleType(module_name)
        for attr in attrs:
            setattr(module, attr, type(attr, (), {}) if attr[0].isupper() else [])
        if not module_name.endswith(("model", "unit", "plugin")):
            module.__path__ = []
        sys.modules[module_name] = module


_ensure_importable()

from integration.helpers import execute_queries_on_unit  # noqa: E402


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        """Enter the context manager."""
        return self

    def __exit__(self, *args):
        """Exit the context manager."""
        pass


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        """Return a cursor over the faked rows."""
        return FakeCursor(self._rows)

    def __enter__(self):
        """Enter the context manager."""
        return self

    def __exit__(self, *args):
        """Exit the context manager."""
        pass


def _patch_connect(monkeypatch, outcomes):
    """Patch psycopg2.connect to pop one outcome per call; outcome is an exception or rows."""
    calls = []

    def fake_connect(dsn, **kwargs):
        outcome = outcomes.pop(0) if outcomes else [(1,)]
        calls.append(dsn)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeConnection(outcome)

    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    return calls


def test_execute_queries_retries_connection_refused(monkeypatch):
    """A transient 'Connection refused' during connect is retried until success."""
    refused = psycopg2.OperationalError("connection to server failed: Connection refused")
    calls = _patch_connect(monkeypatch, [refused, refused, [(42,)]])

    output = execute_queries_on_unit("1.2.3.4", "user", "password", ["SELECT 1;"], "database")

    assert output == [42]
    assert len(calls) == 3


def test_execute_queries_does_not_retry_other_operational_errors(monkeypatch):
    """Non-transient OperationalErrors surface immediately without retries."""
    auth_failed = psycopg2.OperationalError("password authentication failed for user")
    calls = _patch_connect(monkeypatch, [auth_failed, [(1,)]])

    with pytest.raises(psycopg2.OperationalError, match="password authentication"):
        execute_queries_on_unit("1.2.3.4", "user", "password", ["SELECT 1;"], "database")

    assert len(calls) == 1
