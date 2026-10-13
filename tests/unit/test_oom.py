# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from http.client import IncompleteRead
from unittest.mock import call, patch

import pytest
from charmlibs import snap

import oom


def config(hint):
    return {"resilience": {"vitality-hint": hint}}


@pytest.fixture
def snap_factory():
    with patch("oom.snap.Snap", autospec=True) as factory:
        yield factory


@pytest.mark.parametrize("initial", [{}, {"resilience": {}}, config("")])
def test_missing_or_empty_hint(snap_factory, initial):
    read = snap_factory.return_value.get
    read.side_effect = [initial, config("charmed-postgresql")]

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -899

    snap_factory.assert_called_once_with(
        name="system", state=snap.SnapState.Present, channel="", revision="", confinement=""
    )
    assert snap_factory.return_value.mock_calls == [
        call.get(None, typed=True),
        call.set({"resilience.vitality-hint": "charmed-postgresql"}),
        call.get(None, typed=True),
    ]


@pytest.mark.parametrize(
    "hint,rank",
    [
        ("postgresql", 2),
        ("unrelated,charmed-postgresql-other,unrelated", 4),
        (",".join(f"snap-{i}" for i in range(99)), 100),
    ],
)
def test_append_preserves_order_and_duplicates(snap_factory, hint, rank):
    read, write = snap_factory.return_value.get, snap_factory.return_value.set
    updated = f"{hint},charmed-postgresql"
    read.side_effect = [config(hint), config(updated), config(updated)]

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank
    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank

    assert snap_factory.call_count == 2
    write.assert_called_once_with({"resilience.vitality-hint": updated})


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
def test_existing_hint_uses_last_rank_without_write(snap_factory, hint, rank):
    read, write = snap_factory.return_value.get, snap_factory.return_value.set
    read.return_value = config(hint)

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank
    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -900 + rank

    assert snap_factory.call_count == 2
    assert read.call_args_list == [call(None, typed=True)] * 2
    write.assert_not_called()


def test_full_hint_does_not_write(snap_factory):
    read, write = snap_factory.return_value.get, snap_factory.return_value.set
    read.return_value = config(",".join(f"snap-{i}" for i in range(100)))

    with pytest.raises(snap.SnapError, match="full"):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    write.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        None,
        [],
        {"resilience": None},
        {"resilience": []},
        config(None),
        config([]),
        config({}),
        config(42),
        config(False),
        config(",".join(["charmed-postgresql"] * 101)),
    ],
)
def test_invalid_configuration_does_not_write(snap_factory, value):
    read, write = snap_factory.return_value.get, snap_factory.return_value.set
    read.return_value = value

    with pytest.raises(snap.SnapError):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    write.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        OSError("snap missing"),
        snap.SnapError("snap get failed"),
        snap.SnapAPIError({}, 500, "Internal Server Error", "snapd unavailable"),
        json.JSONDecodeError("invalid system configuration", "", 0),
    ],
)
def test_read_failure_does_not_write(snap_factory, error):
    read, write = snap_factory.return_value.get, snap_factory.return_value.set
    read.side_effect = error

    with pytest.raises(snap.SnapError) as exc:
        oom.ensure_snap_oom_protection("charmed-postgresql")

    assert exc.value.__cause__ is error
    write.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        OSError("snap socket unavailable"),
        IncompleteRead(b"truncated snapd response"),
        snap.SnapError("configuration change failed"),
        snap.SnapAPIError({}, 500, "Internal Server Error", "snapd unavailable"),
        TimeoutError("configuration change timed out"),
    ],
)
def test_write_failure_does_not_retry(snap_factory, error):
    read, write = snap_factory.return_value.get, snap_factory.return_value.set
    read.return_value = config("postgresql")
    write.side_effect = error

    with pytest.raises(snap.SnapError) as exc:
        oom.ensure_snap_oom_protection("charmed-postgresql")

    assert exc.value.__cause__ is error
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
        json.JSONDecodeError("invalid system configuration", "", 0),
        snap.SnapError("verification read failed"),
        snap.SnapAPIError({}, 500, "Internal Server Error", "snapd unavailable"),
    ],
)
def test_verification_failure_does_not_overwrite_again(snap_factory, verified):
    read, write = snap_factory.return_value.get, snap_factory.return_value.set
    read.side_effect = [config("postgresql,other,postgresql"), verified]

    with pytest.raises(snap.SnapError):
        oom.ensure_snap_oom_protection("charmed-postgresql")

    assert read.call_count == 2
    write.assert_called_once_with({
        "resilience.vitality-hint": "postgresql,other,postgresql,charmed-postgresql"
    })


def test_rank_comes_from_verified_configuration(snap_factory):
    read = snap_factory.return_value.get
    read.side_effect = [
        config("postgresql"),
        config("postgresql,charmed-postgresql,other,charmed-postgresql"),
    ]

    assert oom.ensure_snap_oom_protection("charmed-postgresql") == -896
