# Ingestor Test Suite

This directory contains the automated tests for the ClickHouse Ingestor add-on.

---

## Unit tests

Unit tests live in `test/test_ingestor_unit.py` (authored separately).  They
exercise the pure-Python helpers (`_parse_dt`, `_to_float`, `_row_from_new_state`,
`Batcher`) without any containers.

### Run locally

```bash
# 1. Install test dependencies once
make test-deps        # pip install -r test/requirements-test.txt

# 2. Run the tests
make test             # python3 -m pytest test/ -v
```

---

## End-to-end (e2e) tests

The e2e harness (`scripts/e2e.sh`) spins up:

| Container | Image | Role |
|-----------|-------|------|
| `ch_e2e_ch` | `clickhouse/clickhouse-server:24.8` | Real ClickHouse |
| `ch_e2e_mock` | `localhost/ch-ingestor:local` | Mock HA WS (Python, file-mounted) |
| `ch_e2e_ingestor` | `localhost/ch-ingestor:local` | The ingestor under test |

All three share a dedicated podman bridge network (`ch_e2e_net`).

### Run locally

```bash
# Optional: build the ingestor image explicitly first
make build

# Run the full e2e suite (builds image if missing, starts containers, asserts)
make e2e
```

The harness is **idempotent**: running `make e2e` twice in a row is safe — it
removes any leftover containers before starting.

### Env knobs for e2e

| Variable | Default | Description |
|----------|---------|-------------|
| `CH_IMAGE` | `docker.io/clickhouse/clickhouse-server:24.8` | ClickHouse image |
| `INGESTOR_IMAGE` | `localhost/ch-ingestor:local` | Ingestor image |
| `NET` | `ch_e2e_net` | Podman network name |
| `CH_HTTP_PORT` | `18123` | Host port forwarded to CH 8123 |
| `MOCK_PORT` | `8765` | Mock WS port (inside the network) |
| `POLL_TIMEOUT` | `60` | Seconds to wait for rows to appear |
| `BUILD_IF_MISSING` | `1` | Auto-build ingestor image if absent |

---

## Mock HA WebSocket server (`test/mock_ha_ws.py`)

A standalone asyncio server that faithfully mimics the Home Assistant WebSocket
authentication + subscription protocol.  The ingestor needs no live HA instance
and no real supervisor token to run against it.

### Protocol replay

1. On connect → `{"type":"auth_required","ha_version":"mock"}`
2. Client sends `{"type":"auth",...}` → server replies `{"type":"auth_ok",...}` (any token accepted)
3. Client sends subscribe → server replies result `success:true`, remembers subscription id
4. Server emits a fixed catalogue of events, then `--events` numeric churn events

### Deterministic event catalogue

| Entity | State | Purpose |
|--------|-------|---------|
| `sensor.numeric_demo` | `21.5` (then churn) | `state_float` must be non-NULL |
| `sensor.text_demo` | `"on"` | `state_float` must be NULL |
| `sensor.excluded_demo` | `99.9` | Must produce 0 rows (excluded) |
| `sensor.vanishing_sensor` | `new_state: null` | Must be silently skipped |

### CLI / env knobs

```
python3 test/mock_ha_ws.py \
    --host 0.0.0.0 \
    --port 8765 \
    --events 10 \     # numeric churn events after catalogue
    --interval 0.2    # seconds between churn events
```

Environment equivalents: `MOCK_HOST`, `MOCK_PORT`, `MOCK_EVENTS`, `MOCK_INTERVAL`.

---

## Proxy / `--http-proxy=false` gotcha

The host shell on the development machine exports `http_proxy` / `https_proxy`
pointing at an external proxy (`168.81.67.134:8000`).  That proxy **cannot reach
`localhost` or the podman bridge network** (`172.x.x.x`).

Two consequences that the scripts work around:

1. **`podman run --http-proxy=false`** — without this flag podman injects the host
   `http_proxy` env into every container.  The ingestor's HTTP `INSERT` goes to
   the ClickHouse container on the bridge network, which the proxy cannot reach →
   `502 Bad Gateway`.  `--http-proxy=false` prevents injection.

2. **`curl --noproxy '*'`** — used for all health-check and assertion `curl` calls
   that target `127.0.0.1:CH_HTTP_PORT`.  Without it the request is sent to the
   external proxy, which cannot forward it to localhost → timeout / 502.

In CI (GitHub Actions, `ubuntu-latest`) there is no such proxy, so the workflow
uses plain `docker run` and `curl` without these flags.
