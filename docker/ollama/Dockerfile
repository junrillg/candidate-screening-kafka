# Rebuild the Go application; retain the exact release's native runtime payload.
FROM ollama/ollama:0.40.6@sha256:265409e1d931028a8f7d5a32bcbafddd7bffbc6cc629d77efe9560adf692a5e7 AS upstream

FROM upstream AS build
ARG TARGETARCH
ENV PATH=/usr/local/go/bin:$PATH CGO_ENABLED=1 GOTOOLCHAIN=local \
    GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org \
    GOMAXPROCS=2 GOMEMLIMIT=768MiB GOFLAGS=-p=1
RUN set -eu; \
    mkdir -p /build; \
    cp /usr/bin/ollama /build/original-ollama; \
    ldd /usr/bin/ollama > /build/original-linkage.txt; \
    libc_path=$(awk '$1 == "libc.so.6" {print $3}' /build/original-linkage.txt); \
    test -n "$libc_path"; \
    cp "$libc_path" /build/runtime-libc.so.6; \
    mkdir /build/runtime-libs; \
    awk '$2 == "=>" && $3 ~ /^\// {print $3} $1 ~ /^\// {print $1}' /build/original-linkage.txt > /build/runtime-lib-paths.txt; \
    while IFS= read -r lib; do cp "$lib" /build/runtime-libs/; done < /build/runtime-lib-paths.txt; \
    getconf GNU_LIBC_VERSION > /build/runtime-glibc.txt
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gcc g++ libc6-dev make cmake git patch python3 binutils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) go_sha=ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5 ;; \
      arm64) go_sha=3f4227ce7b206a513e794f1393adb473a07b46e94d18bd15d1c684bbb7319797 ;; \
      *) echo 'Unsupported build architecture' >&2; exit 1 ;; \
    esac; \
    curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 180 --retry 2 --retry-delay 2 --retry-max-time 360 \
      "https://dl.google.com/go/go1.26.10.linux-${TARGETARCH}.tar.gz" -o go.tgz; \
    echo "$go_sha  go.tgz" | sha256sum -c -; \
    tar -xzf go.tgz -C /usr/local; \
    rm go.tgz; \
    test "$(go env GOVERSION)" = go1.26.10; \
    curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 180 --retry 2 --retry-delay 2 --retry-max-time 360 \
      https://codeload.github.com/ollama/ollama/tar.gz/2476d5ead197abe04629651028f83d48dc75d2b2 -o source.tgz; \
    echo 'e9e51bc34c6541e144a932e3c4aaa9a131d4c7d458631156719fdcbeed7b066c  source.tgz' | sha256sum -c -; \
    mkdir source; \
    tar -xzf source.tgz -C source --strip-components=1; \
    rm source.tgz
COPY docker/ollama/source-lock.json docker/ollama/security-dependencies.patch docker/ollama/check-build.py ./
RUN python3 check-build.py source
WORKDIR /build/source
RUN patch --batch --fuzz=0 -p1 < ../security-dependencies.patch \
    && go mod download && go mod verify \
    && mkdir -p /out \
    && go list -mod=readonly -m -json all > /out/modules.json \
    && python3 ../check-build.py graph
# Upstream discovery unit fixtures assume no installed /lib/ollama payload.
# Isolate it in this builder only, restoring it even when a test fails.
RUN --network=none set -eu; \
    go vet ./api ./auth ./envconfig ./server ./llm ./discover; \
    (mv /usr/lib/ollama /build/retained-test-payload; \
      trap 'mv /build/retained-test-payload /usr/lib/ollama' EXIT; \
      trap 'exit 1' HUP INT TERM; \
      go test -count=1 -timeout=120s ./api ./auth ./envconfig ./server ./llm ./discover); \
    go build -mod=readonly -trimpath -buildvcs=false -buildmode=pie \
      -ldflags='-X github.com/ollama/ollama/version.Version=0.40.6-local.1 -X github.com/ollama/ollama/server.mode=release' \
      -o /out/ollama . \
    && go version -m /out/ollama > /out/build-info.txt
RUN --network=none python3 ../check-build.py binary
RUN cmake -DGO_EXECUTABLE=/usr/local/go/bin/go -DSOURCE_DIR=/build/source \
      -DBINARY_DIR=/build/licenses -DOUTPUT_DIR=/out -DTARGETS=linux/${TARGETARCH} \
      -P cmake/generate_go_license.cmake \
    && test -s /out/GO_LICENSE \
    && cp go.mod go.sum LICENSE /out/ \
    && cp /usr/local/go/LICENSE /out/GO_TOOLCHAIN_LICENSE \
    && cp ../source-lock.json ../security-dependencies.patch ../runtime-glibc.txt /out/ \
    && cp ../original-linkage.txt /out/original-linkage.txt \
    && sha256sum /out/ollama /out/go.mod /out/go.sum /out/modules.json > /out/SHA256SUMS

FROM upstream AS runtime
# Capture the original payload before replacing the Go application and its licenses.
RUN mkdir -p /usr/local/share/ollama-build \
    && find /usr/lib/ollama -type f ! -name GO_LICENSE -exec sha256sum '{}' + > /tmp/native-payload.unsorted \
    && sort /tmp/native-payload.unsorted > /usr/local/share/ollama-build/native-payload.sha256 \
    && rm /tmp/native-payload.unsorted \
    && test -s /usr/local/share/ollama-build/native-payload.sha256
COPY --from=build /out/ollama /usr/bin/ollama
COPY --from=build /out/GO_LICENSE /usr/lib/ollama/GO_LICENSE
COPY --from=build /out/go.mod /out/go.sum /out/LICENSE /out/GO_TOOLCHAIN_LICENSE /out/modules.json /out/build-info.txt /out/source-lock.json /out/security-dependencies.patch /out/runtime-glibc.txt /out/original-linkage.txt /out/elf-versions.json /out/application.spdx.json /out/SHA256SUMS /usr/local/share/ollama-build/
RUN set -eu; \
    sha256sum -c /usr/local/share/ollama-build/native-payload.sha256 >/dev/null; \
    test "$(readlink -f /bin/ollama)" = /usr/bin/ollama; \
    ldd -r /usr/bin/ollama > /usr/local/share/ollama-build/linkage.txt 2>&1; \
    if grep -Eq 'not found|undefined symbol' /usr/local/share/ollama-build/linkage.txt; then exit 1; fi
LABEL org.opencontainers.image.title="Ollama dependency derivative" \
    org.opencontainers.image.version="0.40.6-local.1" \
    org.opencontainers.image.source="https://github.com/ollama/ollama/tree/2476d5ead197abe04629651028f83d48dc75d2b2" \
    org.opencontainers.image.revision="2476d5ead197abe04629651028f83d48dc75d2b2"
