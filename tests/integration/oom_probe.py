# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Read live workload state on a unit using only the Python standard library."""

import json
import subprocess
from pathlib import Path


def snapshot() -> dict:
    """Include reparented PostgreSQL processes by inspecting the service cgroup."""
    properties = subprocess.check_output(
        [
            "systemctl",
            "show",
            "snap.charmed-postgresql.patroni.service",
            "--property=MainPID,ControlGroup,OOMScoreAdjust,ActiveState",
        ],
        text=True,
    )
    service = dict(line.split("=", 1) for line in properties.splitlines())
    group = service["ControlGroup"]
    assert group and service["ActiveState"] == "active", service
    processes = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            groups = [
                line.split(":", 2)[2] for line in (directory / "cgroup").read_text().splitlines()
            ]
            if not any(path == group or path.startswith(f"{group}/") for path in groups):
                continue
            stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            argv = (directory / "cmdline").read_bytes().decode().split("\0")
            processes.append({
                "pid": int(directory.name),
                "ppid": int(stat[1]),
                "start_ticks": int(stat[19]),
                "name": (directory / "comm").read_text().strip(),
                "postmaster": argv[0].endswith("/postgres"),
                "patroni": any(Path(arg).name == "patroni" for arg in argv),
                "oom_score_adj": int((directory / "oom_score_adj").read_text()),
            })
        except (FileNotFoundError, ProcessLookupError):
            # Short-lived backends may exit while /proc is being read.
            continue
    config = json.loads(
        subprocess.check_output(["/usr/bin/snap", "get", "system", "-d"], text=True)
    )
    return {
        "hint": config.get("resilience", {}).get("vitality-hint", ""),
        "revision": Path("/snap/charmed-postgresql/current").resolve().name,
        "service": service,
        "processes": processes,
    }


if __name__ == "__main__":
    print(json.dumps(snapshot()))
