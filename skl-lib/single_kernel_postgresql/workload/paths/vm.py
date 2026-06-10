# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""PostgreSQL Machine Paths."""

from charmlibs.pathops import PathProtocol

from single_kernel_postgresql.config.literals import (
    BASE_SNAP_DIR,
    PATRONI_CONF_PATH,
    POSTGRESQL_CONF_FILE,
    POSTGRESQL_CONF_PATH,
    SNAP,
    SNAP_COMMON,
    SNAP_DATA,
    VM_DATA_PATH,
)
from single_kernel_postgresql.workload.paths.base import Paths


class VMPaths(Paths):
    """This class represents the set of Paths that need to be exposed for the Machine substrate.

    Args:
            conf: Path to the config folder of PostgreSQL
            data: Path to the data folder of PostgreSQL
            logs: Path to the logs folder of PostgreSQL
            tmp: Temporary directory
            bin: Path to the bin/ folder
    """

    @property
    def base_snap_dir(self) -> PathProtocol:
        """Get path to the Base snap directory."""
        return self.root / BASE_SNAP_DIR

    @property
    def snap_data(self) -> PathProtocol:
        """Get path to the snap data directory."""
        return self.base_snap_dir / SNAP_DATA

    @property
    def snap_common(self) -> PathProtocol:
        """Get path to the snap common directory."""
        return self.base_snap_dir / SNAP_COMMON

    @property
    def snap_current(self) -> PathProtocol:
        """Get path to the snap current directory."""
        return self.base_snap_dir / SNAP_DATA

    @property
    def snap(self) -> PathProtocol:
        """Get path to the snap directory."""
        return self.root / SNAP

    @property
    def conf(self) -> PathProtocol:
        """Path to the config folder of PostgreSQL."""
        # TODO: Update path
        return self.snap_current / POSTGRESQL_CONF_PATH

    @property
    def postgresql_conf(self) -> PathProtocol:
        """Path to the postgresql.conf file."""
        return self.conf / POSTGRESQL_CONF_FILE

    @property
    def patroni_conf(self) -> PathProtocol:
        """Path to the patroni.yaml file."""
        return self.snap_current / PATRONI_CONF_PATH

    @property
    def data(self) -> PathProtocol:
        """Path to the data folder of PostgreSQL."""
        return self.snap_common / VM_DATA_PATH

    @property
    def logs(self) -> PathProtocol:
        """Path to the logs folder of PostgreSQL."""
        # TODO: Update path
        return self.root / "logs"

    @property
    def tmp(self) -> PathProtocol:
        """Path to the temporary directory."""
        return self.root / "tmp"
