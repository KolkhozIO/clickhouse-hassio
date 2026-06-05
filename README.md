# ClickHouse Home Assistant add-on repository

This repository contains Home Assistant add-ons for storing and analysing
Home Assistant history in [ClickHouse](https://clickhouse.com/).

Add the repo in the HA add-on store:
`https://github.com/apbodrov/clickhouse-hassio.git`

## Add-ons

| Add-on | Purpose |
| --- | --- |
| **ClickHouse** | ClickHouse server. |
| **ClickHouse Ingestor** | Streams HA state changes straight into ClickHouse. **Recommended.** |
| **Redash** | Dashboards on top of ClickHouse. |
| **PostgreSQL** | _Deprecated_ — relay for the legacy `MaterializedPostgreSQL` pipeline. |

## Recommended setup (direct ingestor)

The ingestor connects to the Home Assistant WebSocket API, subscribes to
`state_changed` events and batch-inserts them into ClickHouse over HTTP. No
PostgreSQL relay is involved.

```
Home Assistant Core ──(WebSocket)──> ClickHouse Ingestor ──(HTTP INSERT)──> ClickHouse
```

1. Install the **ClickHouse** and **ClickHouse Ingestor** add-ons from this repo.
2. Start ClickHouse. It exposes the HTTP interface on host port `8124`.
3. Configure the ingestor (Configuration tab) and start it:
   ```yaml
   clickhouse_host: 192.168.1.10   # your HA host IP
   clickhouse_port: 8124
   clickhouse_database: homeassistant
   clickhouse_table: states
   ```
   The target database and table are created automatically. See the ingestor
   `DOCS.md` for the full schema and all options.
4. Install **Redash**, open it on `http://HA_IP/`, point a ClickHouse data
   source at the HTTP endpoint and build dashboards on `homeassistant.states`.

Example query:

```sql
SELECT last_updated, state_float
FROM homeassistant.states
WHERE entity_id = 'sensor.living_room_temperature'
ORDER BY last_updated DESC
LIMIT 100;
```

## Legacy setup (PostgreSQL relay) — deprecated

> ⚠️ This pipeline relies on ClickHouse's **experimental**
> `MaterializedPostgreSQL` engine and an extra PostgreSQL instance. It is kept
> for existing installs but new setups should use the ingestor above.

1. Install the PostgreSQL and ClickHouse add-ons from the repo.
2. Point the HA recorder at PostgreSQL in `configuration.yaml`:
   ```yaml
   recorder:
     db_url: "postgresql://homeassistant:homeassistant@homeassistant/homeassistant"
   ```
3. (optional) migrate SQLite.
4. Create the incremental materialized view in PostgreSQL:
   ```sql
   CREATE EXTENSION pg_ivm;
   SELECT create_immv('states_view', 'SELECT
   CASE WHEN state~E''^\\d+$'' THEN state::integer ELSE 0 END as state_num,
   CASE WHEN state~E''^[+-]?([0-9]*[.])?[0-9]+$'' THEN state::float ELSE 0.0 END as state_float,
   CASE WHEN NOT (state~E''^[+-]?([0-9]*[.])?[0-9]+$'' OR state~E''^\\d+$''  ) THEN state ELSE '''' END
   AS state_str,
   to_timestamp(last_updated_ts) as ts,
   sm.entity_id
   FROM states
   INNER JOIN states_meta sm
   using(metadata_id)');
   create unique index on states_view (ts);
   alter table states_view ALTER COLUMN ts SET NOT NULL;
   ALTER TABLE states_view REPLICA IDENTITY USING INDEX states_view_ts_idx;
   ```
5. Create the materialized database in ClickHouse:
   ```sql
   CREATE DATABASE homeassistant
   ENGINE = MaterializedPostgreSQL('HA_IP:5432', 'homeassistant', 'homeassistant', 'homeassistant');
   ```
6. Install Redash and access it on `http://HA_IP/`.
7. Enjoy ClickHouse!
