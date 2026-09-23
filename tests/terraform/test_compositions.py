# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Static constraint-composition tests for the postgresql terraform module.

Each case sources the module from a throwaway root that declares a sibling
``juju`` provider constraint, runs ``terraform init``, and asserts the resolved
provider major (or that an unsatisfiable pairing fails cleanly). Guards the
module's ``required_providers`` constraint against a downstream constraint it
cannot compose with.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_MODULE = REPO_ROOT / "terraform"
TF_BINARY = os.getenv("TF_BINARY") or "terraform"
TF_TIMEOUT = 5 * 60

pytestmark = pytest.mark.skipif(
    shutil.which(TF_BINARY) is None, reason=f"{TF_BINARY} not found on PATH"
)

_INSTALLED_RE = re.compile(r"Installed juju/juju v(\d+)\.(\d+)\.(\d+)")

# (root juju provider constraint, expect init success, resolved provider major).
# The module pins `~> 1.0`, so it composes with any sibling constraint that stays inside the
# v1 line: v1_consumer pins the lower bound, and an open-ended `>= 1.1.1` sibling still resolves
# within v1. A `>= 2.0` consumer cannot compose with that pin — assert it fails cleanly rather
# than silently resolving, so widening the module's constraint is a deliberate change here too.
CASES = [
    pytest.param("~> 1.0", True, "1", id="v1_consumer"),
    pytest.param(">= 1.1.1", True, "1", id="open_ended_sibling"),
    pytest.param(">= 2.0", False, None, id="v2_consumer_is_capped_out"),
    pytest.param(">= 99.0", False, None, id="unsatisfiable"),
]


def _run_terraform(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    # Bound each call so a registry stall fails instead of hanging (matches the deploy helper).
    return subprocess.run(
        [TF_BINARY, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=TF_TIMEOUT,
    )


def _write_root(root: Path, module: Path, constraint: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "versions.tf").write_text(
        f"terraform {{\n"
        f'  required_version = ">= 1.6.6"\n'
        f"  required_providers {{\n"
        f"    juju = {{\n"
        f'      source  = "juju/juju"\n'
        f'      version = "{constraint}"\n'
        f"    }}\n"
        f"  }}\n"
        f"}}\n"
    )
    (root / "main.tf").write_text(
        f'module "postgresql" {{\n'
        f'  source     = "{module}/"\n'
        f'  model_uuid = "unused-during-init"\n'
        f"}}\n"
    )


@pytest.mark.parametrize("constraint,expect_ok,expect_major", CASES)
def test_composition_resolves_provider(
    tmp_path: Path, constraint: str, expect_ok: bool, expect_major: str | None
) -> None:
    """Assert the module's constraint composes with a sibling juju constraint."""
    root = tmp_path / "root"
    _write_root(root, TERRAFORM_MODULE, constraint)

    init = _run_terraform(root, "init", "-backend=false", "-input=false")

    if expect_ok:
        assert init.returncode == 0, f"init failed:\n{init.stderr}{init.stdout}"
        match = _INSTALLED_RE.search(init.stdout)
        assert match is not None, f"no resolved provider in output:\n{init.stdout}"
        assert match.group(1) == expect_major, (
            f"resolved {match.group(0)}, expected major {expect_major}"
        )
    else:
        assert init.returncode != 0, f"expected init to fail but it succeeded:\n{init.stdout}"
        # Assert the failure reason is an unsatisfiable constraint, not an unrelated error.
        assert "no available releases match" in (init.stderr + init.stdout), (
            f"init failed for an unexpected reason:\n{init.stderr}{init.stdout}"
        )
