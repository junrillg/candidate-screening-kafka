FROM eclipse-temurin:25-jdk-noble@sha256:ba0f0e1a33864105e1d454e88105c617ab40f71062db1f306bf58be32a3f8e5e AS auth-tool-build
WORKDIR /auth
# Same BCrypt implementation as the pinned application; byte lock, no publisher signature claim.
ADD --checksum=sha256:e183f6f59404fc1e12073cfea4ace7ea103c900463cd21fb609a7c617ecdf624 https://repo.maven.apache.org/maven2/org/mindrot/jbcrypt/0.4/jbcrypt-0.4.jar /auth/jbcrypt.jar
COPY docker/akhq/HashPassword.java /auth/
RUN javac -Xlint:all -Werror -cp /auth/jbcrypt.jar /auth/HashPassword.java \
    && java -Xms8m -Xmx32m -XX:MaxMetaspaceSize=32m -XX:ReservedCodeCacheSize=16m \
      -XX:-UsePerfData -cp /auth:/auth/jbcrypt.jar HashPasswordSelfTest

FROM eclipse-temurin:25-jre-alpine@sha256:3c0a9084927a221ccd1d007fcaf614465672c0af37aaa834c5184483afe56d61 AS auth-tool
# Match the viewer's signed distribution update boundary; scan this distinct image.
RUN apk upgrade --no-cache
WORKDIR /auth
COPY --from=auth-tool-build --chmod=0444 /auth/HashPassword.class /auth/jbcrypt.jar /auth/
COPY docker/akhq/jbcrypt-LICENSE.txt /usr/share/licenses/jbcrypt/LICENSE
USER 10001:10001
ENTRYPOINT ["/opt/java/openjdk/bin/java", "-Xms8m", "-Xmx32m", "-XX:MaxMetaspaceSize=32m", "-XX:ReservedCodeCacheSize=16m", "-XX:-UsePerfData", "-cp", "/auth:/auth/jbcrypt.jar", "HashPassword"]
CMD []

FROM eclipse-temurin:25-jdk-noble@sha256:ba0f0e1a33864105e1d454e88105c617ab40f71062db1f306bf58be32a3f8e5e AS frontend-checks
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git libatomic1=14.2.0-4ubuntu2~24.04.1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY docker/akhq/security-dependencies.patch docker/akhq/frontend-quality.patch docker/akhq/runtime-compatibility.patch docker/akhq/selected-source.sha256 docker/akhq/frontend-ci.init.gradle ./
# The commit-addressed archive is byte-locked; no publisher signature is claimed.
RUN set -eu; \
    curl --fail --location --retry 2 --max-time 120 \
      https://codeload.github.com/tchiotludo/akhq/tar.gz/725a615c22bb38a04c9e1b01e48bb46a7e480ee4 -o source.tgz; \
    echo '6c87ff240cbafa55556cba61add089e2d8b278c1e631f336b7c69adb2e8c79bf  source.tgz' | sha256sum -c -; \
    mkdir source; tar -xzf source.tgz -C source --strip-components=1; \
    cd source; git apply --check ../security-dependencies.patch; \
    git apply ../security-dependencies.patch; \
    git apply --check ../frontend-quality.patch; git apply ../frontend-quality.patch; \
    git apply --check ../runtime-compatibility.patch; git apply ../runtime-compatibility.patch; \
    sha256sum -c ../selected-source.sha256
WORKDIR /build/source
# Upstream frontend tasks download glibc Node; use a glibc builder.
# Keep the selected frontend lock, strict install policy and merged Shadow JAR metadata.
ENV GRADLE_USER_HOME=/build/gradle-home \
    PATH="/opt/akhq-node/bin:${PATH}" \
    NPM_CONFIG_STRICT_ALLOW_SCRIPTS=true \
    CI=true ESLINT_USE_FLAT_CONFIG=false \
    NPM_CONFIG_USERCONFIG=/build/npm-user.npmrc NPM_CONFIG_GLOBALCONFIG=/build/npm-global.npmrc \
    GRADLE_OPTS="-Dorg.gradle.jvmargs=-Xmx512m -Dorg.gradle.workers.max=1" \
    NODE_OPTIONS="--max-old-space-size=512"
RUN set -eu; : > /build/npm-user.npmrc; : > /build/npm-global.npmrc; \
    ./gradlew --no-daemon --no-parallel --max-workers=1 \
      --init-script /build/frontend-ci.init.gradle :client:installFrontend; \
    test "$(node --version)" = v26.3.1; \
    test "$(corepack --version)" = 0.35.0; \
    test "$(cd client && npm --version)" = 11.16.0; \
    cd client; npm run lint; npm run prettier:check; \
    npm test -- --run --maxWorkers=1 --no-file-parallelism --reporter=verbose; \
    npm run build; npm audit; npm ls --all; test -s build/index.html; \
    cd ..; sha256sum -c /build/selected-source.sha256
FROM frontend-checks AS build
# Frontend gates already passed in this exact stage; keep normal resource copying.
RUN ./gradlew --no-daemon --no-parallel --max-workers=1 --warning-mode all --stacktrace \
      --init-script /build/frontend-ci.init.gradle \
      -x :client:installFrontend -x :client:assembleFrontend \
      test --tests org.akhq.configs.security.BasicAuthTest \
      --tests org.akhq.compatibility.KafkaCompatibilityTest \
      --tests org.akhq.compatibility.InputParsingSecurityTest -x startTestKafkaCluster \
    && ./gradlew --no-daemon --no-parallel --max-workers=1 --warning-mode all --stacktrace \
      --init-script /build/frontend-ci.init.gradle \
      -x :client:installFrontend -x :client:assembleFrontend shadowJar \
    && ./gradlew --no-daemon --no-parallel --max-workers=1 --warning-mode all \
      --init-script /build/frontend-ci.init.gradle \
      dependencies --configuration runtimeClasspath > runtime-dependencies.txt
# Fixed in-memory compatibility probe: the build sandbox has no network.
RUN --network=none java -Xms16m -Xmx128m -XX:MaxMetaspaceSize=96m \
      -XX:ReservedCodeCacheSize=32m -XX:-UsePerfData \
      -cp build/libs/akhq-0.28.0-all.jar:build/classes/java/test \
      org.akhq.compatibility.DerivativeRuntimeProbe

FROM eclipse-temurin:25-jre-noble@sha256:3f2223fe04b750aa5a8179e29b5c9a1ddf7665fc9bb08bfe11ad01d143bc13a5 AS runtime-base
# The selected Linux ARM JNI artifacts require glibc; keep signed distribution updates.
# wget serves the existing healthcheck, ca-certificates the trust store, passwd user creation.
RUN set -eu; apt-get update; apt-get upgrade -y; \
    apt-get install -y --no-install-recommends ca-certificates wget passwd; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --system --gid 10001 akhq; \
    useradd --system --uid 10001 --gid akhq --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin akhq
WORKDIR /app
COPY --from=build --chmod=0444 /build/source/build/libs/akhq-0.28.0-all.jar /app/akhq.jar
COPY --from=build /build/source/LICENSE /usr/share/licenses/akhq/LICENSE
COPY --from=build /build/source/runtime-dependencies.txt /usr/share/akhq/provenance/runtime-dependencies.txt
COPY --from=build /build/source/gradle.properties /usr/share/akhq/provenance/gradle.properties
COPY --from=build /build/source/client/package.json /build/source/client/package-lock.json /usr/share/akhq/provenance/client/
COPY docker/akhq/security-dependencies.patch docker/akhq/frontend-quality.patch docker/akhq/runtime-compatibility.patch docker/akhq/selected-source.sha256 docker/akhq/frontend-ci.init.gradle docker/akhq/source-provenance.json /usr/share/akhq/provenance/
COPY docker/akhq/application.yml docker/akhq/logback.xml /app/
COPY --chmod=0555 docker/akhq/entrypoint.sh /app/entrypoint.sh
ENV MICRONAUT_CONFIG_FILES=/app/application.yml
LABEL org.opencontainers.image.title="Kafka inspection viewer source derivative" \
    org.opencontainers.image.version="0.28.0-security-frontend" \
    org.opencontainers.image.source="https://github.com/tchiotludo/akhq" \
    org.opencontainers.image.revision="725a615c22bb38a04c9e1b01e48bb46a7e480ee4"
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1:28081/health || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]

# Check the exact selected JRE and updated runtime filesystem; do not ship the probe.
FROM runtime-base AS runtime-checks
COPY --from=build --chmod=u=rX,go=rX /build/source/build/classes/java/test/org/akhq/compatibility/DerivativeRuntimeProbe*.class /verify/org/akhq/compatibility/
RUN --network=none java -Xms16m -Xmx128m -XX:MaxMetaspaceSize=96m \
      -XX:ReservedCodeCacheSize=32m -XX:-UsePerfData \
      -cp /app/akhq.jar:/verify org.akhq.compatibility.DerivativeRuntimeProbe \
    && printf '%s\n' 'PASS fixed no-network packaged compatibility probe on selected JRE' > /tmp/akhq-runtime-probe-passed

FROM runtime-base AS runtime
COPY --from=runtime-checks --chmod=0444 /tmp/akhq-runtime-probe-passed /usr/share/akhq/provenance/runtime-probe.txt
