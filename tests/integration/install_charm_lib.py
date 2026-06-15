#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
"""Install the single-kernel library (without deps) for the integration tests.

The library URL is defined once in pyproject.toml (single source of truth); the
integration tests import it only for locale constants, so it is installed with
``--no-deps`` to keep psycopg2 and its other runtime deps out of the env.
"""

import subprocess
import sys
import tomllib
from pathlib import Path


def main() -> None:
    """Read the library URL from pyproject.toml and pip-install it without deps."""
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    dependencies = tomllib.loads(pyproject.read_text())["tool"]["poetry"]["dependencies"]
    url = dependencies["postgresql-charms-single-kernel"]["url"]
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", url], check=True)


if __name__ == "__main__":
    main()
