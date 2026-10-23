#!/bin/sh
set -eu
case "${1:-}" in help|--help|-h)
    echo 'Usage: scripts/check.sh (.NET and frontend restore, format, static checks, builds, tests and dependency audits)'; exit 0 ;;
    '') ;; *) echo 'Unexpected argument' >&2; exit 2 ;;
esac
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$root"
docker build --target auth-tool -t candidate-screening-kafka/viewer-auth:local -f docker/akhq/Dockerfile .
./scripts/test-setup.sh
./scripts/setup.sh
python3 -m unittest discover -s tests/integration -p 'test_*.py'
docker compose config --quiet
docker run --rm --memory=1g --cpus=2 \
    -e DOTNET_CLI_TELEMETRY_OPTOUT=1 -e DOTNET_NOLOGO=1 \
    -v "$root:/src" -w /src \
    mcr.microsoft.com/dotnet/sdk:10.0.402@sha256:0d99ebf1ccad15037b5c98c9fe8979d6b8dbac23812feae2277a0b277b5c5ffa \
    sh -ec 'dotnet restore CandidateScreening.slnx --locked-mode
dotnet format CandidateScreening.slnx --verify-no-changes --no-restore
dotnet build CandidateScreening.slnx -c Release --no-restore -warnaserror
dotnet test CandidateScreening.slnx -c Release --no-build
dotnet list CandidateScreening.slnx package --vulnerable --include-transitive'
docker compose build frontend
