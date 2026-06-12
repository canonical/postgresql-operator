# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""PostgreSQL Paths."""

from abc import ABC, abstractmethod

from charmlibs.pathops import PathProtocol


class Paths(ABC):
    """This class represents the set of Paths that need to be exposed.

    Args:
            conf: Path to the config folder of PostgreSQL
            data: Path to the data folder of PostgreSQL
            logs: Path to the logs folder of PostgreSQL
            tmp: Temporary directory
            bin: Path to the bin/ folder
    """

    def __init__(self, root: PathProtocol):
        """Initialize the Paths.

        Args:
            root: The root path for the PostgreSQL installation.
        """
        super().__init__()
        self.root = root

    @property
    @abstractmethod
    def conf(self) -> PathProtocol:
        """Path to the config folder of PostgreSQL."""
        pass

    @property
    @abstractmethod
    def data(self) -> PathProtocol:
        """Path to the data folder of PostgreSQL."""
        pass

    @property
    @abstractmethod
    def logs(self) -> PathProtocol:
        """Path to the logs folder of PostgreSQL."""
        pass

    @property
    @abstractmethod
    def tmp(self) -> PathProtocol:
        """Path to the temporary directory."""
        pass
