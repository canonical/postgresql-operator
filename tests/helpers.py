# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


from pathlib import Path
from typing import Callable
from unittest.mock import patch

import yaml

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
STORAGE_PATH = METADATA["storage"]["pgdata"]["location"]


def patch_network_get(private_address="1.1.1.1") -> Callable:
    def network_get(*args, **kwargs) -> dict:
        """Patch for the not-yet-implemented testing backend needed for `bind_address`.

        This patch decorator can be used for cases such as:
        self.model.get_binding(event.relation).network.bind_address
        """
        return {
            "bind-addresses": [
                {
                    "addresses": [{"value": private_address}],
                }
            ]
        }

    return patch("ops.testing._TestingModelBackend.network_get", network_get)
