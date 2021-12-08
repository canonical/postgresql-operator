# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.


import tempfile
from typing import Optional

PERMISSION_MASK_FOR_SCP = 644


async def pull_content_from_unit_file(unit, path: str) -> str:
    """Pull the content of a file from one unit.

    Args:
        unit: the Juju unit instance.
        path: the path of the file to get the contents from.

    Returns:
        the entire content of the file.
    """
    # Get the file's original access permission mask.
    result = await run_command_on_unit(unit, f"stat -c %a {path}")
    permissions = result.strip()
    # Change the permission in order to be able to retrieve the file using the ubuntu user.
    if permissions != PERMISSION_MASK_FOR_SCP:
        await run_command_on_unit(unit, f"chmod {PERMISSION_MASK_FOR_SCP} {path}")

    # Get the contents of the file.
    temp_file = tempfile.NamedTemporaryFile()
    await unit.scp_from(
        path,
        temp_file.name,
        scp_opts=["-v"],
    )
    data = temp_file.read().decode("utf-8")
    temp_file.close()

    # Change the file permissions back to the original mask.
    if permissions != PERMISSION_MASK_FOR_SCP:
        await run_command_on_unit(unit, f"chmod {permissions} {path}")

    return data


async def run_command_on_unit(unit, command: str) -> Optional[str]:
    """Run a command in one Juju unit.

    Args:
        unit: the Juju unit instance.
        command: the command to run.

    Returns:
        command execution output or none if
        the command produces no output.
    """
    action = await unit.run(command)
    return action.results.get("Stdout", None)
