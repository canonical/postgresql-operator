#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
"""Install the single-kernel library for the integration tests.

The library URL is defined once in pyproject.toml (single source of truth); the
integration tests import it only for locale constants. psycopg2 is an optional
``postgresql`` extra in the library, so a plain install pulls only the
pure-Python runtime deps and never builds psycopg2 from source.
"""

import subprocess
import sys
import tomllib
from pathlib import Path


def main() -> None:
    """Read the library URL from pyproject.toml and pip-install it."""
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    dependencies = tomllib.loads(pyproject.read_text())["tool"]["poetry"]["dependencies"]
    url = dependencies["postgresql-charms-single-kernel"]["url"]
    subprocess.run([sys.executable, "-m", "pip", "install", url], check=True)


if __name__ == "__main__":
    main()
