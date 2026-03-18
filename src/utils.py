# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""A collection of utility functions that are used in the charm."""

import logging
import os
import platform
import secrets
import shutil
import string
from collections import defaultdict

from importlib_metadata import distributions

from constants import (
    POSTGRESQL_SNAP_NAME,
    SNAP_PACKAGES,
)


def new_password() -> str:
    """Generate a random password string.

    Returns:
       A random password string.
    """
    choices = string.ascii_letters + string.digits
    password = "".join([secrets.choice(choices) for i in range(16)])
    return password


def snap_refreshed(target_rev: str) -> bool:
    """Whether the snap was refreshed to the target version."""
    arch = platform.machine()

    for snap_package in SNAP_PACKAGES:
        snap_name = snap_package[0]
        snap_revs = snap_package[1]["revision"]
        if snap_name == POSTGRESQL_SNAP_NAME and target_rev != snap_revs.get(arch):
            return False

    return True


def label2name(label: str) -> str:
    """Convert a unit label (with `-`) to a unit name (with `/`).

    Args:
        label: The label to convert.

    Returns:
        The converted name.
    """
    return label.rsplit("-", 1)[0] + "/" + label.rsplit("-", 1)[1]


def _remove_stale_otel_sdk_packages():
    """Hack to remove stale opentelemetry sdk packages from the charm's python venv.

    See https://github.com/canonical/grafana-agent-operator/issues/146 and
    https://bugs.launchpad.net/juju/+bug/2058335 for more context. This patch can be removed after
    this juju issue is resolved and sufficient time has passed to expect most users of this library
    have migrated to the patched version of juju.  When this patch is removed, un-ignore rule E402 for this file in the pyproject.toml (see setting
    [tool.ruff.lint.per-file-ignores] in pyproject.toml).

    This only has an effect if executed on an upgrade-charm event.
    """
    # all imports are local to keep this function standalone, side-effect-free, and easy to revert later

    if os.getenv("JUJU_DISPATCH_PATH") != "hooks/upgrade-charm":
        return

    otel_logger = logging.getLogger("charm_tracing_otel_patcher")
    otel_logger.debug("Applying _remove_stale_otel_sdk_packages patch on charm upgrade")
    # group by name all distributions starting with "opentelemetry_"
    otel_distributions = defaultdict(list)
    for distribution in distributions():
        name = distribution._normalized_name
        if name.startswith("opentelemetry_"):
            otel_distributions[name].append(distribution)

    otel_logger.debug(f"Found {len(otel_distributions)} opentelemetry distributions")

    # If we have multiple distributions with the same name, remove any that have 0 associated files
    for name, distributions_ in otel_distributions.items():
        if len(distributions_) <= 1:
            continue

        otel_logger.debug(f"Package {name} has multiple ({len(distributions_)}) distributions.")
        for distribution in distributions_:
            if not distribution.files:  # Not None or empty list
                path = distribution._path
                otel_logger.info(f"Removing empty distribution of {name} at {path}.")
                shutil.rmtree(path)

    otel_logger.debug("Successfully applied _remove_stale_otel_sdk_packages patch.")
