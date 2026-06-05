# Home Assistant Add-on: PostgreSQL

> **⚠️ Deprecated.** This add-on was only a relay for the
> `HA -> PostgreSQL -> ClickHouse (MaterializedPostgreSQL)` pipeline. New
> installs should use the **ClickHouse Ingestor** add-on, which streams state
> changes directly into ClickHouse and removes the need for PostgreSQL,
> `pg_ivm` and the experimental `MaterializedPostgreSQL` engine.
>
> The add-on still works for existing setups and continues to receive base-image
> and version maintenance.

## How to use

This add-on runs a PostgreSQL 17 server with the `pg_ivm` extension available,
listening on port `5432`. It is intended to be used as the Home Assistant
`recorder` database when running the legacy relay pipeline. See the repository
`README.md` for the full setup.
