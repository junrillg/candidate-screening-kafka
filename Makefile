.DEFAULT_GOAL := help
.NOTPARALLEL:

.PHONY: help up down restart logs status sample build test check check-source check-images smoke

help:
	@printf '%s\n' \
	  'Usage: make <target> (run from the repository root)' \
	  '  help          Show this help without Docker or configuration reads (default)' \
	  '  up            Build and start the complete local stack; wait for readiness' \
	  '  down          Remove lab containers/networks; retain volumes and credentials' \
	  '  restart       Stop, rebuild and start the stack with retained data; wait for readiness' \
	  '  logs          Follow safe application/collector container logs' \
	  '  status        Show Compose service status' \
	  '  sample        Run the synthetic screening flow, verify its result, then delete its records' \
	  '  build         Build all local images sequentially; prepare ignored configuration' \
	  '  test          Run existing source checks, builds, tests and dependency audits' \
	  '  check         Run source checks/security, image builds and image security scans' \
	  '  check-source  Run source checks/tests and source/configuration security scans' \
	  '  check-images  Scan the built images; build first' \
	  '  smoke         Check running service connectivity and isolation' \
	  'Prerequisites: GNU Make 3.81+, POSIX shell, Docker Compose (Linux ARM64), Git,' \
	  'OpenSSL, curl and Python 3. Builds/checks require network access. No custom variables.' \
	  'Terraform mock plans and pinned CodeQL analysis remain separate CI checks.' \
	  'See docs/operations.md for viewers, credentials and opt-in recovery checks.'

up:
	./scripts/start.sh

down:
	docker compose down --timeout 30

restart:
	docker compose stop --timeout 30
	./scripts/start.sh

logs:
	docker compose logs --follow --tail 100 candidates screening otel-collector

status:
	docker compose ps

sample:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests/integration python3 -c 'from flow import run; print("Verified synthetic submission (deleted after checks):", run())'

build:
	docker build --target auth-tool -t candidate-screening-kafka/viewer-auth:local -f docker/akhq/Dockerfile .
	./scripts/setup.sh
	docker compose --parallel 1 build

test:
	./scripts/check.sh

check-source: test
	./scripts/scan.sh source

check-images:
	./scripts/scan.sh images

check: check-source build check-images

smoke:
	./scripts/smoke.sh
