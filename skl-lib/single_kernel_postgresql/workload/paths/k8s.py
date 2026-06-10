# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""PostgreSQL Kubernetes Paths."""

from charmlibs.pathops import PathProtocol

from single_kernel_postgresql.config.literals import (
    K8S_DATA_PATH,
    PATRONI_CONF_PATH,
    POSTGRESQL_CONF_FILE,
    POSTGRESQL_CONF_PATH,
)
from single_kernel_postgresql.workload.paths.base import Paths


class K8sPaths(Paths):
    """This class represents the set of Paths that need to be exposed for the Kubernetes substrate.

    Args:
            conf: Path to the config folder of PostgreSQL
            data: Path to the data folder of PostgreSQL
            logs: Path to the logs folder of PostgreSQL
            tmp: Temporary directory
            bin: Path to the bin/ folder
    """

    @property
    def conf(self) -> PathProtocol:
        """Path to the config folder of PostgreSQL."""
        # TODO: Update path
        return self.root / POSTGRESQL_CONF_PATH

    @property
    def data(self) -> PathProtocol:
        """Path to the data folder of PostgreSQL."""
        return self.root / K8S_DATA_PATH

    @property
    def logs(self) -> PathProtocol:
        """Path to the logs folder of PostgreSQL."""
        # TODO: Update path
        return self.root / "logs"

    @property
    def tmp(self) -> PathProtocol:
        """Path to the temporary directory."""
        return self.root / "tmp"

    @property
    def postgresql_conf(self) -> PathProtocol:
        """Path to the postgresql.conf file."""
        return self.conf / POSTGRESQL_CONF_FILE

    @property
    def patroni_conf(self) -> PathProtocol:
        """Path to the patroni.yaml file."""
        return self.root / PATRONI_CONF_PATH
