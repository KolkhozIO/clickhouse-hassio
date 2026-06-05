#!/usr/bin/env python3
"""Mock Home Assistant WebSocket API server for e2e testing.

Mimics the HA WebSocket protocol so that the ingestor can be tested without a
live Home Assistant instance or a real supervisor token.

Protocol replay
---------------
1. On connect  → sends {"type":"auth_required","ha_version":"mock"}
2. Receives    {"type":"auth","access_token":<anything>}
               → replies {"type":"auth_ok","ha_version":"mock"}
3. Receives    {"id":N,"type":"subscribe_events","event_type":"state_changed"}
               → replies {"id":N,"type":"result","success":true,"result":null}
               → remembers N as the subscription id
4. Emits a deterministic set of state_changed events (see EVENT CATALOGUE below)
   followed by --events numeric churn events on sensor.numeric_demo.
5. Keeps the connection open so the ingestor stays connected.

Event catalogue
---------------
- sensor.numeric_demo    : numeric sensor, state_float exercised (multiple values)
- sensor.text_demo       : text sensor, state_float must be NULL
- sensor.excluded_demo   : entity that matches EXCLUDE_ENTITIES in e2e; 0 rows expected
- null-new_state event   : new_state is null; ingestor must skip it silently

Usage
-----
    python3 mock_ha_ws.py [--host 0.0.0.0] [--port 8765] \
                          [--events 10] [--interval 0.2]

Environment overrides (lower priority than CLI args)
-----
    MOCK_HOST, MOCK_PORT, MOCK_EVENTS, MOCK_INTERVAL
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import websockets
import websockets.server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [mock-ha] %(message)s",
)
log = logging.getLogger("mock-ha")


def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string with microseconds."""
    return datetime.now(timezone.utc).isoformat()


def _make_event(sub_id: int, entity_id: str, state: str, attributes: dict) -> str:
    """Build a JSON-serialised state_changed event matching the real HA wire format."""
    ts = _now_iso()
    return json.dumps({
        "id": sub_id,
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "time_fired": ts,
            "data": {
                "entity_id": entity_id,
                "new_state": {
                    "entity_id": entity_id,
                    "state": state,
                    "attributes": attributes,
                    "last_changed": ts,
                    "last_updated": ts,
                },
                # old_state present but not consumed by ingestor
                "old_state": None,
            },
        },
    })


def _make_null_new_state_event(sub_id: int) -> str:
    """Build a state_changed event where new_state is null — ingestor must skip it."""
    ts = _now_iso()
    return json.dumps({
        "id": sub_id,
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "time_fired": ts,
            "data": {
                "entity_id": "sensor.vanishing_sensor",
                "new_state": None,
                "old_state": {
                    "entity_id": "sensor.vanishing_sensor",
                    "state": "42",
                    "attributes": {},
                    "last_changed": ts,
                    "last_updated": ts,
                },
            },
        },
    })


async def _handle(websocket: websockets.server.WebSocketServerProtocol,
                  num_events: int, interval: float) -> None:
    """Handle one ingestor connection through the full mock protocol."""
    remote = websocket.remote_address
    log.info("Client connected: %s", remote)

    # Step 1: challenge
    await websocket.send(json.dumps({"type": "auth_required", "ha_version": "mock"}))

    # Step 2: authentication
    raw = await websocket.recv()
    msg = json.loads(raw)
    if msg.get("type") != "auth":
        log.warning("Expected auth message, got: %s", msg.get("type"))
        return
    log.info("Client authenticated (token accepted blindly): %s", remote)
    await websocket.send(json.dumps({"type": "auth_ok", "ha_version": "mock"}))

    # Step 3: wait for subscription
    sub_id: int | None = None
    while sub_id is None:
        raw = await websocket.recv()
        msg = json.loads(raw)
        if (msg.get("type") == "subscribe_events"
                and msg.get("event_type") == "state_changed"):
            sub_id = int(msg["id"])
            await websocket.send(json.dumps({
                "id": sub_id,
                "type": "result",
                "success": True,
                "result": None,
            }))
            log.info("Subscription accepted, id=%d", sub_id)

    # Step 4: emit the fixed catalogue events first
    catalogue = [
        # (entity_id, state, attributes)
        # Numeric sensor — first value; will produce a non-NULL state_float
        ("sensor.numeric_demo", "21.5",
         {"unit_of_measurement": "°C", "friendly_name": "Numeric Demo"}),
        # Text sensor — state_float must be NULL in ClickHouse
        ("sensor.text_demo", "on",
         {"device_class": "plug", "friendly_name": "Text Demo"}),
        # Excluded entity — the e2e harness sets EXCLUDE_ENTITIES=sensor.excluded_demo
        ("sensor.excluded_demo", "99.9",
         {"friendly_name": "Should Be Excluded"}),
    ]
    for entity_id, state, attrs in catalogue:
        await websocket.send(_make_event(sub_id, entity_id, state, attrs))
        await asyncio.sleep(0.05)

    # Null new_state event — ingestor must silently skip it, no row expected
    await websocket.send(_make_null_new_state_event(sub_id))
    await asyncio.sleep(0.05)
    log.info("Emitted catalogue events (numeric, text, excluded, null-new_state)")

    # Step 4b: churn events for the numeric sensor so batch-size > 1 is exercised
    for i in range(num_events):
        value = str(20.0 + i * 0.5)
        await websocket.send(
            _make_event(sub_id, "sensor.numeric_demo", value,
                        {"unit_of_measurement": "°C"})
        )
        await asyncio.sleep(interval)

    log.info("Emitted %d numeric churn events; keeping connection open", num_events)

    # Keep alive — real HA stays connected indefinitely
    try:
        async for _ in websocket:
            pass  # discard any pings / extra messages from the ingestor
    except websockets.exceptions.ConnectionClosed:
        log.info("Client disconnected: %s", remote)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mock HA WebSocket server")
    p.add_argument("--host", default=os.environ.get("MOCK_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("MOCK_PORT", "8765")))
    p.add_argument("--events", type=int,
                   default=int(os.environ.get("MOCK_EVENTS", "10")),
                   help="Number of numeric churn events after the catalogue")
    p.add_argument("--interval", type=float,
                   default=float(os.environ.get("MOCK_INTERVAL", "0.2")),
                   help="Seconds between churn events")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    log.info("Starting mock HA WS on %s:%d  (events=%d, interval=%.2fs)",
             args.host, args.port, args.events, args.interval)

    async def handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        await _handle(ws, args.events, args.interval)

    async with websockets.serve(handler, args.host, args.port):
        log.info("Mock HA WS ready — waiting for connections")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(_main())
