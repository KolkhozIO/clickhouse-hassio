ARG BUILD_FROM=ghcr.io/hassio-addons/base/aarch64:17.2.1
ARG BUILD_ARCH=aarch64

FROM $BUILD_FROM

RUN \
  apk add --no-cache \
    python3 curl docker-cli docker-compose pwgen perl-utils

COPY setup.sh /setup.sh
RUN chmod +x /setup.sh
WORKDIR /
RUN ./setup.sh

COPY docker-compose.yml /docker-compose.yml
ENV DOCKER_SOCK="unix:///run/docker.sock"
ENV DOCKER_HOST="unix:///run/docker.sock"
CMD ["/usr/bin/bash", "-c", "docker-compose run --rm server create_db && docker-compose up"]