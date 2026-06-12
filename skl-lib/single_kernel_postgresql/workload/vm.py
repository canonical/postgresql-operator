# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Machine Workload."""

import logging
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from charmlibs import pathops
from charmlibs.pathops import PathProtocol

from single_kernel_postgresql.workload.base import BaseWorkload
from single_kernel_postgresql.workload.paths.base import Paths as BasePaths
from single_kernel_postgresql.workload.paths.vm import VMPaths

logger = logging.getLogger(__name__)


class VMWorkload(BaseWorkload):
    """Machine PostgreSQL Workload."""

    def __init__(self, charm_dir: Path):
        super().__init__(charm_dir)

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
        mode="w+b",
        data: str | None = None,
        encoding: str | None = None,
        directory: PathProtocol | None = None,
        delete: bool = True,
        chown: str | None = None,
        *,
        errors: str | None = None,
        suffix: str | None = None,
    ) -> Generator[PathProtocol, None, None]:
        """Create a temporary file and return the file, clean it once context is closed."""
        f = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode=mode,
            encoding=encoding,
            dir=directory.as_posix() if directory else None,
            delete=False,
            errors=errors,
            suffix=suffix,
        )
        if chown is not None:
            command = f"sudo chown {chown} {f.name}"
            self.run_cmd(command)
        file_path: PathProtocol = self.root / f.name
        try:
            if data:
                self.write_text(data, file_path)
            yield file_path
        finally:
            if not f.closed:
                f.close()
            if delete:
                try:
                    file_path.unlink()
                except OSError as e:
                    raise e

    @property
    def paths(self) -> BasePaths:
        """Return Workload's paths."""
        return VMPaths(self.root)

    @property
    def root(self) -> PathProtocol:
        """Return the root path."""
        return pathops.LocalPath("/")

    @property
    def workload_present(self) -> bool:
        """Flag to check if workload is present and ready."""
        raise NotImplementedError
