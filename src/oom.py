# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Configure snap OOM protection without changing running services."""

import json
import subprocess

from charmlibs import snap

from constants import (
    SNAP_OOM_SCORE_ADJUST_MIN,
    SNAP_VITALITY_HINT,
    SNAP_VITALITY_MAX_SNAPS,
)


def _read_vitality_hint() -> str:
    """Read the hint without confusing a failed query with an absent setting."""
    config = json.loads(
        subprocess.check_output(["/usr/bin/snap", "get", "system", "-d"], text=True)
    )
    if not isinstance(config, dict):
        raise ValueError("Snap system configuration must be an object")
    resilience = config.get("resilience", {})
    if not isinstance(resilience, dict):
        raise ValueError("Snap resilience configuration must be an object")
    hint = resilience.get("vitality-hint", "")
    if not isinstance(hint, str):
        raise ValueError("Snap vitality hint must be a string")
    if hint and len(hint.split(",")) > SNAP_VITALITY_MAX_SNAPS:
        raise ValueError("Snap vitality hint exceeds the supported limit")
    return hint


def ensure_snap_oom_protection(snap_name: str) -> int:
    """Append a missing snap to the hint and return its effective OOM adjustment.

    Juju serializes the synchronous hooks and actions that call this helper.
    Administrator changes to the same setting must not run concurrently because
    snapd has no atomic append API.
    Existing processes keep their adjustment until their normal next startup.
    """
    try:
        hint = _read_vitality_hint()
        entries = hint.split(",") if hint else []
        if snap_name not in entries:
            if len(entries) >= SNAP_VITALITY_MAX_SNAPS:
                raise ValueError("Snap vitality hint is full")
            updated_hint = f"{hint},{snap_name}" if hint else snap_name
            subprocess.check_call([  # noqa: S603
                "/usr/bin/snap",
                "set",
                "system",
                f"{SNAP_VITALITY_HINT}={updated_hint}",
            ])
            verified_hint = _read_vitality_hint()
            verified_entries = verified_hint.split(",") if verified_hint else []
            if verified_entries[: len(entries)] != entries or snap_name not in verified_entries:
                raise ValueError("Snap vitality hint verification failed")
            entries = verified_entries
        # snapd overwrites earlier duplicate ranks with the last occurrence.
        rank = len(entries) - entries[::-1].index(snap_name)
        return SNAP_OOM_SCORE_ADJUST_MIN + rank
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise snap.SnapError(f"Failed to configure OOM protection for {snap_name}: {exc}") from exc
