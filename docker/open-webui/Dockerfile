# syntax=docker/dockerfile:1.7
# Local security derivative; upstream application and branding remain Open WebUI 0.12.3.
FROM ghcr.io/open-webui/open-webui:v0.12.3@sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def AS assets
FROM python:3.11.17-alpine3.24@sha256:d9368b3a5ac59afea7b5d4f2e2aea0941dbf9fdee9c369c5bec00b98244bc929 AS base
ARG TARGETARCH
RUN test "$TARGETARCH" = arm64
COPY docker/open-webui/ /checks/
RUN python /checks/check.py config \
    && apk upgrade --no-cache \
    && apk add --no-cache bash curl=8.22.0-r0 jq ca-certificates re2=2025.11.05-r3 \
        libuuid=2.42.3-r1 libncursesw=6.6_p20260516-r0 scanelf=1.3.9-r1 \
    && ! apk info --exists acl-libs \
    && python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /checks/bootstrap.lock \
    && python -m pip check \
    && python /checks/check.py tools

FROM base AS source
ADD --checksum=sha256:80323c7eeb41f816b8dc8969f3ad830cb57d426bbcf842418f87ab6f027b1fd1 https://codeload.github.com/open-webui/open-webui/tar.gz/91ea4e3421d1d8069f13d4a463fcb5986d94ea60 /upstream.tar.gz
RUN mkdir /source && tar -xzf /upstream.tar.gz --strip-components=1 -C /source && rm /upstream.tar.gz
RUN python /checks/check.py source /source

# First export this target's APK inventory, freeze it, then build two independent wheels.
FROM base AS binding-toolchain
RUN apk add --no-cache gcc=15.2.0-r5 g++=15.2.0-r5 musl-dev=1.2.6-r3 \
        libgcc=15.2.0-r5 libstdc++=15.2.0-r5 binutils=2.45.1-r1 re2-dev=2025.11.05-r3 \
    && python -m venv /opt/build \
    && /opt/build/bin/python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /checks/build-tools.lock \
    && /opt/build/bin/python -m pip check
ENV PATH=/opt/build/bin:$PATH PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python /checks/check.py toolchain
FROM scratch AS native-toolchain-export
COPY --from=binding-toolchain /out/ /

FROM binding-toolchain AS binding
ADD --checksum=sha256:1db14a292ee8303b91e91e7c37e05ac17d3c467f29416c79ac70a78be3e65bda https://files.pythonhosted.org/packages/6b/60/805c654ba53d685513df955ee745f71920fe8e6a284faf0f9b9dc19b659c/google_re2-1.1.20251105.tar.gz /build/google-re2.tar.gz
ADD --checksum=sha256:5719d8b7e96334d8fa838e0abbf769bfb82a9d6b58d54a4968cd68c5fcc090e1 https://raw.githubusercontent.com/google/re2/927f5d53caf8111721e734cf24724686bb745f55/python/re2_test.py /build/re2_test.py
RUN mkdir /build/google-re2 && tar -xzf /build/google-re2.tar.gz --strip-components=1 -C /build/google-re2
WORKDIR /build/google-re2
RUN --network=none python /checks/check.py binding /build/google-re2 \
    && python /checks/check.py wheel /out
FROM scratch AS native-wheel-export
COPY --from=binding /out/ /

FROM base AS dependencies
# This rejects an unresolved musl lock before any application package installation.
RUN python /checks/check.py lock
COPY --from=binding /out/ /native-wheel/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /checks/bootstrap.lock \
    && /opt/venv/bin/python -m pip install --no-cache-dir --only-binary=:all: --require-hashes \
        --find-links=/native-wheel -r /checks/requirements-musl.lock \
    && /opt/venv/bin/python -m pip check \
    && /opt/venv/bin/python /checks/check.py tools

FROM base AS runtime-files
RUN apk add --no-cache tcl=8.6.17-r1 tk=8.6.17-r1
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 ENV=prod PORT=8080 \
    USE_SLIM_DOCKER=true USE_OLLAMA_DOCKER=false USE_CUDA_DOCKER=false \
    SCARF_NO_ANALYTICS=true DO_NOT_TRACK=true ANONYMIZED_TELEMETRY=false \
    OFFLINE_MODE=true HF_HUB_OFFLINE=1 DOCKER=true \
    WEBUI_BUILD_HASH=91ea4e3421d1d8069f13d4a463fcb5986d94ea60
WORKDIR /app/backend
COPY --from=source /source/backend/ /app/backend/
COPY --from=source /source/LICENSE /source/LICENSE_HISTORY /source/LICENSE_NOTICE /source/CONTRIBUTOR_LICENSE_AGREEMENT /app/licenses/
COPY --from=source /source/src/lib/encryption/vendor/THIRD_PARTY_LICENSES.txt /app/licenses/frontend-encryption-THIRD_PARTY_LICENSES.txt
COPY --from=source /source/package.json /source/CHANGELOG.md /app/
COPY --from=assets /app/build/ /app/build/
COPY --from=assets /app/backend/open_webui/static/ /app/backend/open_webui/static/
COPY --from=assets /app/package.json /checks/asset-package.json
COPY --from=assets /app/backend/open_webui/utils/auth.py /checks/asset-auth.py
COPY --from=assets /app/backend/start.sh /checks/asset-start.sh
COPY --from=dependencies /opt/venv/ /opt/venv/
COPY --from=dependencies /usr/share/open-webui-derivative/installer-venv.json /usr/share/open-webui-derivative/installer-venv.json
# No original site-packages, OS libraries or persistent data are inherited from assets.
RUN cp /checks/requirements-slim.in /app/backend/requirements-slim.txt \
    && mkdir -p /app/backend/data /usr/share/open-webui-derivative \
    && python /checks/check.py installed /app \
    && /usr/local/bin/python -m pip check && /opt/venv/bin/python -m pip check \
    && apk info -vv > /usr/share/open-webui-derivative/os-packages.txt
FROM runtime-files AS runtime
RUN --network=none python /checks/test_native_check.py \
    && python /checks/check.py native \
    && python /checks/check.py auth /app/backend \
    && python /checks/check.py imports /app/backend
EXPOSE 8080
HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-8080}/health | jq -ne 'input.status == true' || exit 1
# Preserve upstream default UID and startup behavior; data-volume ownership is root-controlled.
CMD ["bash", "start.sh"]
