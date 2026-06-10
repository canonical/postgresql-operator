#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for Charm."""

from single_kernel_postgresql.config.literals import SECRET_KEY_OVERRIDES


def translate_field_to_secret_key(key: str) -> str:
    """Change 'key' to secrets-compatible key field."""
    key = SECRET_KEY_OVERRIDES.get(key, key)
    new_key = key.replace("_", "-")
    return new_key.strip("-")
