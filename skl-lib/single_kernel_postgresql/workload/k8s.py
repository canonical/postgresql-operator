# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Kubernetes Workload."""

import logging
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from charmlibs import pathops
from charmlibs.pathops import PathProtocol
from ops import Container

from single_kernel_postgresql.config.exceptions import PostgreSQLFileOperationError
from single_kernel_postgresql.config.literals import DIR_PERMISSIONS_READONLY
from single_kernel_postgresql.workload.base import BaseWorkload
from single_kernel_postgresql.workload.paths.base import Paths as BasePaths
from single_kernel_postgresql.workload.paths.k8s import K8sPaths

logger = logging.getLogger(__name__)


class K8sWorkload(BaseWorkload):
    """Kubernetes PostgreSQL Workload."""

    def __init__(self, charm_dir: Path, container: Container | None = None):
        """Initialize workload.

        Args:
            charm_dir: the path to charm code.
            container: the Container instance.
        """
        super().__init__(charm_dir=charm_dir)
        if not container:
            raise AttributeError("Container is required.")
        self.container = container
        self._paths: BasePaths | None = None

    def install(self) -> None:
        """Install the workload."""
        pass

    def is_service_started(self, paused: bool | None = False) -> bool:
        """Check if the snap service is running.

        Set paused=True if the process was intentionally paused.
        """
        raise NotImplementedError

    def start_service_only(self):
        """Start the actual service only (snap / pebble)."""
        raise NotImplementedError

    def run_cmd(
        self,
        command: str,
        args: str | None = None,
        use_errors_replace: bool = False,
        stdin: str | None = None,
    ) -> SimpleNamespace:
        """Run Command in CLI."""
        raise NotImplementedError

    def is_failed(self) -> bool:
        """Check if snap service failed."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop the PostgreSQL service."""
        ...

    def start_service(self):
        """Start the PostgreSQL service."""
        ...

    def get_workload_version(self) -> str:
        """Get the workload version."""
        raise NotImplementedError

    @contextmanager
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
        """Create a temporary file in the container and return the file path.

        Args:
            mode: file mode
            data: Optional string data to write to the file.
            encoding: encoding for data writing (defaults to utf-8).
            directory: Optional directory path.
            delete: If True, delete the file when context exits.
            errors: Error handling mode
            suffix: Optional suffix to append to filename.
            chown: Optional user to chown the file to after creation.

        Yields:
            PathProtocol: Path object representing the temporary file.

        Raises:
            PebbleError: if file operations fail.
        """
        # PathProtocol exposes text operations.
        temp_dir_path = directory or self.paths.tmp
        self.mkdir(
            temp_dir_path,
            mode=DIR_PERMISSIONS_READONLY,
            parents=True,
            exist_ok=True,
        )

        temp_filename = "temp_{}{}".format(uuid.uuid4().hex, suffix or "")
        file_path = temp_dir_path / temp_filename

        try:
            if data is not None:
                file_path.write_text(data)
            yield file_path
        finally:
            if delete:
                try:
                    self.unlink(file_path, missing_ok=True)
                except PostgreSQLFileOperationError as e:
                    logger.warning(f"Failed to delete temporary file {file_path}: {e}")

    @property
    def root(self) -> PathProtocol:
        """Return the root path for container filesystem.

        For K8s containers, use PathOps ContainerPath for container API.
        ContainerPath handles pull/push operations internally via its read_text/write_text methods.

        Returns:
            PathProtocol: ContainerPath instance bound to the container.
        """
        return pathops.ContainerPath("/", container=self.container)

    @property
    def paths(self) -> BasePaths:
        """Return Workload's paths.

        This is cached to avoid recreating K8sPaths on every access, since self.root
        is a ContainerPath bound to self.container.
        """
        if self._paths is None:
            # access self.root which depends on self.container
            # this may raise RuntimeError if container isn't set, which is expected
            # during initialization before container is available
            root_path = self.root
            self._paths = K8sPaths(root_path)
        return self._paths

    @property
    def workload_present(self) -> bool:
        """Flag to check if workload is present and ready."""
        raise NotImplementedError
