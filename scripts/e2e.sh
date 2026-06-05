#!/usr/bin/env bash
# scripts/e2e.sh — End-to-end test harness for the ClickHouse Ingestor add-on.
#
# Spins up:
#   1. A ClickHouse container (from CH_IMAGE)
#   2. A mock HA WebSocket server (Python, mounted into the ingestor image)
#   3. The ingestor itself (ingestor image, connects mock ↔ ClickHouse)
#
# Then polls ClickHouse and asserts expected rows before tearing everything down.
#
# PROXY NOTE
# ----------
# The host shell exports http_proxy/https_proxy pointing at an external proxy
# that cannot reach localhost or the podman bridge network.  We must:
#   - Pass --http-proxy=false to every `podman run` so the proxy env vars are
#     NOT injected into containers (otherwise ingestor HTTP → CH gets 502).
#   - Use `curl --noproxy '*'` for every probe against 127.0.0.1.
#
# Usage:
#   bash scripts/e2e.sh
#
# Knobs (env vars with defaults):
#   CH_IMAGE        ClickHouse server image  (default: docker.io/clickhouse/clickhouse-server:24.8)
#   INGESTOR_IMAGE  Ingestor image           (default: localhost/ch-ingestor:local)
#   NET             Podman network name      (default: ch_e2e_net)
#   CH_CONTAINER    ClickHouse container     (default: ch_e2e_ch)
#   MOCK_CONTAINER  Mock WS container        (default: ch_e2e_mock)
#   INGESTOR_CONT   Ingestor container       (default: ch_e2e_ingestor)
#   CH_HTTP_PORT    Host port → CH 8123      (default: 18123)
#   MOCK_PORT       Mock WS port (in-net)    (default: 8765)
#   POLL_TIMEOUT    Seconds to wait for rows (default: 60)
#   BUILD_IF_MISSING  Build ingestor if absent (default: 1)

set -euo pipefail

# ── Configurable defaults ─────────────────────────────────────────────────────
CH_IMAGE="${CH_IMAGE:-docker.io/clickhouse/clickhouse-server:24.8}"
INGESTOR_IMAGE="${INGESTOR_IMAGE:-localhost/ch-ingestor:local}"
NET="${NET:-ch_e2e_net}"
CH_CONTAINER="${CH_CONTAINER:-ch_e2e_ch}"
MOCK_CONTAINER="${MOCK_CONTAINER:-ch_e2e_mock}"
INGESTOR_CONT="${INGESTOR_CONT:-ch_e2e_ingestor}"
CH_HTTP_PORT="${CH_HTTP_PORT:-18123}"
MOCK_PORT="${MOCK_PORT:-8765}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"
BUILD_IF_MISSING="${BUILD_IF_MISSING:-1}"

# Resolve repo root (the script lives in scripts/, so go one level up)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Teardown (always runs, idempotent) ────────────────────────────────────────
teardown() {
    echo "==> Teardown: removing containers and network"
    podman rm -f "${INGESTOR_CONT}"  2>/dev/null || true
    podman rm -f "${MOCK_CONTAINER}" 2>/dev/null || true
    podman rm -f "${CH_CONTAINER}"   2>/dev/null || true
    podman network rm "${NET}"       2>/dev/null || true
    echo "==> Teardown done"
}

# On exit (success or failure) always clean up.
# On failure, dump the ingestor logs first to aid debugging.
on_exit() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        echo ""
        echo "==> E2E FAIL — dumping ClickHouse container logs:"
        podman logs --tail 40 "${CH_CONTAINER}"  2>&1 || true
        echo "==> Ingestor container logs:"
        podman logs --tail 40 "${INGESTOR_CONT}" 2>&1 || true
        echo "==> Mock WS container logs:"
        podman logs --tail 40 "${MOCK_CONTAINER}" 2>&1 || true
    fi
    teardown
    exit $exit_code
}
trap on_exit EXIT

# ── Helper: run a ClickHouse query via the HTTP interface ─────────────────────
# Uses --noproxy '*' so the external proxy env never intercepts localhost traffic.
ch_query() {
    local query="$1"
    # -G: send --data-urlencode as a URL query param (?query=...). Without -G,
    # curl POSTs the body, and ClickHouse treats the whole POST body as the SQL
    # statement, so "query=SELECT 1" becomes invalid SQL → HTTP 400.
    curl --noproxy '*' -sf -G \
        "http://127.0.0.1:${CH_HTTP_PORT}/" \
        --data-urlencode "query=${query}"
}

# ── Step 0: Pre-flight cleanup (idempotent re-run) ────────────────────────────
echo "==> Pre-flight cleanup of any leftover containers/network from a previous run"
podman rm -f "${INGESTOR_CONT}"  2>/dev/null || true
podman rm -f "${MOCK_CONTAINER}" 2>/dev/null || true
podman rm -f "${CH_CONTAINER}"   2>/dev/null || true
podman network rm "${NET}"       2>/dev/null || true

# ── Step 1: Build ingestor image if missing ───────────────────────────────────
if [[ "${BUILD_IF_MISSING}" == "1" ]] && ! podman image exists "${INGESTOR_IMAGE}"; then
    echo "==> Ingestor image '${INGESTOR_IMAGE}' not found — building..."
    # --http-proxy=false: prevent the build from routing registry traffic through
    # the host proxy (large Alpine apk layer downloads hang through it).
    podman build \
        --http-proxy=false \
        --build-arg BUILD_FROM=ghcr.io/hassio-addons/base/amd64:17.2.1 \
        -t "${INGESTOR_IMAGE}" \
        "${REPO_ROOT}/ingestor"
    echo "==> Ingestor image built: ${INGESTOR_IMAGE}"
else
    echo "==> Using existing ingestor image: ${INGESTOR_IMAGE}"
fi

# ── Step 2: Create a dedicated podman bridge network ─────────────────────────
echo "==> Creating podman network: ${NET}"
podman network create "${NET}"

# ── Step 3: Start ClickHouse ──────────────────────────────────────────────────
echo "==> Starting ClickHouse container: ${CH_CONTAINER}"
podman run -d \
    --name "${CH_CONTAINER}" \
    --network "${NET}" \
    --http-proxy=false \
    -p "127.0.0.1:${CH_HTTP_PORT}:8123" \
    --ulimit nofile=262144:262144 \
    -e CLICKHOUSE_SKIP_USER_SETUP=1 \
    "${CH_IMAGE}"

# Wait until ClickHouse responds to SELECT 1
echo -n "==> Waiting for ClickHouse to be ready"
ch_ready=0
for i in $(seq 1 40); do
    # --noproxy '*': the proxy cannot reach localhost, bypass it
    # -G: query goes in the URL (?query=...), not the POST body (see ch_query)
    if curl --noproxy '*' -sf -G "http://127.0.0.1:${CH_HTTP_PORT}/" \
            --data-urlencode "query=SELECT 1" 2>/dev/null | grep -q '^1$'; then
        ch_ready=1
        echo " OK (${i}s)"
        break
    fi
    echo -n "."
    sleep 2
done
if [[ $ch_ready -ne 1 ]]; then
    echo " TIMEOUT"
    echo "E2E FAIL: ClickHouse did not become ready within ~80s"
    exit 1
fi

# ── Step 4: Start the mock HA WebSocket server ────────────────────────────────
# We run it inside the ingestor image (which has Python + websockets) by mounting
# the host file into the container.  This keeps the mock in sync with the repo.
#
# --http-proxy=false: the mock talks only to other containers on the podman net,
#   not to any external host, so no proxy is needed and injecting it would break
#   things.
echo "==> Starting mock HA WS container: ${MOCK_CONTAINER}"
podman run -d \
    --name "${MOCK_CONTAINER}" \
    --network "${NET}" \
    --http-proxy=false \
    -v "${REPO_ROOT}/test/mock_ha_ws.py:/mock.py:ro,z" \
    --entrypoint python3 \
    "${INGESTOR_IMAGE}" \
    /mock.py --host 0.0.0.0 --port "${MOCK_PORT}" --events 10 --interval 0.2

# Give the mock a moment to bind
sleep 1
echo "==> Mock HA WS started (port ${MOCK_PORT} on network ${NET})"

# ── Step 5: Start the ingestor ────────────────────────────────────────────────
# --http-proxy=false: the ingestor's HTTP INSERT goes to the ClickHouse container
#   on the podman bridge network, not the internet. If the host proxy env were
#   injected here, every INSERT would hit the external proxy and get 502.
echo "==> Starting ingestor container: ${INGESTOR_CONT}"
podman run -d \
    --name "${INGESTOR_CONT}" \
    --network "${NET}" \
    --http-proxy=false \
    --entrypoint python3 \
    -e "HA_WS_URL=ws://${MOCK_CONTAINER}:${MOCK_PORT}" \
    -e "SUPERVISOR_TOKEN=test-token" \
    -e "CLICKHOUSE_HOST=${CH_CONTAINER}" \
    -e "CLICKHOUSE_PORT=8123" \
    -e "CLICKHOUSE_DATABASE=homeassistant" \
    -e "CLICKHOUSE_TABLE=states" \
    -e "BATCH_SIZE=5" \
    -e "FLUSH_INTERVAL=2" \
    -e "INCLUDE_ATTRIBUTES=true" \
    -e "EXCLUDE_ENTITIES=sensor.excluded_demo" \
    "${INGESTOR_IMAGE}" \
    /app/ingestor.py

echo "==> Ingestor started"

# ── Step 6: Poll until rows appear (or timeout) ───────────────────────────────
echo -n "==> Waiting for rows in ClickHouse"
rows_ready=0
for i in $(seq 1 "${POLL_TIMEOUT}"); do
    count=$(ch_query "SELECT count() FROM homeassistant.states" 2>/dev/null || echo "0")
    # Trim whitespace
    count="${count//[[:space:]]/}"
    if [[ "${count}" =~ ^[0-9]+$ ]] && [[ "${count}" -gt 0 ]]; then
        rows_ready=1
        echo " OK (${i}s, rows=${count})"
        break
    fi
    echo -n "."
    sleep 1
done
if [[ $rows_ready -ne 1 ]]; then
    echo " TIMEOUT"
    echo "E2E FAIL: No rows inserted within ${POLL_TIMEOUT}s"
    exit 1
fi

# ── Step 7: Assertions ────────────────────────────────────────────────────────
fail_count=0

assert_eq() {
    local label="$1"
    local expected="$2"
    local actual="$3"
    if [[ "${actual}" == "${expected}" ]]; then
        echo "  PASS  ${label} (got: ${actual})"
    else
        echo "  FAIL  ${label} — expected '${expected}', got '${actual}'"
        (( fail_count++ )) || true
    fi
}

assert_gt() {
    local label="$1"
    local threshold="$2"
    local actual="$3"
    actual="${actual//[[:space:]]/}"
    if [[ "${actual}" =~ ^[0-9]+$ ]] && [[ "${actual}" -gt "${threshold}" ]]; then
        echo "  PASS  ${label} (got: ${actual} > ${threshold})"
    else
        echo "  FAIL  ${label} — expected > ${threshold}, got '${actual}'"
        (( fail_count++ )) || true
    fi
}

assert_zero() {
    local label="$1"
    local actual="$2"
    actual="${actual//[[:space:]]/}"
    if [[ "${actual}" == "0" ]]; then
        echo "  PASS  ${label} (got: 0)"
    else
        echo "  FAIL  ${label} — expected 0, got '${actual}'"
        (( fail_count++ )) || true
    fi
}

echo ""
echo "==> Running assertions"

# A1: Total row count > 0
total=$(ch_query "SELECT count() FROM homeassistant.states" | tr -d '[:space:]')
assert_gt "total rows > 0" 0 "${total}"

# A2: Numeric sensor has rows with non-NULL state_float
numeric_float_rows=$(ch_query "SELECT count() FROM homeassistant.states WHERE entity_id='sensor.numeric_demo' AND state_float IS NOT NULL" | tr -d '[:space:]')
assert_gt "numeric sensor: rows with non-NULL state_float" 0 "${numeric_float_rows}"

# A3: Text sensor has rows and its state_float is NULL (never a number)
text_null_rows=$(ch_query "SELECT count() FROM homeassistant.states WHERE entity_id='sensor.text_demo' AND state_float IS NULL" | tr -d '[:space:]')
assert_gt "text sensor: rows with NULL state_float" 0 "${text_null_rows}"

text_nonnull_rows=$(ch_query "SELECT count() FROM homeassistant.states WHERE entity_id='sensor.text_demo' AND state_float IS NOT NULL" | tr -d '[:space:]')
assert_zero "text sensor: zero rows with non-NULL state_float" "${text_nonnull_rows}"

# A4: Excluded entity has 0 rows
excluded_rows=$(ch_query "SELECT count() FROM homeassistant.states WHERE entity_id='sensor.excluded_demo'" | tr -d '[:space:]')
assert_zero "excluded entity: 0 rows" "${excluded_rows}"

# A5: At least one row with non-empty attributes JSON
attrs_rows=$(ch_query "SELECT count() FROM homeassistant.states WHERE length(attributes) > 2" | tr -d '[:space:]')
assert_gt "attributes column: at least one row with JSON content" 0 "${attrs_rows}"

# ── Step 8: Report ────────────────────────────────────────────────────────────
echo ""
if [[ $fail_count -eq 0 ]]; then
    echo "E2E PASS — all ${total} rows verified, all assertions passed"
    exit 0
else
    echo "E2E FAIL: ${fail_count} assertion(s) failed (see above)"
    exit 1
fi
