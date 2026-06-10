# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utilities for catching and raising architecture errors."""

import logging
import os
import sys

from ops.charm import CharmBase
from ops.model import BlockedStatus

logger = logging.getLogger(__name__)


class WrongArchitectureWarningCharm(CharmBase):
    """A fake charm class that only signals a wrong architecture deploy."""

    def __init__(self, *args):
        super().__init__(*args)
        self.unit.status = BlockedStatus(
            f"Error: Charm version incompatible with {os.uname().machine} architecture"
        )
        sys.exit(0)


def is_wrong_architecture() -> bool:
    """Checks if charm was deployed on wrong architecture."""
    manifest_file_path = f"{os.environ.get('CHARM_DIR')}/manifest.yaml"
    if not os.path.exists(manifest_file_path):
        logger.error(
            "Cannot check architecture: manifest file not found in %s", manifest_file_path
        )
        return False

    with open(manifest_file_path) as file:
        manifest = file.read()
    hw_arch = os.uname().machine
    if ("amd64" in manifest and hw_arch == "x86_64") or (
        "arm64" in manifest and hw_arch == "aarch64"
    ):
        logger.info("Charm architecture matches")
        return False

    logger.error("Charm architecture does not match")
    return True
