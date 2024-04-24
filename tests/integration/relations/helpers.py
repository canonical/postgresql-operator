#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import Optional

import yaml
from pytest_operator.plugin import OpsTest


async def get_legacy_db_connection_str(
    ops_test: OpsTest,
    application_name: str,
    relation_name: str,
    read_only_endpoint: bool = False,
    remote_unit_name: str = None,
) -> Optional[str]:
    """Returns a PostgreSQL connection string.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        relation_name: name of the relation to get connection data from
        read_only_endpoint: whether to choose the read-only endpoint
            instead of the read/write endpoint
        remote_unit_name: Optional remote unit name used to retrieve
            unit data instead of application data

    Returns:
        a PostgreSQL connection string
    """
    unit_name = f"{application_name}/0"
    raw_data = (await ops_test.juju("show-unit", unit_name))[1]
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [
        v for v in data[unit_name]["relation-info"] if v["related-endpoint"] == relation_name
    ]
    if len(relation_data) == 0:
        raise ValueError(
            f"no relation data could be grabbed on relation with endpoint {relation_name}"
        )
    if remote_unit_name:
        data = relation_data[0]["related-units"][remote_unit_name]["data"]
    else:
        data = relation_data[0]["application-data"]
    if read_only_endpoint:
        if data.get("standbys") is None:
            return None
        return data.get("standbys").split(",")[0]
    else:
        return data.get("master")
