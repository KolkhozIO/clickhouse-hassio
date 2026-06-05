<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.1.0

- **Deprecated.** This add-on existed only as a relay for the
  `HA -> PostgreSQL -> ClickHouse (MaterializedPostgreSQL)` pipeline. That
  pipeline is superseded by the new **ClickHouse Ingestor** add-on, which writes
  Home Assistant state changes straight into ClickHouse. The add-on still works
  and receives maintenance, but new installs should prefer the ingestor.
- Upgraded PostgreSQL 16.1 -> 17.2
- Upgraded `pg_ivm` 1.7 -> 1.9
- Upgraded add-on base image

## 1.0.0

- Initial release
