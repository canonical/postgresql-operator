#!/bin/bash
# Diagnostic script for TLS test pg_rewind failure investigation
# This script manually reproduces the TLS test steps and captures detailed logs
# to determine why Patroni's _check_timeline_and_lsn() replication connection fails with /32 rules.
#
# Prerequisites:
#   - A Juju model with 3 postgresql units deployed with our charm change and TLS enabled
#   - Patroni paused, logging_log_connections enabled, primary_start_timeout set to 0
#   - A replica already promoted via pg_ctl promote
#   - Divergent data written to the old primary
#
# Usage:
#   ./diagnose_tls_test.sh <old_primary_unit> <new_primary_unit> <third_unit>
#   Example: ./diagnose_tls_test.sh postgresql/0 postgresql/1 postgresql/2

set -euo pipefail

OLD_PRIMARY="${1:?Usage: $0 <old_primary> <new_primary> <third_unit>}"
NEW_PRIMARY="${2:?}"
THIRD_UNIT="${3:?}"

OLD_NUM="${OLD_PRIMARY##*/}"
NEW_NUM="${NEW_PRIMARY##*/}"
THIRD_NUM="${THIRD_UNIT##*/}"

PG_LOG_DIR="/var/snap/charmed-postgresql/common/var/log/postgresql"
PATRONI_LOG_DIR="/var/snap/charmed-postgresql/common/var/log/patroni"
PATRONI_CONF="/var/snap/charmed-postgresql/current/etc/patroni/patroni.yaml"
PG_HBA="/var/snap/charmed-postgresql/common/var/lib/postgresql/pg_hba.conf"

echo "=== DIAGNOSTIC: TLS test pg_rewind investigation ==="
echo "Old primary: $OLD_PRIMARY (will be killed)"
echo "New primary: $NEW_PRIMARY (manually promoted)"
echo "Third unit:  $THIRD_UNIT"
echo ""

# Step 0: Capture pre-kill state
echo "=== STEP 0: Pre-kill state ==="
echo "--- Patroni cluster status (from $NEW_PRIMARY) ---"
NEW_IP=$(juju show-unit "$NEW_PRIMARY" --format json | python3 -c "import sys,json; print(json.load(sys.stdin)['$NEW_PRIMARY']['address'])")
OLD_IP=$(juju show-unit "$OLD_PRIMARY" --format json | python3 -c "import sys,json; print(json.load(sys.stdin)['$OLD_PRIMARY']['address'])")
THIRD_IP=$(juju show-unit "$THIRD_UNIT" --format json | python3 -c "import sys,json; print(json.load(sys.stdin)['$THIRD_UNIT']['address'])")
echo "Old primary IP: $OLD_IP"
echo "New primary IP: $NEW_IP"
echo "Third unit IP:  $THIRD_IP"
echo ""

PATRONI_PASS=$(juju run "$NEW_PRIMARY" get-password username=patroni --format json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[list(d.keys())[0]]['results']['password'])" 2>/dev/null || echo "unknown")

echo "--- pg_hba.conf on NEW PRIMARY ($NEW_PRIMARY) ---"
juju ssh "$NEW_PRIMARY" "sudo cat $PG_HBA" 2>/dev/null || echo "(could not read)"
echo ""

echo "--- pg_hba replication rules on NEW PRIMARY ---"
juju ssh "$NEW_PRIMARY" "sudo grep replication $PG_HBA" 2>/dev/null || echo "(none found)"
echo ""

echo "--- patroni.yaml pg_hba section on NEW PRIMARY ---"
juju ssh "$NEW_PRIMARY" "sudo grep -A5 'pg_hba' $PATRONI_CONF | head -20" 2>/dev/null || echo "(could not read)"
echo ""

echo "--- patroni.yaml replication rules on OLD PRIMARY ---"
juju ssh "$OLD_PRIMARY" "sudo grep -A5 'pg_hba' $PATRONI_CONF | head -20" 2>/dev/null || echo "(could not read)"
echo ""

echo "--- Patroni cluster status ---"
juju ssh "$NEW_PRIMARY" "curl -sk https://${NEW_IP}:8008/cluster -u patroni:${PATRONI_PASS}" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "(could not reach)"
echo ""

echo "--- PostgreSQL timelines ---"
for UNIT in "$OLD_PRIMARY" "$NEW_PRIMARY" "$THIRD_UNIT"; do
    echo -n "$UNIT: "
    juju ssh "$UNIT" "sudo -u snap_daemon charmed-postgresql.pg-controldata /var/snap/charmed-postgresql/common/var/lib/postgresql/ 2>/dev/null | grep -E 'TimeLineID|cluster state'" 2>/dev/null || echo "(could not read)"
done
echo ""

# Mark current log positions so we can show only NEW entries
echo "=== STEP 1: Mark log positions ==="
for UNIT in "$OLD_PRIMARY" "$NEW_PRIMARY" "$THIRD_UNIT"; do
    NUM="${UNIT##*/}"
    LINE_COUNT=$(juju ssh "$UNIT" "sudo wc -l ${PG_LOG_DIR}/postgresql-*.log 2>/dev/null | tail -1 | awk '{print \$1}'" 2>/dev/null || echo "0")
    eval "PG_LOG_OFFSET_${NUM}=$LINE_COUNT"
    PATRONI_LINES=$(juju ssh "$UNIT" "sudo wc -l ${PATRONI_LOG_DIR}/patroni.log 2>/dev/null | awk '{print \$1}'" 2>/dev/null || echo "0")
    eval "PATRONI_LOG_OFFSET_${NUM}=$PATRONI_LINES"
    echo "$UNIT: PG log offset=$LINE_COUNT, Patroni log offset=$PATRONI_LINES"
done
echo ""

# Step 2: SIGKILL both PG and Patroni on old primary
echo "=== STEP 2: Killing PostgreSQL and Patroni on $OLD_PRIMARY ==="
echo "Killing PostgreSQL..."
juju ssh "$OLD_PRIMARY" "sudo pkill --signal SIGKILL -f '/snap/charmed-postgresql/current/usr/lib/postgresql/14/bin/postgres'" 2>/dev/null || true
echo "Waiting 1 second..."
sleep 1
echo "Killing Patroni..."
juju ssh "$OLD_PRIMARY" "sudo pkill --signal SIGKILL -f '/snap/charmed-postgresql/[0-9]*/usr/bin/patroni'" 2>/dev/null || true
echo "Both killed at $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# Step 3: Monitor for 120 seconds, collecting logs every 10 seconds
echo "=== STEP 3: Monitoring for 120 seconds ==="
for i in $(seq 1 12); do
    sleep 10
    echo ""
    echo "--- CHECK at +$((i * 10))s ($(date -u '+%H:%M:%S UTC')) ---"

    # Check Patroni cluster status
    echo "Cluster status:"
    juju ssh "$NEW_PRIMARY" "curl -sk https://${NEW_IP}:8008/cluster -u patroni:${PATRONI_PASS} 2>/dev/null" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for m in data.get('members', []):
        print(f\"  {m['name']}: role={m.get('role','?')} state={m.get('state','?')} timeline={m.get('timeline','?')} lag={m.get('lag','?')}\")
except:
    print('  (unreachable)')
" 2>/dev/null || echo "  (unreachable)"

    # Check for replication connection attempts/rejections on NEW PRIMARY
    echo "New primary PG log (replication/rewind connections):"
    juju ssh "$NEW_PRIMARY" "sudo tail -100 ${PG_LOG_DIR}/postgresql-*.log 2>/dev/null | grep -iE 'replication|rewind|pg_hba|FATAL|authorized' | tail -10" 2>/dev/null || echo "  (none)"

    # Check old primary Patroni log for rewind-related messages
    echo "Old primary Patroni log (recent):"
    juju ssh "$OLD_PRIMARY" "sudo tail -20 ${PATRONI_LOG_DIR}/patroni.log 2>/dev/null | grep -iE 'rewind|timeline|diverge|leader|lock|PAUSE|recover|pg_hba|connection|error|FATAL'" 2>/dev/null || echo "  (none)"
done

echo ""
echo "=== STEP 4: Full log dump ==="

echo ""
echo "--- FULL NEW PRIMARY PG LOG (new entries only) ---"
OFFSET_VAR="PG_LOG_OFFSET_${NEW_NUM}"
juju ssh "$NEW_PRIMARY" "sudo tail -n +${!OFFSET_VAR:-0} ${PG_LOG_DIR}/postgresql-*.log 2>/dev/null" || echo "(no log)"

echo ""
echo "--- FULL OLD PRIMARY PATRONI LOG (new entries only) ---"
OFFSET_VAR="PATRONI_LOG_OFFSET_${OLD_NUM}"
juju ssh "$OLD_PRIMARY" "sudo tail -n +${!OFFSET_VAR:-0} ${PATRONI_LOG_DIR}/patroni.log 2>/dev/null" || echo "(no log)"

echo ""
echo "--- FULL NEW PRIMARY PATRONI LOG (new entries only) ---"
OFFSET_VAR="PATRONI_LOG_OFFSET_${NEW_NUM}"
juju ssh "$NEW_PRIMARY" "sudo tail -n +${!OFFSET_VAR:-0} ${PATRONI_LOG_DIR}/patroni.log 2>/dev/null" || echo "(no log)"

echo ""
echo "--- FULL THIRD UNIT PATRONI LOG (new entries only) ---"
OFFSET_VAR="PATRONI_LOG_OFFSET_${THIRD_NUM}"
juju ssh "$THIRD_UNIT" "sudo tail -n +${!OFFSET_VAR:-0} ${PATRONI_LOG_DIR}/patroni.log 2>/dev/null" || echo "(no log)"

echo ""
echo "--- pg_hba.conf on NEW PRIMARY (post-test) ---"
juju ssh "$NEW_PRIMARY" "sudo cat $PG_HBA" 2>/dev/null || echo "(could not read)"

echo ""
echo "--- pg_hba.conf on OLD PRIMARY (post-test) ---"
juju ssh "$OLD_PRIMARY" "sudo cat $PG_HBA" 2>/dev/null || echo "(could not read)"

echo ""
echo "=== DIAGNOSTIC COMPLETE ==="
echo ""
echo "KEY THINGS TO LOOK FOR:"
echo "1. 'FATAL: no pg_hba.conf entry for replication connection from host' on new primary PG log"
echo "   → Confirms pg_hba /32 rules are blocking Patroni's timeline check"
echo "2. 'connection authorized: user=replication' on new primary PG log"
echo "   → Replication connection from old primary succeeded"
echo "3. Old primary Patroni log: 'check_timeline_and_lsn' or 'rewind' messages"
echo "   → Shows whether Patroni attempted the timeline check"
echo "4. Old primary Patroni log: 'removed leader lock' message"
echo "   → Confirms leader key was deleted"
echo "5. New primary Patroni log: 'acquired session lock as a leader'"
echo "   → Confirms new primary acquired the DCS leader key"
