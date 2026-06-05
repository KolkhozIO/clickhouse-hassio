ARG BUILD_FROM=ghcr.io/hassio-addons/base/aarch64:17.2.1
FROM $BUILD_FROM

# websockets is pure-Python, so no compiler/build deps are needed.
RUN apk add --no-cache python3 py3-pip \
    && pip3 install --no-cache-dir --break-system-packages "websockets==13.1"

COPY ingestor.py /app/ingestor.py
COPY rootfs /

LABEL \
    io.hass.name="ClickHouse Ingestor Add-on" \
    io.hass.description="Stream Home Assistant state changes directly into ClickHouse" \
    io.hass.type="addon" \
    io.hass.version="1.0.0"
