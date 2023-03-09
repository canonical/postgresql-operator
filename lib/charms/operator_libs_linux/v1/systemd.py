# Copyright 2021 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Abstractions for stopping, starting and managing system services via systemd.

This library assumes that your charm is running on a platform that uses systemd. E.g.,
Centos 7 or later, Ubuntu Xenial (16.04) or later.

For the most part, we transparently provide an interface to a commonly used selection of
systemd commands, with a few shortcuts baked in. For example, service_pause and
service_resume with run the mask/unmask and enable/disable invocations.

Example usage:
```python
from charms.operator_libs_linux.v0.systemd import service_running, service_reload

# Start a service
if not service_running("mysql"):
    success = service_start("mysql")

# Attempt to reload a service, restarting if necessary
success = service_reload("nginx", restart_on_failure=True)
```

"""

import logging
import subprocess

__all__ = [  # Don't export `_systemctl`. (It's not the intended way of using this lib.)
    "service_pause",
    "service_reload",
    "service_restart",
    "service_resume",
    "service_running",
    "service_start",
    "service_stop",
    "daemon_reload",
]

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "045b0d179f6b4514a8bb9b48aee9ebaf"

# Increment this major API version when introducing breaking changes
LIBAPI = 1

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class SystemdError(Exception):
    pass


def _popen_kwargs():
    return dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
        encoding="utf-8",
    )


def _systemctl(
    sub_cmd: str, service_name: str = None, now: bool = None, quiet: bool = None
) -> bool:
    """Control a system service.

    Args:
        sub_cmd: the systemctl subcommand to issue
        service_name: the name of the service to perform the action on
        now: passes the --now flag to the shell invocation.
        quiet: passes the --quiet flag to the shell invocation.
    """
    cmd = ["systemctl", sub_cmd]

    if service_name is not None:
        cmd.append(service_name)
    if now is not None:
        cmd.append("--now")
    if quiet is not None:
        cmd.append("--quiet")
    if sub_cmd != "is-active":
        logger.debug("Attempting to {} '{}' with command {}.".format(cmd, service_name, cmd))
    else:
        logger.debug("Checking if '{}' is active".format(service_name))

    proc = subprocess.Popen(cmd, **_popen_kwargs())
    last_line = ""
    for line in iter(proc.stdout.readline, ""):
        last_line = line
        logger.debug(line)

    proc.wait()

    if sub_cmd == "is-active":
        # If we are just checking whether a service is running, return True/False, rather
        # than raising an error.
        if proc.returncode < 1:
            return True
        if proc.returncode == 3:  # Code returned when service is not active.
            return False

    if proc.returncode < 1:
        return True

    raise SystemdError(
        "Could not {}{}: systemd output: {}".format(
            sub_cmd, " {}".format(service_name) if service_name else "", last_line
        )
    )


def service_running(service_name: str) -> bool:
    """Determine whether a system service is running.

    Args:
        service_name: the name of the service to check
    """
    return _systemctl("is-active", service_name, quiet=True)


def service_start(service_name: str) -> bool:
    """Start a system service.

    Args:
        service_name: the name of the service to start
    """
    return _systemctl("start", service_name)


def service_stop(service_name: str) -> bool:
    """Stop a system service.

    Args:
        service_name: the name of the service to stop
    """
    return _systemctl("stop", service_name)


def service_restart(service_name: str) -> bool:
    """Restart a system service.

    Args:
        service_name: the name of the service to restart
    """
    return _systemctl("restart", service_name)


def service_reload(service_name: str, restart_on_failure: bool = False) -> bool:
    """Reload a system service, optionally falling back to restart if reload fails.

    Args:
        service_name: the name of the service to reload
        restart_on_failure: boolean indicating whether to fallback to a restart if the
          reload fails.
    """
    try:
        return _systemctl("reload", service_name)
    except SystemdError:
        if restart_on_failure:
            return _systemctl("restart", service_name)
        else:
            raise


def service_pause(service_name: str) -> bool:
    """Pause a system service.

    Stop it, and prevent it from starting again at boot.

    Args:
        service_name: the name of the service to pause
    """
    _systemctl("disable", service_name, now=True)
    _systemctl("mask", service_name)

    if not service_running(service_name):
        return True

    raise SystemdError("Attempted to pause '{}', but it is still running.".format(service_name))


def service_resume(service_name: str) -> bool:
    """Resume a system service.

    Re-enable starting again at boot. Start the service.

    Args:
        service_name: the name of the service to resume
    """
    _systemctl("unmask", service_name)
    _systemctl("enable", service_name, now=True)

    if service_running(service_name):
        return True

    raise SystemdError("Attempted to resume '{}', but it is not running.".format(service_name))


def daemon_reload() -> bool:
    """Reload systemd manager configuration."""
    return _systemctl("daemon-reload")
