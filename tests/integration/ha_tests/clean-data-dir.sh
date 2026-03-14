#!/usr/bin/env bash

set -Eeuo pipefail
find /var/snap/charmed-postgresql/common/data/archive -mindepth 1 -delete 2>/dev/null || true
find /var/snap/charmed-postgresql/common/var/lib/postgresql -mindepth 1 -delete
find /var/snap/charmed-postgresql/common/data/logs -mindepth 1 -delete 2>/dev/null || true
find /var/snap/charmed-postgresql/common/data/temp -mindepth 1 -delete 2>/dev/null || true
