# Architecture

Home Assistant history analytics on ClickHouse, packaged as Home Assistant
add-ons. This document is the design of record: components, data flow, the
deployment/configuration model, the diagnostics & test design, the environment
constraints we must engineer around, and the decisions behind them.

---

## 1. Goals & non-goals

**Goals**
- Capture Home Assistant state history into ClickHouse for fast analytical
  queries and dashboards (Redash/Grafana).
- Be installable and configurable through the standard HA Add-on Store —
  reproducible, no manual host surgery.
- Make every operational action (deploy, configure, diagnose, test)
  **scripted and reproducible**, not ad-hoc.

**Non-goals**
- Replacing HA's own recorder (we observe it, we don't manage it).
- Long-term-statistics aggregation inside HA (we keep raw state in ClickHouse
  and aggregate there).

---

## 2. System overview

```
                          ┌──────────────────────────────────────────────┐
                          │              Home Assistant OS                 │
                          │                                                │
   devices ──▶ HA Core ──▶│  state machine ──▶ WebSocket API (state_changed)│
                          │        │                    ▲                   │
                          │        │ recorder           │ ws://supervisor   │
                          │        ▼                    │ /core/websocket   │
                          │   SQLite/PG (HA's own)      │ (SUPERVISOR_TOKEN)│
                          │                             │                   │
                          │                   ┌─────────┴──────────┐        │
                          │                   │ ClickHouse Ingestor│  ◀── add-on (this repo)
                          │                   │   add-on (python)  │        │
                          │                   └─────────┬──────────┘        │
                          └─────────────────────────────┼───────────────────┘
                                                        │ HTTP INSERT (JSONEachRow)
                                                        ▼
                                          ┌───────────────────────────┐
                                          │   ClickHouse add-on        │
                                          │   homeassistant.states     │
                                          │   (MergeTree)              │
                                          └─────────────┬──────────────┘
                                                        │ SQL (HTTP / native)
                                                        ▼
                                          ┌───────────────────────────┐
                                          │   Redash add-on            │
                                          │   dashboards / queries     │
                                          └───────────────────────────┘
```

**Canonical path (v1.1+):** `HA Core → Ingestor → ClickHouse → Redash`.

**Deprecated path:** `HA recorder → PostgreSQL (pg_ivm) → ClickHouse
(MaterializedPostgreSQL) → Redash`. Kept for existing installs; see
[§9 Decisions](#9-decisions-adr-style).

---

## 3. Components

| Component | Repo dir | Runtime | Responsibility |
| --- | --- | --- | --- |
| **ClickHouse Ingestor** | `ingestor/` | Python 3 + `websockets` | Subscribe to HA `state_changed`, transform, batch-insert into ClickHouse. **The active path.** |
| **ClickHouse** | `clickhouse/` | ClickHouse server | Columnar store + HTTP/native query interface. |
| **Redash** | `redash/` | docker-compose (Redash 10) | Dashboards over ClickHouse. |
| **PostgreSQL** | `postgresql/` | PG 17 + `pg_ivm` | _Deprecated_ relay for the legacy `MaterializedPostgreSQL` path. |

Each is a Supervisor add-on: `config.json` (metadata + options schema),
`Dockerfile`, `build.yaml` (per-arch base image), optional `rootfs/` (s6
services), `DOCS.md`, `CHANGELOG.md`, `translations/`.

---

## 4. Ingestor design (the core component)

A single long-running async process (`ingestor/ingestor.py`) supervised by
s6 (`rootfs/etc/services.d/clickhouse_ingestor/run`).

**Lifecycle**
1. **Config** — `run` reads add-on options via `bashio::config` and exports
   them as env (`CLICKHOUSE_*`, `BATCH_SIZE`, `FLUSH_INTERVAL`,
   `INCLUDE_ATTRIBUTES`, `EXCLUDE_ENTITIES`). `HA_WS_URL` defaults to the
   Supervisor-proxied core socket and is overridable (testing / remote HA).
2. **Schema bootstrap** — `CREATE DATABASE/TABLE IF NOT EXISTS` over HTTP,
   retried until ClickHouse is reachable (decouples start order).
3. **Connect & auth** — WebSocket to `ws://supervisor/core/websocket`; handle
   `auth_required` → send `{type:auth, access_token: $SUPERVISOR_TOKEN}` →
   expect `auth_ok`.
4. **Subscribe** — `subscribe_events` for `state_changed`.
5. **Ingest loop** — for each event, take `new_state` (skip if null →
   entity removed), map to a row, push to the batcher.
6. **Reconnect** — any loop error flushes the buffer, then reconnects with
   exponential backoff (1→60 s). The process never exits on transient errors;
   s6 restarts it on a hard crash.

**Batching & backpressure** — `Batcher` accumulates rows; flushes on
`BATCH_SIZE` or every `FLUSH_INTERVAL` seconds (whichever first). On insert
failure rows are **re-queued** (at-least-once), so a brief ClickHouse outage
doesn't drop data.

**Transform** (pure, unit-tested functions):
- `_parse_dt` — HA ISO-8601 → `YYYY-MM-DD HH:MM:SS.fff` UTC (ms precision).
- `_to_float` — numeric state → `Float64`, else `NULL` (cheap numeric queries).
- `_row_from_new_state` — builds the row; honours `EXCLUDE_ENTITIES` and
  `INCLUDE_ATTRIBUTES`.

**Delivery semantics** — at-least-once. Duplicates are possible across
reconnects; the data model tolerates this (see §5) and dedup is a query-time
or `ReplacingMergeTree` concern (future work).

---

## 5. Data model

```sql
CREATE TABLE homeassistant.states
(
    entity_id    LowCardinality(String),   -- few thousand distinct → dictionary-encoded
    state        String,                   -- raw state, always present
    state_float  Nullable(Float64),        -- parsed numeric, NULL for text states
    attributes   String,                   -- JSON blob (optional, INCLUDE_ATTRIBUTES)
    last_changed DateTime64(3, 'UTC'),
    last_updated DateTime64(3, 'UTC'),
    ingested_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(last_updated)        -- monthly parts → cheap retention drops
ORDER BY (entity_id, last_updated);        -- primary use: one entity over time
```

Rationale: `ORDER BY (entity_id, last_updated)` matches the dominant query
(history of one entity); monthly partitions make retention a partition drop;
`state_float` avoids per-query parsing; `attributes` as raw JSON keeps ingest
schema-free (query with ClickHouse JSON functions). Retention via TTL is future
work (e.g. `TTL last_updated + INTERVAL 1 YEAR`).

---

## 6. Deployment & configuration model

- **Distribution** — push to the repo's default branch; users add the repo URL
  in the HA Add-on Store, or drop the add-on into `addons/local/` for a local
  build. Supervisor builds the image per `build.yaml`.
- **Multi-arch** — `build.yaml` maps each arch to its `hassio-addons/base`
  image; CI (`.github/workflows/builder.yaml`) builds all arches via
  `home-assistant/builder`. Validated locally with `podman --arch arm64` under
  qemu binfmt.
- **Inter-add-on networking** — the ingestor reaches ClickHouse either via the
  ClickHouse add-on's host-published port (`<HA-IP>:8124`) or, on the Supervisor
  network, by add-on hostname. `clickhouse_host`/`clickhouse_port` are explicit
  options (no fragile autodiscovery).
- **Auth to HA** — the add-on declares `homeassistant_api: true`; Supervisor
  injects `SUPERVISOR_TOKEN`, which authenticates the WebSocket via the
  Supervisor proxy. **No user-managed long-lived token is needed** in the
  supervised path.

---

## 7. Environment constraints (engineer around these — do not "fix" by hand)

These are real properties of the target host and images; the tooling must
account for them so operations are reproducible rather than ad-hoc.

1. **Host proxy env.** The host shell exports `http_proxy`/`https_proxy` to an
   external proxy that **cannot reach localhost/LAN**. A known-up local service
   then returns `502`/timeout when probed naively.
   - Probes: `curl --noproxy '*' …`.
   - Containers: `podman run --http-proxy=false …` (otherwise the proxy env is
     injected and the ingestor's insert to ClickHouse fails with `502`).
2. **ClickHouse default-user lockdown.** The official `clickhouse-server` image
   **disables network access for `default`** when neither `CLICKHOUSE_USER` nor
   `CLICKHOUSE_PASSWORD` is set → HTTP probes get `AUTHENTICATION_FAILED`. Set
   `CLICKHOUSE_SKIP_USER_SETUP=1` (test/dev) or real credentials (prod).
3. **HA in a TCG VM.** The local HA OS runs under qemu **without KVM** — slow.
   All HA-side automation must use generous timeouts and stream output.
4. **docker.io blob pulls hang through the proxy.** Large layer pulls stall via
   the proxy; pull with the proxy bypassed (`env -u http_proxy …`) or reuse
   locally cached images. ghcr.io works.

---

## 8. Diagnostics & test architecture

The guiding principle: **diagnosis is a designed capability, not manual
poking.** Failures must be self-explaining and reproduction must be one command.

**Unit tests** (`test/test_ingestor_unit.py`, pytest) — pure transform
functions, hermetic, no network. Run: `make test`.

**Hermetic e2e** (`scripts/e2e.sh`, `test/mock_ha_ws.py`) — the full path with
**no live HA and no token**:
- `mock_ha_ws.py` faithfully replays the HA WS protocol (auth → subscribe →
  emit a deterministic catalogue: numeric, text, excluded, null-`new_state`).
- `e2e.sh` (podman) builds the ingestor image, starts ClickHouse (with
  `CLICKHOUSE_SKIP_USER_SETUP=1`, §7.2), runs the mock and the ingestor
  (`--http-proxy=false`, §7.1), then **asserts** on ClickHouse: rows present,
  `state_float` parsed for numeric, `NULL` for text, excluded entity absent,
  attributes JSON present.
- **Self-diagnosing:** on any failure the harness dumps the logs of *all*
  containers (ClickHouse included) before teardown; the `trap` always cleans up
  so re-runs are idempotent. Run: `make e2e`.

**CI** (`.github/workflows/test.yaml`) — `unit` job (always) + `e2e` job
(docker, no proxy). Add-on image builds run in `builder.yaml`; add-on configs
are linted in `lint.yaml`.

**HA-side diagnostics** — operations against the live HA OS go through the
Supervisor API / `ha` CLI with documented, scripted steps (a runbook), never
hand-typed one-offs. Known facts captured for reuse: there is no `options`
subcommand in the current `ha` CLI (set add-on options via
`POST /addons/<slug>/options`); local add-ons are picked up by `ha store
reload`; the host shell lacks `curl`/`wget` (use a container or `ha`).

---

## 9. Decisions (ADR-style)

- **Direct ingestor over `MaterializedPostgreSQL`.** The PG relay depended on an
  *experimental* ClickHouse engine, `pg_ivm`, logical replication, and a whole
  extra PostgreSQL just to forward data. The ingestor is a small, observable,
  reconnecting process with no relay. → PG path **deprecated**, not deleted.
- **HTTP `JSONEachRow` inserts.** No native-protocol client dependency; trivial
  to batch; works through the add-on network. Cost: slightly larger payloads —
  acceptable at HA event volumes.
- **At-least-once + re-queue on failure.** Prefer duplicates over data loss for
  history. Exactly-once would need dedup keys; deferred.
- **`state_float` alongside raw `state`.** One cheap numeric column covers the
  vast majority of analytical queries without parsing text at query time.
- **Explicit `clickhouse_host` option.** Inter-add-on discovery in HA is
  environment-specific; an explicit option is predictable and documented.

---

## 10. Future work

- `ReplacingMergeTree`/dedup keys to collapse reconnect duplicates.
- TTL-based retention + optional rollup materialized views.
- Optional one-shot **backfill** of existing recorder history on first run.
- A packaged ClickHouse data source / starter dashboards for Redash/Grafana.
- A `scripts/diagnose.sh` runbook that checks each hop (proxy, CH auth, WS auth,
  ingest lag) and prints a health summary.
