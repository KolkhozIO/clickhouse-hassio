<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.1.0

- Upgraded ClickHouse 23.11.1 -> 24.8.12.28 (LTS)
- Upgraded add-on base image and glibc donor (Ubuntu 20.04 -> 22.04)
- Fixed the aarch64 dynamic-loader setup so the image builds and runs on arm64
- Restored sha512 verification of downloaded ClickHouse packages

## 1.0.0

- Initial release
