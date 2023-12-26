ARG BUILD_FROM=ghcr.io/hassio-addons/base/aarch64:14.3.3
ARG BUILD_ARCH=aarch64

FROM $BUILD_FROM

RUN \
  apk add --no-cache \
    python3 curl docker-cli pwgen perl-utils

RUN curl -L "https://github.com/docker/compose/releases/download/v2.23.3/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
RUN chmod +x /usr/local/bin/docker-compose

COPY setup.sh /setup.sh
RUN chmod +x /setup.sh
WORKDIR /
RUN ./setup.sh

COPY docker-compose.yml /docker-compose.yml
ENV DOCKER_SOCK="unix:///run/docker.sock"
ENV DOCKER_HOST="unix:///run/docker.sock"
CMD docker-compose run --rm server create_db && docker-compose up