# Run and inspect the local services

Install Docker with Compose, Git, OpenSSL, curl and Python 3. From the repository root:

```sh
./scripts/start.sh
```

Startup creates ignored, owner-only local credentials, builds the application and viewer images, and starts their dependencies. The initial model download needs internet access; the running model server is isolated from external networks. Existing local applications and data volumes are preserved.

Open the workbench, enter a synthetic profile and requirements, and submit for screening. The processing view can be left immediately; saved notices link to completed results. Browser identity is a protected synthetic session, not a production account. Resetting it removes access to its previous records. Text, results and notices expire after seven days; delete a submission sooner through its processing or result view.

Run the standalone integration checks against this local synthetic environment. Recovery checks temporarily interrupt application services and restore them afterward.

| Service | Open locally | Access and inspection |
| --- | --- | --- |
| React workbench | [Application](http://127.0.0.1:8094) | Submission, processing, results and saved inbox notices. |
| Candidates API | [Readiness](http://127.0.0.1:8095/health/ready), [liveness](http://127.0.0.1:8095/health/live) | Readiness reflects database and broker connectivity. |
| Kafka / AKHQ | [Kafka viewer](http://127.0.0.1:8096/ui/) | User `lab`; password in `.secrets/kafbat.password` (retained filename for existing installations). Inspect the local cluster and retained topics. Viewer is read only. |
| PostgreSQL / pgAdmin | [Database viewer](http://127.0.0.1:8097) | User `lab@example.com`; password in `.secrets/admin.password`. Separate read-only database connections are preconfigured. |
| OpenObserve | [Searchable logs](http://127.0.0.1:8098) | User `lab@example.invalid`; password in `.secrets/openobserve.password`. Open Logs and select the `foundation` stream; filter `service_name` or the startup event. |
| Ollama / Open WebUI | [Model viewer](http://127.0.0.1:8099) | User `lab@example.com`; password in `.secrets/openwebui.password`. Signup is disabled. The local model appears in model inspection. |

Credential files are intended for local use; open them directly when entering a viewer password. Never paste their values into issues, documentation or command arguments. Open WebUI uses its supported administrator bootstrap and keeps authentication state in its persistent volume. External integrations and code execution are disabled by default.

The worker has the same health routes on its internal service endpoint. Inspect its container health using `docker compose ps`; it has no published port. Business database and broker ports are also private. Kafka uses an isolated local network; production transport authentication and availability require a separately reviewed deployment.

```sh
# Restore, formatting, build, tests and dependency auditing
./scripts/check.sh
# Secret, source/configuration and image scans
./scripts/scan.sh
# Check running service connections and isolation
./scripts/smoke.sh
# Isolated initializer and transactional schema-upgrade checks (requires the running stack)
python3 tests/integration/initializer.py
# Synthetic submission, result and selected recovery paths
python3 tests/integration/flow.py
# Opt-in recovery faults; requires an idle queue and an owner-only output directory
RECOVERY_DIR="$(mktemp -d)"
python3 tests/integration/recovery.py --output "$RECOVERY_DIR/recovery.json"
# Inspect service health and safe application logs
docker compose ps
docker compose logs candidates screening otel-collector
# Stop without deleting data
docker compose stop
# Restart the same services and retained data
./scripts/start.sh
```

Ordinary startup never deletes volumes or rotates existing credentials. PostgreSQL initialization runs only for a fresh database volume; changing an existing role's credentials needs an explicit database rotation. Avoid `down --volumes` unless deliberately discarding all local data.

Deployment initialization waits briefly for newly created Kafka topics to become visible. Incompatible topic topology or settings still stop initialization.

Application schema changes run transactionally under a migration lock. Unknown versions, altered migration checksums and unexpected schema shapes stop startup. Do not clear the migration ledger or reset data to bypass that refusal; investigate the mismatch and preserve a recoverable copy first.

The expiry deadline applies to active data. Encrypted backups age out under their own retention. Keep a restored system unavailable until expiry and deletion reconciliation passes. A historical snapshot cannot reveal later owner deletions; restore procedures need consistent later deletion state or verified reconciliation before opening traffic.

Ollama uses the pinned local model and verifies its manifest before serving. Model availability is separate from successful inference; the health endpoint and model listing do not establish output quality. Open WebUI is an operational viewer, not the application interface. Its upstream branding must remain intact under the [Open WebUI licence](https://github.com/open-webui/open-webui/blob/v0.12.3/LICENSE).

Structured application logs reach OpenObserve through the collector's authenticated OTLP exporter. Telemetry buffering and retention are bounded; logs can be dropped during an extended telemetry outage. Business events belong in Kafka and durable application storage, never in the logging pipeline.

Container stdout and stderr use Docker's [rotating local logging driver](https://docs.docker.com/engine/logging/drivers/local/). Read them through `docker compose logs`; do not read or modify the daemon's storage files directly.
