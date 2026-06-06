#!/usr/bin/env bash
# scripts/e2e_pg.sh — End-to-end test harness for the PostgreSQL add-on.
#
# Spins up the PostgreSQL add-on image, waits for it to be ready, then runs
# SQL assertions via psql inside the container.  No external mock needed —
# we simulate the HA recorder pattern directly with SQL.
#
# PROXY NOTE: same as e2e.sh — use --http-proxy=false on every podman run.
#
# Usage:
#   bash scripts/e2e_pg.sh
#
# Knobs (env vars with defaults):
#   PG_IMAGE          Postgres add-on image  (default: localhost/pg-addon:local)
#   NET               Podman network name    (default: pg_e2e_net)
#   PG_CONTAINER      Container name         (default: pg_e2e_pg)
#   PG_PORT           Host port → PG 5432   (default: 15432)
#   POLL_TIMEOUT      Seconds to wait        (default: 120)
#   BUILD_IF_MISSING  Build if absent        (default: 1)

set -euo pipefail

PG_IMAGE="${PG_IMAGE:-localhost/pg-addon:local}"
NET="${NET:-pg_e2e_net}"
PG_CONTAINER="${PG_CONTAINER:-pg_e2e_pg}"
PG_PORT="${PG_PORT:-15432}"
POLL_TIMEOUT="${POLL_TIMEOUT:-120}"
BUILD_IF_MISSING="${BUILD_IF_MISSING:-1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

teardown() {
    echo "==> Teardown"
    podman rm -f "${PG_CONTAINER}" 2>/dev/null || true
    podman network rm "${NET}"    2>/dev/null || true
    echo "==> Teardown done"
}

on_exit() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        echo ""
        echo "==> E2E FAIL — dumping PostgreSQL logs:"
        podman logs --tail 60 "${PG_CONTAINER}" 2>&1 || true
    fi
    teardown
    exit $exit_code
}
trap on_exit EXIT

# ── Pre-flight cleanup ────────────────────────────────────────────────────────
echo "==> Pre-flight cleanup"
podman rm -f "${PG_CONTAINER}" 2>/dev/null || true
podman network rm "${NET}"    2>/dev/null || true

# ── Build image if missing ────────────────────────────────────────────────────
if [[ "${BUILD_IF_MISSING}" == "1" ]] && ! podman image exists "${PG_IMAGE}"; then
    echo "==> Building PostgreSQL add-on image (this takes ~30 min — compiles from source)..."
    podman build \
        --http-proxy=false \
        --build-arg BUILD_FROM=ghcr.io/hassio-addons/base/amd64:17.2.1 \
        -t "${PG_IMAGE}" \
        "${REPO_ROOT}/postgresql"
    echo "==> Build done: ${PG_IMAGE}"
else
    echo "==> Using existing image: ${PG_IMAGE}"
fi

# ── Network ───────────────────────────────────────────────────────────────────
podman network create "${NET}"

# ── Start PostgreSQL ──────────────────────────────────────────────────────────
echo "==> Starting PostgreSQL container"
podman run -d \
    --name "${PG_CONTAINER}" \
    --network "${NET}" \
    --http-proxy=false \
    -p "127.0.0.1:${PG_PORT}:5432" \
    -e POSTGRES_USER=homeassistant \
    -e POSTGRES_PASSWORD=homeassistant \
    -e POSTGRES_DB=homeassistant \
    "${PG_IMAGE}"

# ── Wait for ready ────────────────────────────────────────────────────────────
echo -n "==> Waiting for PostgreSQL to be ready"
pg_ready=0
for i in $(seq 1 $(( POLL_TIMEOUT / 2 ))); do
    if podman exec "${PG_CONTAINER}" \
           pg_isready -U homeassistant -d homeassistant -q 2>/dev/null; then
        echo " OK (${i}×2s)"
        pg_ready=1
        break
    fi
    echo -n "."
    sleep 2
done
if [[ $pg_ready -ne 1 ]]; then
    echo " TIMEOUT"
    exit 1
fi
# Extra second so initdb scripts (inventory.sql) finish running.
sleep 2

# ── SQL helper ────────────────────────────────────────────────────────────────
pg_sql() {
    podman exec "${PG_CONTAINER}" \
        psql -U homeassistant -d homeassistant -tAc "$1" 2>/dev/null
}

# ── Assertions ────────────────────────────────────────────────────────────────
fail_count=0

assert_eq() {
    local label="$1" expected="$2"
    local actual="${3//[[:space:]]/}"
    if [[ "${actual}" == "${expected}" ]]; then
        echo "  PASS  ${label} (got: ${actual})"
    else
        echo "  FAIL  ${label} — expected '${expected}', got '${actual}'"
        (( fail_count++ )) || true
    fi
}

assert_gt() {
    local label="$1" threshold="$2"
    local actual="${3//[[:space:]]/}"
    if [[ "${actual}" =~ ^[0-9]+$ ]] && [[ "${actual}" -gt "${threshold}" ]]; then
        echo "  PASS  ${label} (got: ${actual})"
    else
        echo "  FAIL  ${label} — expected > ${threshold}, got '${actual}'"
        (( fail_count++ )) || true
    fi
}

echo ""
echo "==> Running assertions"

# A1: basic connectivity
assert_eq "SELECT 1 returns 1" \
    "1" "$(pg_sql 'SELECT 1')"

# A2: homeassistant database exists
assert_eq "homeassistant database exists" \
    "1" "$(pg_sql "SELECT count(*) FROM pg_database WHERE datname='homeassistant'")"

# A3: homeassistant user exists
assert_eq "homeassistant user exists" \
    "1" "$(pg_sql "SELECT count(*) FROM pg_roles WHERE rolname='homeassistant'")"

# A4: pg_ivm extension available in the catalogue
assert_eq "pg_ivm extension available" \
    "1" "$(pg_sql "SELECT count(*) FROM pg_available_extensions WHERE name='pg_ivm'")"

# A5: pg_ivm extension actually installed (inventory.sql ran at initdb)
assert_eq "pg_ivm extension installed" \
    "1" "$(pg_sql "SELECT count(*) FROM pg_extension WHERE extname='pg_ivm'")"

# A6: wal_level must be logical (required for ClickHouse MaterializedPostgreSQL)
assert_eq "wal_level is logical" \
    "logical" "$(pg_sql 'SHOW wal_level')"

# A7: simulate HA recorder — write + read states
pg_sql "
CREATE TABLE IF NOT EXISTS states (
    id            BIGSERIAL PRIMARY KEY,
    entity_id     TEXT NOT NULL,
    state         TEXT,
    attributes    JSONB,
    last_changed  TIMESTAMPTZ DEFAULT now(),
    last_updated  TIMESTAMPTZ DEFAULT now()
);
INSERT INTO states (entity_id, state, attributes) VALUES
  ('sensor.temperature', '22.5', '{\"unit_of_measurement\":\"°C\"}'),
  ('sensor.humidity',    '60',   '{\"unit_of_measurement\":\"%\"}'),
  ('binary_sensor.door', 'on',   '{}'),
  ('sensor.temperature', '23.1', '{\"unit_of_measurement\":\"°C\"}');
" >/dev/null

assert_gt "recorder: sensor rows written and readable" \
    0 "$(pg_sql "SELECT count(*) FROM states WHERE entity_id LIKE 'sensor.%'")"

assert_eq "recorder: binary_sensor row present" \
    "1" "$(pg_sql "SELECT count(*) FROM states WHERE entity_id='binary_sensor.door'")"

# A8: JSONB attributes accessible
assert_eq "JSONB attribute query works" \
    "°C" "$(pg_sql "SELECT attributes->>'unit_of_measurement' FROM states WHERE entity_id='sensor.temperature' LIMIT 1")"

# ── Report ────────────────────────────────────────────────────────────────────
echo ""
if [[ $fail_count -eq 0 ]]; then
    echo "E2E PASS — all assertions passed"
    exit 0
else
    echo "E2E FAIL: ${fail_count} assertion(s) failed"
    exit 1
fi
