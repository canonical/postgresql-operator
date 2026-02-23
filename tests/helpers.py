# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


from pathlib import Path

import yaml

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
STORAGE_PATH = METADATA["storage"]["data"]["location"]
PGDATA_PATH = f"{STORAGE_PATH}/16/main"
