# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""PostgreSQL Workload.

This module contains the PostgreSQL workloads implementations.
In general each charm and substrate combination have its own workload implementation,
they all inherit from the BaseWorkload class and implement the same interface.

A rule of thumb is to have all operations that interact with the actual
PostgreSQL database in the workload, for example file operations, scripts, commands, etc.
"""
