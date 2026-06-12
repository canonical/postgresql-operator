# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path
from typing import get_args

from single_kernel_postgresql.config.locales import LOCALES


def test_locales_fixture_matches_library() -> None:
    """The integration locale fixture must mirror the library's LOCALES.

    The integration tests cannot import the library (it is absent from their
    dependency group), so they assert against this committed snapshot instead.
    This guard fails CI if the snapshot drifts from the library. Regenerate it
    from the repo root after a library locale change:
        python -c "from typing import get_args; \
from single_kernel_postgresql.config.locales import LOCALES; \
print(chr(10).join(get_args(LOCALES)))" > tests/integration/locales.txt
    """
    fixture = (Path(__file__).parents[1] / "integration" / "locales.txt").read_text().splitlines()
    assert fixture == list(get_args(LOCALES))
