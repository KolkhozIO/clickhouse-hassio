#!/usr/bin/env python3
"""Stream Home Assistant state changes into ClickHouse.

Connects to the Supervisor-proxied Home Assistant WebSocket API, subscribes to
``state_changed`` events and batch-inserts them into a ClickHouse table over the
HTTP interface. No PostgreSQL relay is involved.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ch-ingestor")


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value if value is not None else default


# --- Configuration (provided by the s6 run script via bashio::config) --------
SUPERVISOR_TOKEN = _env("SUPERVISOR_TOKEN")
# Defaults to the Supervisor-proxied Core API; overridable for testing or for
# pointing at a Home Assistant instance over the network.
WS_URL = _env("HA_WS_URL", "ws://supervisor/core/websocket")

CH_HOST = _env("CLICKHOUSE_HOST")
CH_PORT = int(_env("CLICKHOUSE_PORT", "8123"))
CH_USER = _env("CLICKHOUSE_USER", "default")
CH_PASSWORD = _env("CLICKHOUSE_PASSWORD")
CH_DATABASE = _env("CLICKHOUSE_DATABASE", "homeassistant")
CH_TABLE = _env("CLICKHOUSE_TABLE", "states")

BATCH_SIZE = max(1, int(_env("BATCH_SIZE", "500")))
FLUSH_INTERVAL = max(1, int(_env("FLUSH_INTERVAL", "5")))
INCLUDE_ATTRIBUTES = _env("INCLUDE_ATTRIBUTES", "true").lower() == "true"
EXCLUDE_ENTITIES = {
    e.strip() for e in _env("EXCLUDE_ENTITIES").split(",") if e.strip()
}

CH_BASE_URL = f"http://{CH_HOST}:{CH_PORT}/"


# --- ClickHouse HTTP helpers --------------------------------------------------
def _ch_request(query: str, body: bytes | None = None) -> str:
    """Run a query against the ClickHouse HTTP interface (blocking)."""
    url = CH_BASE_URL + "?" + urllib.parse.urlencode({"query": query})
    headers = {
        "X-ClickHouse-User": CH_USER,
        "X-ClickHouse-Key": CH_PASSWORD,
        "Content-Type": "text/plain; charset=utf-8",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _ensure_schema() -> None:
    """Create the target database and table if they do not exist."""
    _ch_request(f"CREATE DATABASE IF NOT EXISTS `{CH_DATABASE}`")
    ddl = f"""
        CREATE TABLE IF NOT EXISTS `{CH_DATABASE}`.`{CH_TABLE}`
        (
            entity_id    LowCardinality(String),
            state        String,
            state_float  Nullable(Float64),
            attributes   String,
            last_changed DateTime64(3, 'UTC'),
            last_updated DateTime64(3, 'UTC'),
            ingested_at  DateTime64(3, 'UTC') DEFAULT now64(3)
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(last_updated)
        ORDER BY (entity_id, last_updated)
    """
    _ch_request(ddl)
    log.info("ClickHouse schema ready: %s.%s", CH_DATABASE, CH_TABLE)


def _insert_rows(rows: list[dict]) -> None:
    """Insert a batch of rows using JSONEachRow (blocking)."""
    query = f"INSERT INTO `{CH_DATABASE}`.`{CH_TABLE}` FORMAT JSONEachRow"
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode("utf-8")
    _ch_request(query, body)


# --- Event parsing ------------------------------------------------------------
def _parse_dt(value: str | None) -> str | None:
    """HA ISO-8601 timestamp -> 'YYYY-MM-DD HH:MM:SS.fff' in UTC for ClickHouse."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _to_float(state: str) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def _row_from_new_state(new_state: dict) -> dict | None:
    entity_id = new_state.get("entity_id")
    if not entity_id or entity_id in EXCLUDE_ENTITIES:
        return None

    last_updated = _parse_dt(new_state.get("last_updated"))
    last_changed = _parse_dt(new_state.get("last_changed")) or last_updated
    if last_updated is None:
        return None

    state = new_state.get("state")
    state = "" if state is None else str(state)

    attributes = ""
    if INCLUDE_ATTRIBUTES:
        attributes = json.dumps(
            new_state.get("attributes", {}), ensure_ascii=False
        )

    return {
        "entity_id": entity_id,
        "state": state,
        "state_float": _to_float(state),
        "attributes": attributes,
        "last_changed": last_changed,
        "last_updated": last_updated,
    }


# --- Batching buffer ----------------------------------------------------------
class Batcher:
    def __init__(self) -> None:
        self._buffer: list[dict] = []
        self._lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()

    async def add(self, row: dict) -> None:
        async with self._lock:
            self._buffer.append(row)
            if len(self._buffer) >= BATCH_SIZE:
                await self._flush_locked()

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        try:
            await self._loop.run_in_executor(None, _insert_rows, rows)
            log.info("Inserted %d rows", len(rows))
        except (urllib.error.URLError, OSError) as err:
            # Re-queue on failure so we retry on the next flush instead of
            # dropping data outright.
            self._buffer[:0] = rows
            log.error("Insert failed (%s); %d rows re-queued", err, len(rows))


async def _periodic_flush(batcher: Batcher) -> None:
    while True:
        await asyncio.sleep(FLUSH_INTERVAL)
        await batcher.flush()


# --- WebSocket loop -----------------------------------------------------------
async def _run_once(batcher: Batcher) -> None:
    async with websockets.connect(WS_URL, max_size=None, ping_interval=30) as ws:
        # Authentication handshake.
        msg = json.loads(await ws.recv())
        if msg.get("type") == "auth_required":
            await ws.send(
                json.dumps({"type": "auth", "access_token": SUPERVISOR_TOKEN})
            )
            msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Authentication failed: {msg}")
        log.info("Authenticated with Home Assistant")

        # Subscribe to state changes.
        await ws.send(
            json.dumps(
                {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
            )
        )
        log.info("Subscribed to state_changed events")

        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") != "event":
                continue
            new_state = (msg.get("event", {}).get("data", {}) or {}).get("new_state")
            if not new_state:
                continue
            row = _row_from_new_state(new_state)
            if row is not None:
                await batcher.add(row)


async def main() -> None:
    if not CH_HOST:
        log.error("CLICKHOUSE_HOST is not set; aborting")
        raise SystemExit(1)
    if not SUPERVISOR_TOKEN:
        log.error("SUPERVISOR_TOKEN is missing; is homeassistant_api enabled?")
        raise SystemExit(1)

    # Wait for ClickHouse and create the schema before consuming events.
    while True:
        try:
            _ensure_schema()
            break
        except (urllib.error.URLError, OSError) as err:
            log.warning("ClickHouse not ready yet (%s); retrying in 5s", err)
            await asyncio.sleep(5)

    batcher = Batcher()
    flusher = asyncio.ensure_future(_periodic_flush(batcher))

    backoff = 1
    try:
        while True:
            try:
                await _run_once(batcher)
            except Exception as err:  # noqa: BLE001 - keep the stream alive
                log.error("WebSocket loop error (%s); reconnecting in %ds", err, backoff)
                await batcher.flush()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            else:
                backoff = 1
    finally:
        flusher.cancel()
        await batcher.flush()


if __name__ == "__main__":
    asyncio.run(main())
