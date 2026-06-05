# Home Assistant Add-on: ClickHouse Ingestor

Stream Home Assistant state changes **directly** into ClickHouse, without the
PostgreSQL + `MaterializedPostgreSQL` relay.

The add-on connects to the Home Assistant WebSocket API (proxied through the
Supervisor), subscribes to `state_changed` events and batch-inserts them into a
ClickHouse table over the HTTP interface.

## How it works

```
Home Assistant Core ──(WebSocket: state_changed)──> Ingestor ──(HTTP INSERT)──> ClickHouse
```

On first start the add-on creates the target database and table automatically:

```sql
CREATE TABLE homeassistant.states
(
    entity_id    LowCardinality(String),
    state        String,
    state_float  Nullable(Float64),
    attributes   String,                       -- JSON
    last_changed DateTime64(3, 'UTC'),
    last_updated DateTime64(3, 'UTC'),
    ingested_at  DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(last_updated)
ORDER BY (entity_id, last_updated);
```

`state_float` is populated when the state parses as a number, which makes
numeric queries cheap.

## Configuration

| Option | Default | Description |
| --- | --- | --- |
| `clickhouse_host` | _(required)_ | Hostname/IP of the ClickHouse HTTP interface. See note below. |
| `clickhouse_port` | `8123` | ClickHouse HTTP port. |
| `clickhouse_user` | `default` | ClickHouse user. |
| `clickhouse_password` | _(empty)_ | ClickHouse password. |
| `clickhouse_database` | `homeassistant` | Target database (auto-created). |
| `clickhouse_table` | `states` | Target table (auto-created). |
| `batch_size` | `500` | Flush after this many buffered rows. |
| `flush_interval` | `5` | Flush at least every N seconds. |
| `include_attributes` | `true` | Store the entity attributes as a JSON string. |
| `exclude_entities` | `[]` | List of `entity_id`s to skip. |

### Setting `clickhouse_host`

The simplest reliable value is your Home Assistant host IP plus the port the
ClickHouse add-on publishes (`8124` by default, mapped to ClickHouse's internal
`8123`):

```yaml
clickhouse_host: 192.168.1.10   # your HA host IP
clickhouse_port: 8124
```

## Querying

```sql
SELECT last_updated, state_float
FROM homeassistant.states
WHERE entity_id = 'sensor.living_room_temperature'
ORDER BY last_updated DESC
LIMIT 100;
```

Point Redash (or Grafana) at the ClickHouse HTTP endpoint and build dashboards
directly on this table.
