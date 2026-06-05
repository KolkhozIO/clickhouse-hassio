# Home Assistant Add-on: ClickHouse

Runs a ClickHouse server for storing Home Assistant history.

## Ports

- HTTP interface on host port `8124` (container `8123`)
- Native protocol on host port `9001` (container `9000`)

## Usage

Pair this with the **ClickHouse Ingestor** add-on (recommended) to stream state
changes directly into ClickHouse, or with the deprecated PostgreSQL relay. See
the repository `README.md` for both setups.
