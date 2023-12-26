# Clickhouse Home Assistant add-on repository

This repository contains Clickhouse Home Assistant add-on based on official Clickhouse image.

NB: This is a first(!) step to store history data in ClickHouse

0. Add the repo in HA addons store `https://github.com/apbodrov/clickhouse-hassio.git`
1. Install PostgreSQL, ClickHouse addon from the repo
2. Setup recorder in configuration.yaml
```
recorder:
  db_url: "postgresql://homeassistant:homeassistant@homeassistant/homeassistant"
```
3. (optional) migrate SQLite
4. Run SQL in PostgreSQL to create incremental mat view
```
CREATE EXTENSION pg_ivm;
SELECT 
create_immv('states_view', 'SELECT
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
5. Run SQL in Clickhouse to create mat view

```
CREATE DATABASE homeassistant
ENGINE = MaterializedPostgreSQL('HA_IP:5432', 'homeassistant', 'homeassistant', 'homeassistant')


```
6. Install Redash and access it on http://HA_IP/
7. Enjoy ClickHouse!
