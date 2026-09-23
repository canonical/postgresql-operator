#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Real-deploy integration test for the postgresql terraform module.

Applies the module into the pre-created ``testing`` model and waits for
active/idle. The module pins the juju provider to the v1 line, so there is a
single deploy leg; the resolved provider major is asserted before applying.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import jubilant
import pytest

from .. import architecture

_JUJU_PROVIDER = "registry.terraform.io/juju/juju"
# Major admitted by the module's `required_providers` constraint (`~> 1.0`). Kept in sync with
# tests/terraform/test_compositions.py, which asserts the same cap statically.
EXPECTED_PROVIDER_MAJOR = "1"

REPO_ROOT = Path(__file__).resolve().parents[3]
TERRAFORM_MODULE = REPO_ROOT / "terraform"
APP = "postgresql"
TIMEOUT = 20 * 60
# `terraform apply` blocks until the charm's units are created, so give it the deploy budget.
TF_TIMEOUT = 15 * 60
TF_BINARY = os.getenv("TF_BINARY") or "terraform"
# Storage directives for the postgresql charm: archive, data, logs, temp — drives the `storage`
# variable (the machine module's name for `storage_directives`).
STORAGE = '{"data"="2G","archive"="1G","logs"="1G","temp"="1G"}'
# A string-typed postgresql config option (profile) — drives the `config` variable.
CONFIG = '{"profile"="testing"}'


def _run_terraform(
    cwd: Path, timeout: int, *args: str, capture: bool = False
) -> subprocess.CompletedProcess:
    # Stream by default so the slow init/apply show live progress; capture only when the
    # caller reads stdout (else `.stdout` is None). Timeout so a stall fails fast.
    return subprocess.run(
        [TF_BINARY, *args],
        cwd=str(cwd),
        check=True,
        timeout=timeout,
        capture_output=capture,
        text=capture,
    )


def test_terraform_apply_deploys_postgresql(juju: jubilant.Juju) -> None:
    """The module must apply postgresql with storage/config, reach active/idle, and expose outputs."""
    if shutil.which(TF_BINARY) is None:
        pytest.skip(f"{TF_BINARY} not found on PATH")

    model_uuid = juju.show_model().model_uuid

    _run_terraform(TERRAFORM_MODULE, TF_TIMEOUT, "init", "-input=false")

    # Guard against the module's provider pin drifting unnoticed: assert init resolved the
    # juju provider major the module's `required_providers` constraint admits.
    versions = _run_terraform(TERRAFORM_MODULE, TF_TIMEOUT, "version", "-json", capture=True)
    resolved = json.loads(versions.stdout)["provider_selections"][_JUJU_PROVIDER]
    assert resolved.split(".")[0] == EXPECTED_PROVIDER_MAJOR, (
        f"expected juju provider major {EXPECTED_PROVIDER_MAJOR}, resolved {resolved}"
    )

    _run_terraform(
        TERRAFORM_MODULE,
        TF_TIMEOUT,
        "apply",
        "-auto-approve",
        "-input=false",
        "-var",
        f"model_uuid={model_uuid}",
        # Deploy for the runner's arch, not the module's default `arch=amd64` (unschedulable on arm64).
        "-var",
        f"constraints=arch={architecture.architecture}",
        "-var",
        f"storage={STORAGE}",
        "-var",
        f"config={CONFIG}",
    )

    juju.wait(
        lambda status: jubilant.all_active(status, APP) and jubilant.all_agents_idle(status, APP),
        error=lambda status: jubilant.any_error(status, APP),
        timeout=TIMEOUT,
    )

    # The module exposes an `application_name` output; assert it reflects the deployed app.
    # capture=True so `.stdout` holds the value instead of streaming to the log.
    output = _run_terraform(
        TERRAFORM_MODULE, TF_TIMEOUT, "output", "-raw", "application_name", capture=True
    )
    assert output.stdout.strip() == APP, f"application_name output: {output.stdout!r}"
