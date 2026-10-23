FROM eclipse-temurin:25-jdk-alpine@sha256:3fd2d245c4e0eba615fe366a71b8bd25f5db7104f53e4026b24bf508b880bd2a AS source

RUN apk add --no-cache bash=5.3.9-r1 git=2.54.0-r0 gnupg \
    && gpg --version >/dev/null
WORKDIR /build
COPY docker/kafka/release-key.asc docker/kafka/security-dependencies.patch ./
RUN set -eu; \
    wget -T 30 -O kafka-src.tgz https://downloads.apache.org/kafka/4.3.1/kafka-4.3.1-src.tgz; \
    echo 'e9d1ed5f47901bf0229a97f464d863ea44dd1af455937fb906dd624e9fcb814c89f76d507253620948adae17922ccbb4304865db2797b24959a2471d1af7c9ef  kafka-src.tgz' | sha512sum -c -; \
    wget -T 30 -O kafka-src.tgz.asc https://downloads.apache.org/kafka/4.3.1/kafka-4.3.1-src.tgz.asc; \
    export GNUPGHOME=/tmp/release-keys; \
    mkdir -m 700 "$GNUPGHOME"; \
    gpg --batch --no-autostart --import release-key.asc; \
    gpg --batch --no-autostart --status-fd 1 --verify kafka-src.tgz.asc kafka-src.tgz > signature-status; \
    grep -F '[GNUPG:] VALIDSIG 365454B8BAF2D52CCCE65EB471650CBCEA26F8A4 ' signature-status; \
    mkdir source; \
    tar -xzf kafka-src.tgz -C source --strip-components=1; \
    cd source; \
    git apply --check ../security-dependencies.patch; \
    git apply ../security-dependencies.patch; \
    grep -Fx 'distributionSha256Sum=72f44c9f8ebcb1af43838f45ee5c4aa9c5444898b3468ab3f4af7b6076c5bc3f' gradle/wrapper/gradle-wrapper.properties

WORKDIR /build/source
ENV GRADLE_USER_HOME=/build/gradle-home
COPY docker/kafka/build-limits.init.gradle /build/build-limits.init.gradle
RUN set -eu; \
    wget -T 30 -O gradle/wrapper/gradle-wrapper.jar https://raw.githubusercontent.com/gradle/gradle/v9.2.1/gradle/wrapper/gradle-wrapper.jar; \
    echo '423cb469ccc0ecc31f0e4e1c309976198ccb734cdcbb7029d4bda0f18f57e8d9  gradle/wrapper/gradle-wrapper.jar' | sha256sum -c -
COPY docker/kafka/verification-metadata.xml /build/source/gradle/verification-metadata.xml
RUN set -eu; \
    mkdir /build/kafka-evidence; \
    for metric in memory.max cpu.max; do \
      if test -r "/sys/fs/cgroup/$metric"; then cat "/sys/fs/cgroup/$metric" > "/build/kafka-evidence/$metric"; fi; \
    done; \
    run_gradle() { \
      ./gradlew --no-daemon --no-parallel --max-workers=1 --dependency-verification=strict \
    '-Dorg.gradle.jvmargs=-Xmx768m -Xss4m -XX:+UseParallelGC' \
    --init-script /build/build-limits.init.gradle \
    -PmaxParallelForks=1 -PmaxScalacThreads=1 \
    -PcommitId=26b251a451ce941d3d7a55e6487bcb7f16b5ad48 \
      "$@"; \
    }; \
    retain_phase() { \
      for report in task-limits.json resolved-graph.json build-dependencies.spdx.json; do \
        cp "/build/kafka-evidence/$report" "/build/kafka-evidence/$1-$report"; \
      done; \
    }; \
    run_gradle :clients:testClasses :server-common:testClasses :core:testClasses :tools:testClasses; \
    retain_phase compile; \
    run_gradle :clients:checkstyleMain :clients:checkstyleTest \
      :server-common:checkstyleMain :server-common:checkstyleTest \
      :core:checkstyleMain :core:checkstyleTest :tools:checkstyleMain :tools:checkstyleTest; \
    retain_phase checkstyle; \
    run_gradle \
    :clients:test --tests org.apache.kafka.common.compress.Lz4CompressionTest \
    :server-common:test --tests org.apache.kafka.server.util.JsonTest \
    :core:test --tests kafka.docker.KafkaDockerWrapperTest --tests kafka.log.LogConfigTest \
    :tools:test --tests org.apache.kafka.tools.ConsoleProducerTest --tests org.apache.kafka.tools.consumer.ConsoleConsumerTest \
    :core:releaseTarGz; \
    retain_phase selected-tests-release; \
    find . -path '*/build/test-results/test/TEST-*.xml' -exec sh -c 'target="/build/kafka-evidence/$1"; mkdir -p "${target%/*}"; cp "$1" "$target"' sh {} \;; \
    find "$GRADLE_USER_HOME/caches/modules-2/files-2.1" -type f -exec sha256sum {} + > /build/kafka-evidence/downloaded-artifacts.sha256; \
    cp gradle/wrapper/gradle-wrapper.properties /build/kafka-evidence/; \
    apk info -v > /build/kafka-evidence/builder-packages.txt

FROM scratch AS build-evidence
COPY --from=source /build/kafka-evidence/ /

FROM eclipse-temurin:21-jre-alpine@sha256:51ab5e3302e7141ce665ca3ea85e8b5cd648eafbc3c0c90dd79d6537684e4555 AS runtime

# Keep the signed distribution packages containing the reported fixes explicit.
RUN set -eu; apk add --no-cache bash=5.3.9-r1 procps-ng=4.0.6-r0; \
    apk add --upgrade --no-cache \
    libcrypto3=3.5.9-r0 libssl3=3.5.9-r0 openssl=3.5.9-r0 \
    libexpat=2.9.0-r0 p11-kit=0.26.2-r0 p11-kit-trust=0.26.2-r0 sqlite-libs=3.53.4-r0 \
    libpng=1.6.59-r0 musl-utils=1.2.6-r3 zlib=1.3.2-r1
COPY --from=source /build/source/core/build/distributions/kafka_2.13-4.3.1.tgz /tmp/kafka.tgz
RUN set -eu; \
    mkdir -p /opt/kafka /etc/kafka/docker /var/lib/kafka/data /etc/kafka/secrets /mnt/shared/config /usr/logs; \
    tar -xzf /tmp/kafka.tgz -C /opt/kafka --strip-components=1; \
    rm /tmp/kafka.tgz; \
    adduser -h /home/appuser -D --shell /bin/bash appuser; \
    cp /opt/kafka/config/log4j2.yaml /etc/kafka/docker/log4j2.yaml; \
    cp /opt/kafka/config/tools-log4j2.yaml /etc/kafka/docker/tools-log4j2.yaml; \
    chown -R appuser:root /opt/kafka /etc/kafka /var/lib/kafka /mnt/shared/config /usr/logs; \
    chmod -R ug+w /etc/kafka /var/lib/kafka /mnt/shared/config
COPY --from=source --chown=appuser:root /build/source/docker/resources/common-scripts/ /etc/kafka/docker/
COPY --from=source --chown=appuser:root /build/source/docker/jvm/launch /build/source/docker/server.properties /etc/kafka/docker/
COPY --from=source /build/source/docker/jvm/jsa_launch /etc/kafka/docker/jsa_launch
COPY docker/kafka/security-dependencies.patch /opt/kafka/security-dependencies.patch

# Generate archives with the same runtime that loads them, using upstream's checks.
WORKDIR /
RUN set -eu; KAFKA_HEAP_OPTS='-Xms128m -Xmx256m' /etc/kafka/docker/jsa_launch; \
    mv /kafka.jsa /storage.jsa /opt/kafka/; \
    rm -rf /tmp/kraft-combined-logs /opt/kafka/logs; \
    mkdir /opt/kafka/logs; \
    chown appuser:root /opt/kafka/logs

LABEL org.opencontainers.image.title="Kafka security dependency derivative" \
    org.opencontainers.image.version="4.3.1-security-dependencies" \
    org.opencontainers.image.source="https://github.com/apache/kafka/tree/4.3.1" \
    org.opencontainers.image.revision="26b251a451ce941d3d7a55e6487bcb7f16b5ad48"
USER appuser
EXPOSE 9092
VOLUME ["/etc/kafka/secrets", "/var/lib/kafka/data", "/mnt/shared/config"]
ENTRYPOINT []
CMD ["/etc/kafka/docker/run"]
