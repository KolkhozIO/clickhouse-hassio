<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.0.0

- Initial release
- Streams Home Assistant `state_changed` events directly into ClickHouse over
  the HTTP interface, replacing the PostgreSQL + `MaterializedPostgreSQL` relay
- Auto-creates the target database and `MergeTree` table
- Configurable batching, attribute capture and per-entity exclusions
