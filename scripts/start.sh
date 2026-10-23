#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$root"
docker build --target auth-tool -t candidate-screening-kafka/viewer-auth:local -f docker/akhq/Dockerfile .
./scripts/setup.sh
docker compose build postgres
docker compose build kafka
docker compose build ollama
docker compose build candidates
docker compose build screening
docker compose build pgadmin
docker compose build frontend
docker compose build akhq
docker compose build openwebui
docker compose up --no-build --wait --wait-timeout 300
for endpoint in http://127.0.0.1:8094/health http://127.0.0.1:8095/health/ready http://127.0.0.1:8096/api/auths http://127.0.0.1:8097/misc/ping http://127.0.0.1:8098/healthz http://127.0.0.1:8099/health; do
    attempt=0
    until curl --fail --silent --output /dev/null --max-time 3 "$endpoint"; do
        attempt=$((attempt + 1))
        [ "$attempt" -lt 60 ] || { echo "Readiness failed: $endpoint" >&2; exit 1; }
        sleep 2
    done
done
curl --fail --silent --max-time 5 http://127.0.0.1:8096/api/auths | \
    python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("loginEnabled") is True and d.get("formEnabled") is True, "Viewer authentication is not enabled"'
echo 'Workbench is reachable at http://127.0.0.1:8094; see docs/operations.md for viewers.'
