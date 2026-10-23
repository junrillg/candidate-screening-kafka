#!/bin/sh
set -eu
ollama serve >/tmp/ollama-setup.log 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT HUP INT TERM
attempt=0
until ollama list >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 60 ] || { echo 'Model setup server unavailable' >&2; exit 1; }
    sleep 1
done
ollama pull qwen2.5:1.5b-instruct
manifest=/root/.ollama/models/manifests/registry.ollama.ai/library/qwen2.5/1.5b-instruct
actual=$(sha256sum "$manifest")
actual=${actual%% *}
# Ollama normalizes the manifest and retains the original registry manifest as a blob.
original=$(sha256sum /root/.ollama/models/blobs/sha256-65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b)
original=${original%% *}
[ "$actual" = f2b0a490f661d58f20c4b98e5797ecf55e6e96293a88b71e96e7cc389f658f7e ] && \
[ "$original" = 65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b ] || {
    echo 'Model manifest changed; review the pin before using it' >&2
    exit 1
}
echo 'Pinned local model manifest verified'
