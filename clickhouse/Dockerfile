ARG BUILD_FROM=ghcr.io/hassio-addons/base/aarch64:17.2.1
ARG BUILD_ARCH=aarch64

FROM ubuntu:22.04 AS glibc-donor

ARG BUILD_ARCH
RUN arch=${BUILD_ARCH:-amd64} \
    && case $arch in \
        amd64) rarch=x86_64 ;; \
        aarch64|arm64) rarch=aarch64 ;; \
    esac \
    && ln -s "${rarch}-linux-gnu" /lib/linux-gnu


FROM $BUILD_FROM

ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    TZ=UTC \
    CLICKHOUSE_CONFIG=/etc/clickhouse-server/config.xml

COPY --from=glibc-donor /lib/linux-gnu/libc.so.6 /lib/linux-gnu/libdl.so.2 /lib/linux-gnu/libm.so.6 /lib/linux-gnu/libpthread.so.0 /lib/linux-gnu/librt.so.1 /lib/linux-gnu/libnss_dns.so.2 /lib/linux-gnu/libnss_files.so.2 /lib/linux-gnu/libresolv.so.2 /lib/linux-gnu/ld-linux-* /lib/
COPY --from=glibc-donor /etc/nsswitch.conf /etc/
COPY docker_related_config.xml /etc/clickhouse-server/config.d/
COPY entrypoint.sh /entrypoint.sh

ARG BUILD_ARCH
RUN arch=${BUILD_ARCH:-amd64} \
    && case $arch in \
        amd64) mkdir -p /lib64 && ln -sf /lib/ld-linux-x86-64.so.2 /lib64/ld-linux-x86-64.so.2 ;; \
        aarch64|arm64) : ;; \
    esac

# lts / testing / prestable / etc
ARG REPO_CHANNEL="lts"
ARG REPOSITORY="https://packages.clickhouse.com/tgz/${REPO_CHANNEL}"
ARG VERSION="25.8.24.21"
ARG PACKAGES="clickhouse-client clickhouse-server clickhouse-common-static"

RUN arch=${BUILD_ARCH:-amd64} \
    && case $arch in \
        aarch64) dlarch=arm64 ;; \
        *) dlarch=$arch ;; \
    esac \
    && for package in ${PACKAGES}; do \
    ( \
        cd /tmp \
        && echo "Get ${REPOSITORY}/${package}-${VERSION}-${dlarch}.tgz" \
            && wget -c -q "${REPOSITORY}/${package}-${VERSION}-${dlarch}.tgz" \
            && wget -c -q "${REPOSITORY}/${package}-${VERSION}-${dlarch}.tgz.sha512" \
            && sed 's:/output/:/tmp/:' < "${package}-${VERSION}-${dlarch}.tgz.sha512" | sha512sum -c \
            && tar xvzf "${package}-${VERSION}-${dlarch}.tgz" --strip-components=1 -C / \
    ) \
    ; done \
    && rm /tmp/*.tgz /install -rf \
    && addgroup -S -g 101 clickhouse \
    && adduser -S -h /var/lib/clickhouse -s /bin/bash -G clickhouse -g "ClickHouse server" -u 101 clickhouse \
    && mkdir -p /var/lib/clickhouse /var/log/clickhouse-server /etc/clickhouse-server/config.d /etc/clickhouse-server/users.d /etc/clickhouse-client /docker-entrypoint-initdb.d \
    && chown clickhouse:clickhouse /var/lib/clickhouse \
    && chown root:clickhouse /var/log/clickhouse-server \
    && chmod +x /entrypoint.sh \
    && apk add --no-cache bash tzdata \
    && cp /usr/share/zoneinfo/UTC /etc/localtime \
    && echo "UTC" > /etc/timezone \
    && chmod ugo+Xrw -R /var/lib/clickhouse /var/log/clickhouse-server /etc/clickhouse-server /etc/clickhouse-client

EXPOSE 9000 8123 9009

VOLUME /var/lib/clickhouse \
       /var/log/clickhouse-server

COPY default_setting.xml /etc/clickhouse-server/users.d/

ENTRYPOINT ["/entrypoint.sh"]

# we need to allow "others" access to clickhouse folder, because docker container
# can be started with arbitrary uid (openshift usecase)
