#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base interface for common workload operations."""

from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from charmlibs import pathops
from charmlibs.pathops import PathProtocol
from ops import ModelError
from ops.pebble import Error as PebbleError

from single_kernel_postgresql.config.exceptions import PostgreSQLFileOperationError
from single_kernel_postgresql.config.literals import DIR_PERMISSIONS_READONLY
from single_kernel_postgresql.workload.paths.base import Paths


# --- Base Workload
class BaseWorkload(ABC):
    """Base interface for common workload operations."""

    def __init__(self, charm_dir: Path):
        """Initialize K8s workload.

        Args:
            charm_dir: the path to charm code.
        """
        super().__init__()
        self.charm_dir = charm_dir

    @property
    @abstractmethod
    def root(self) -> PathProtocol:
        """Return the root path."""
        pass

    @abstractmethod
    def install(self) -> None:
        """Install the workload."""
        pass

    @property
    @abstractmethod
    def paths(self) -> Paths:
        """Return the Workload's paths."""
        pass

    @property
    @abstractmethod
    def workload_present(self) -> bool:
        """Flag to check if workload is present and ready."""
        pass

    def write_text(
        self, content: str, path: pathops.PathProtocol, mode: int | None = None
    ) -> None:
        """Write content to a file on disk.

        Args:
            content (str): The content to be written.
            path (pathops.PathProtocol): The file path where the content should be written.
            mode (int, optional): The mode/permissions to use when writing the file.

        Raises:
            PostgreSQLFileOperationError: If there is an error during the file write operation.
        """
        try:
            path.write_text(content, mode=mode)
        except (
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise PostgreSQLFileOperationError(e) from e

    def read_text(self, path: pathops.PathProtocol) -> str:
        """Read content from a file on disk.

        Args:
            path (pathops.PathProtocol): The file path to read from.

        Returns:
            str: The content read from the file.
        """
        try:
            return path.read_text()
        except (
            FileNotFoundError,
            UnicodeError,
            PermissionError,
            PebbleError,
            ModelError,
            pathops.PebbleConnectionError,
        ) as e:
            raise PostgreSQLFileOperationError(e) from e

    def mkdir(
        self,
        path: pathops.PathProtocol,
        mode: int = DIR_PERMISSIONS_READONLY,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        """Create a directory on disk.

        Args:
            path (pathops.PathProtocol): The directory path to create.
            mode (int): The mode/permissions to use for the new directory.
            parents (bool): Whether to create parent directories if they do not exist.
            exist_ok (bool): Whether to ignore the error if the directory already exists.
        """
        try:
            path.mkdir(mode=mode, parents=parents, exist_ok=exist_ok)
        except (
            PebbleError,
            ModelError,
            FileExistsError,
            FileNotFoundError,
            LookupError,
            NotADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
            ValueError,
        ) as e:
            raise PostgreSQLFileOperationError(e) from e

    def exists(self, path: pathops.PathProtocol) -> bool:
        """Check if a file or directory exists on disk.

        Args:
            path (pathops.PathProtocol): The file or directory path to check.

        Returns:
            bool: True if the file or directory exists, False otherwise.

        Raises:
            PostgreSQLFileOperationError: If there is an error accessing the file system.
        """
        try:
            return path.exists()
        except (PermissionError, pathops.PebbleConnectionError) as e:
            raise PostgreSQLFileOperationError(e) from e

    def unlink(self, path: pathops.PathProtocol, missing_ok: bool = False) -> None:
        """Remove a file from disk.

        Args:
            path (pathops.PathProtocol): The file path to remove.
            missing_ok (bool): Whether to ignore the error if the file does not exist.
        """
        try:
            path.unlink(missing_ok=missing_ok)
        except (
            FileNotFoundError,
            IsADirectoryError,
            PermissionError,
            pathops.PebbleConnectionError,
        ) as e:
            raise PostgreSQLFileOperationError(e) from e

    @contextmanager
    @abstractmethod
    def temp_file(
        self,
        mode: str = "w+b",
        data: str | None = None,
        encoding: str | None = None,
        directory: PathProtocol | None = None,
        delete: bool = True,
        chown: str | None = None,
        *,
        errors: str | None = None,
        suffix: str | None = None,
    ) -> Generator[PathProtocol, None, None]:
        """Context manager for creating temporary files."""
        raise NotImplementedError

    @abstractmethod
    def is_service_started(self, paused: bool | None = False) -> bool:
        """Check if the snap service is running.

        Set paused=True if the process was intentionally paused.
        """
        pass

    @abstractmethod
    def start_service_only(self):
        """Start the actual service only (snap / pebble)."""
        pass

    @abstractmethod
    def run_cmd(
        self,
        command: str,
        args: str | None = None,
        use_errors_replace: bool = False,
        stdin: str | None = None,
    ) -> SimpleNamespace:
        """Run Command in CLI."""
        pass

    @abstractmethod
    def is_failed(self) -> bool:
        """Check if snap service failed."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the PostgreSQL service."""
        pass

    @abstractmethod
    def start_service(self):
        """Start the PostgreSQL service."""
        pass

    @abstractmethod
    def get_workload_version(self) -> str:
        """Get the workload version."""
        raise NotImplementedError
