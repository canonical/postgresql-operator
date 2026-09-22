# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import subprocess
from unittest.mock import call, patch

import pytest
from charmlibs import snap

import oom


def config(hint):
    return json.dumps({"resilience": {"vitality-hint": hint}})


@pytest.fixture
def snap_commands():
    with patch("oom.subprocess.check_output") as read, patch("oom.subprocess.check_call") as write:
        yield read, write


@pytest.mark.parametrize("initial", ["{}", '{"resilience": {}}', config("")])
def test_missing_or_empty_hint(snap_commands, initial):
    read, write = snap_commands
    read.side_effect = [initial, config("charmed-postgresql")]

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -899

    assert read.call_args_list == [call(["/usr/bin/snap", "get", "system", "-d"], text=True)] * 2
    write.assert_called_once_with([
        "/usr/bin/snap",
        "set",
        "system",
        "resilience.vitality-hint=charmed-postgresql",
    ])


@pytest.mark.parametrize(
    "hint,rank",
    [
        ("postgresql", 2),
        ("unrelated,charmed-postgresql-other,unrelated", 4),
        (",".join(f"snap-{i}" for i in range(99)), 100),
    ],
)
def test_append_preserves_order_and_duplicates(snap_commands, hint, rank):
    read, write = snap_commands
    updated = f"{hint},charmed-postgresql"
    read.side_effect = [config(hint), config(updated), config(updated)]

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank
    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank

    write.assert_called_once_with([
        "/usr/bin/snap",
        "set",
        "system",
        f"resilience.vitality-hint={updated}",
    ])


@pytest.mark.parametrize(
    "hint,rank",
    [
        ("charmed-postgresql", 1),
        ("charmed-postgresql,postgresql", 1),
        ("postgresql,charmed-postgresql", 2),
        ("charmed-postgresql,postgresql,charmed-postgresql,other", 3),
        (",".join(["other"] * 99 + ["charmed-postgresql"]), 100),
    ],
)
def test_existing_hint_uses_last_rank_without_write(snap_commands, hint, rank):
    read, write = snap_commands
    read.return_value = config(hint)

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank
    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank

    assert read.call_count == 2
    write.assert_not_called()


def test_full_hint_does_not_write(snap_commands):
    read, write = snap_commands
    read.return_value = config(",".join(f"snap-{i}" for i in range(100)))

    with pytest.raises(snap.SnapError, match="full"):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    write.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        "null",
        "[]",
        '{"resilience": null}',
        '{"resilience": []}',
        config(None),
        config([]),
        config({}),
        config(42),
        config(False),
        config(",".join(["charmed-postgresql"] * 101)),
    ],
)
def test_invalid_configuration_does_not_write(snap_commands, value):
    read, write = snap_commands
    read.return_value = value

    with pytest.raises(snap.SnapError):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    write.assert_not_called()


@pytest.mark.parametrize(
    "error", [OSError("snap missing"), subprocess.CalledProcessError(1, "snap get")]
)
def test_read_failure_does_not_write(snap_commands, error):
    read, write = snap_commands
    read.side_effect = error

    with pytest.raises(snap.SnapError):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    write.assert_not_called()


@pytest.mark.parametrize(
    "error", [OSError("snap missing"), subprocess.CalledProcessError(1, "snap set")]
)
def test_write_failure_does_not_retry(snap_commands, error):
    read, write = snap_commands
    read.return_value = config("postgresql")
    write.side_effect = error

    with pytest.raises(snap.SnapError):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    assert read.call_count == 1
    assert write.call_count == 1


@pytest.mark.parametrize(
    "verified",
    [
        config("postgresql,other"),
        config("charmed-postgresql,postgresql,other"),
        config("postgresql,charmed-postgresql"),
        config("postgresql,other,other,charmed-postgresql"),
        config(""),
        config(None),
        "bad-json",
        subprocess.CalledProcessError(1, "snap get"),
    ],
)
def test_verification_failure_does_not_overwrite_again(snap_commands, verified):
    read, write = snap_commands
    read.side_effect = [config("postgresql,other,postgresql"), verified]

    with pytest.raises(snap.SnapError):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    write.assert_called_once_with([
        "/usr/bin/snap",
        "set",
        "system",
        "resilience.vitality-hint=postgresql,other,postgresql,charmed-postgresql",
    ])


def test_rank_comes_from_verified_configuration(snap_commands):
    read, _ = snap_commands
    read.side_effect = [
        config("postgresql"),
        config("postgresql,charmed-postgresql,other,charmed-postgresql"),
    ]

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -896
