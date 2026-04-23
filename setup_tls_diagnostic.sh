#!/bin/bash
# Setup script for TLS test diagnostic
# Deploys the charm, enables TLS, pauses Patroni, promotes a replica, writes divergent data.
# After this script completes, run diagnose_tls_test.sh with the unit names shown.
#
# Usage:
#   ./setup_tls_diagnostic.sh <charm_file> [model_name]
#   Example: ./setup_tls_diagnostic.sh ./postgresql_ubuntu-22.04-amd64.charm tls-diag

set -euo pipefail

CHARM="${1:?Usage: $0 <charm_file> [model_name]}"
MODEL="${2:-tls-diag-$(date +%s)}"

echo "=== TLS Diagnostic Setup ==="
echo "Charm: $CHARM"
echo "Model: $MODEL"
echo ""

# Create model
echo "--- Creating model $MODEL ---"
juju add-model "$MODEL" 2>/dev/null || true
juju switch "$MODEL"
juju model-config update-status-hook-interval=1m
echo ""

# Deploy
echo "--- Deploying PostgreSQL (3 units) ---"
juju deploy "$CHARM" postgresql --num-units 3 --base ubuntu@22.04 --config profile=testing
echo "Waiting for active/idle..."
juju wait-for application postgresql --query='name=="postgresql" && status=="active"' --timeout 15m 2>/dev/null || \
    juju-wait -t 900 2>/dev/null || \
    sleep 120

echo ""
echo "--- Deploying self-signed-certificates ---"
juju deploy self-signed-certificates --config ca-common-name="Test CA" --channel 1/stable --base ubuntu@24.04
echo "Waiting for certificates to deploy..."
sleep 30

echo ""
echo "--- Integrating TLS ---"
juju integrate postgresql:certificates self-signed-certificates:certificates
echo "Waiting for active/idle with TLS..."
juju wait-for application postgresql --query='name=="postgresql" && status=="active"' --timeout 10m 2>/dev/null || \
    juju-wait -t 600 2>/dev/null || \
    sleep 180

echo ""
echo "--- Current status ---"
juju status --relations

# Identify units
PRIMARY=$(juju run postgresql/0 get-primary --format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[list(d.keys())[0]]['results']['primary'])" 2>/dev/null)
if [ -z "$PRIMARY" ]; then
    echo "ERROR: Could not determine primary. Check juju status."
    exit 1
fi

# Pick a replica to promote
REPLICA=""
THIRD=""
for UNIT in postgresql/0 postgresql/1 postgresql/2; do
    if [ "$UNIT" != "$PRIMARY" ]; then
        if [ -z "$REPLICA" ]; then
            REPLICA="$UNIT"
        else
            THIRD="$UNIT"
        fi
    fi
done

echo ""
echo "Primary:        $PRIMARY (will be killed)"
echo "Replica:        $REPLICA (will be promoted)"
echo "Third unit:     $THIRD"

# Get IPs
PRIMARY_IP=$(juju show-unit "$PRIMARY" --format json | python3 -c "import sys,json; print(json.load(sys.stdin)['$PRIMARY']['address'])")
REPLICA_IP=$(juju show-unit "$REPLICA" --format json | python3 -c "import sys,json; print(json.load(sys.stdin)['$REPLICA']['address'])")

# Get Patroni password
PATRONI_PASS=$(juju run "$PRIMARY" get-password username=patroni --format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[list(d.keys())[0]]['results']['password'])")
OPERATOR_PASS=$(juju run "$PRIMARY" get-password --format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[list(d.keys())[0]]['results']['password'])")

echo ""
echo "--- Enabling log_connections ---"
juju config postgresql logging_log_connections=True
sleep 10
juju wait-for application postgresql --query='name=="postgresql" && status=="active"' --timeout 5m 2>/dev/null || sleep 60

echo ""
echo "--- Setting primary_start_timeout=0 ---"
curl -sk "https://${PRIMARY_IP}:8008/config" \
    -XPATCH -H "Content-Type: application/json" \
    -d '{"primary_start_timeout": 0}' \
    -u "patroni:${PATRONI_PASS}" || echo "(warning: could not set)"

echo ""
echo "--- Pausing Patroni ---"
curl -sk "https://${PRIMARY_IP}:8008/config" \
    -XPATCH -H "Content-Type: application/json" \
    -d '{"pause": true}' \
    -u "patroni:${PATRONI_PASS}" || echo "(warning: could not pause)"
sleep 5

# Verify pause
echo ""
echo "--- Verifying pause ---"
curl -sk "https://${PRIMARY_IP}:8008/cluster" -u "patroni:${PATRONI_PASS}" | python3 -m json.tool 2>/dev/null || echo "(could not reach)"

echo ""
echo "--- Promoting $REPLICA ---"
for i in $(seq 1 5); do
    juju ssh "$REPLICA" "sudo -u snap_daemon charmed-postgresql.pg-ctl -D /var/snap/charmed-postgresql/common/var/lib/postgresql/ promote" 2>/dev/null && break
    echo "  Retry $i..."
    sleep 3
done

# Verify promotion
sleep 3
echo "Checking if $REPLICA is now primary:"
juju ssh "$REPLICA" "sudo -u snap_daemon charmed-postgresql.psql -h /tmp -c 'SELECT pg_is_in_recovery();'" 2>/dev/null || echo "(check failed)"

echo ""
echo "--- Writing divergent data to $PRIMARY ---"
juju ssh "$PRIMARY" "sudo -u snap_daemon charmed-postgresql.psql -h /tmp -d postgres -c 'CREATE TABLE IF NOT EXISTS pgrewindtest (testcol INT);'" 2>/dev/null
juju ssh "$PRIMARY" "sudo -u snap_daemon charmed-postgresql.psql -h /tmp -d postgres -c 'INSERT INTO pgrewindtest SELECT generate_series(1,1000);'" 2>/dev/null
echo "Divergent data written."

echo ""
echo "--- Showing pg_hba.conf on all units ---"
for UNIT in "$PRIMARY" "$REPLICA" "$THIRD"; do
    echo ""
    echo "pg_hba.conf on $UNIT (replication rules):"
    juju ssh "$UNIT" "sudo grep replication /var/snap/charmed-postgresql/common/var/lib/postgresql/pg_hba.conf" 2>/dev/null || echo "(none)"
done

echo ""
echo "--- Showing patroni.yaml peer rules on all units ---"
for UNIT in "$PRIMARY" "$REPLICA" "$THIRD"; do
    echo ""
    echo "patroni.yaml replication rules on $UNIT:"
    juju ssh "$UNIT" "sudo grep -E 'replication.*replication.*/' /var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml" 2>/dev/null || echo "(none)"
done

echo ""
echo "=============================================="
echo "SETUP COMPLETE"
echo "=============================================="
echo ""
echo "Now run the diagnostic script:"
echo "  ./diagnose_tls_test.sh $PRIMARY $REPLICA $THIRD"
echo ""
echo "This will SIGKILL PG+Patroni on $PRIMARY and monitor the recovery."
echo "Look for 'FATAL: no pg_hba.conf entry for replication' in the output."
