#!/bin/sh
set -eu
case "${1:-all}" in
    help|--help|-h) echo 'Usage: scripts/scan.sh [all|source|images] (findings block publication)'; exit 0 ;;
    all|source|images) mode=${1:-all} ;;
    *) echo 'Expected all, source or images' >&2; exit 2 ;;
esac
umask 077
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$root"
# Keep bind-mounted scratch beneath the repository's Docker-shared filesystem.
mkdir -p .artifacts
scratch=$(mktemp -d "$root/.artifacts/scan.XXXXXX")
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
trivy=aquasec/trivy:0.76.0@sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222
gitleaks=ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
if [ "$mode" != images ]; then
    mkdir "$scratch/source"
    git ls-files --cached --others --exclude-standard -z > "$scratch/files"
    tar --null -T "$scratch/files" -cf "$scratch/source.tar"
    tar -xf "$scratch/source.tar" -C "$scratch/source"
    test -s "$scratch/source/compose.yaml"
    docker run --rm --memory=512m --cpus=1 -v "$scratch/source:/scan:ro" "$gitleaks" dir --no-banner --redact /scan
    docker run --rm --memory=512m --cpus=1 -v "$root:/scan:ro" "$gitleaks" git --no-banner --redact /scan
    docker run --rm --memory=768m --cpus=1 -v candidate-screening-trivy:/root/.cache/trivy \
        -v "$scratch/source:/scan:ro" "$trivy" fs --quiet --exit-code 1 --severity HIGH,CRITICAL \
        --scanners vuln,misconfig,secret /scan
fi
if [ "$mode" != source ]; then
    failed=0
    { docker compose config --images; printf '%s\n' candidate-screening-kafka/viewer-auth:local; } | sort -u > "$scratch/images"
    while IFS= read -r image; do
        printf 'Scanning image: %s\n' "$image"
        docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
        docker image save --output "$scratch/image.tar" "$image"
        docker run --rm --memory=768m --cpus=1 -v candidate-screening-trivy:/root/.cache/trivy \
            -v "$scratch:/scan:ro" "$trivy" image --quiet --exit-code 1 --severity HIGH,CRITICAL \
            --input /scan/image.tar || failed=1
        rm "$scratch/image.tar"
    done < "$scratch/images"
    [ "$failed" -eq 0 ] || exit 1
fi
