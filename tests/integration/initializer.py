#!/usr/bin/env python3
"""Opt-in isolated bootstrap/schema checks; never attach existing data volumes."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import uuid

SDK_IMAGE = "mcr.microsoft.com/dotnet/sdk:10.0.402@sha256:0d99ebf1ccad15037b5c98c9fe8979d6b8dbac23812feae2277a0b277b5c5ffa"

SAFE_LOGGING = {
    "log_statement": "none", "log_min_error_statement": "panic", "log_error_verbosity": "terse",
    "log_parameter_max_length": "0", "log_parameter_max_length_on_error": "0", "log_duration": "off",
    "log_min_duration_statement": "-1", "log_min_duration_sample": "-1",
    "log_statement_sample_rate": "0", "log_transaction_sample_rate": "0",
}


def operation(*args, data=None, expected=0, timeout=300):
    result = subprocess.run(["docker", *args], input=data, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode != expected:
        for line in result.stderr.splitlines():
            if line.startswith("Deployment initialization failed: stage=") and len(line) < 160:
                print(line, flush=True)
        raise AssertionError("Isolated initializer fixture Docker operation failed")
    safe_diagnostics = [line for line in result.stderr.splitlines()
                        if line.startswith("Deployment initialization failed: stage=") and len(line) < 160]
    return (result.stdout + "\n" + "\n".join(safe_diagnostics)).strip()


def envfile(directory, name, values):
    path = directory / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(key + "=" + value + "\n" for key, value in values.items()))
    return str(path)


def run():
    suffix = uuid.uuid4().hex
    database = "lab-init-db-" + suffix
    initializer = "candidate-screening-bootstrap-" + suffix
    network = "candidate-screening-kafka_runtime"
    source_database = operation("compose", "ps", "-q", "postgres")
    assert source_database and "\n" not in source_database
    postgres_image = operation("inspect", "--format", "{{.Image}}", source_database)
    source_api = operation("compose", "ps", "-q", "candidates")
    assert source_api and "\n" not in source_api
    initializer_image = operation("inspect", "--format", "{{.Image}}", source_api)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", initializer_image)
    assert operation("inspect", "--format", "{{.Config.User}}", initializer_image) in ("1654", "1654:1654")
    assert json.loads(operation("inspect", "--format", "{{json .Config.Entrypoint}}", initializer_image)) == ["dotnet", "service.dll"]
    assembly = Path("services/Candidates.Api/bin/Release/net10.0/Candidates.Api.dll")
    assembly_hash = hashlib.sha256(assembly.read_bytes()).hexdigest()
    admin_password = secrets.token_urlsafe(36)
    master_password = secrets.token_urlsafe(36)
    passwords = {name: secrets.token_hex(32) for name in (
        "CandidatesPassword", "ScreeningPassword", "CandidatesViewerPassword", "ScreeningViewerPassword")}
    with tempfile.TemporaryDirectory(prefix="candidate-screening-initializer-") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        postgres_env = envfile(directory, "postgres.env", {
            "POSTGRES_USER": "fixture_super", "POSTGRES_PASSWORD": admin_password,
            "PGDATA": "/var/lib/postgresql/fixture"})
        values = {"Lab__Deployment": "Local", "Lab__ServiceName": "candidates",
                  "Lab__DatabaseHost": database, "Lab__KafkaBootstrapServers": "kafka:9092",
                  "Lab__BootstrapDatabaseUsername": "fixture_master",
                  "Lab__BootstrapDatabasePassword": master_password}
        values.update({"Lab__" + name: value for name, value in passwords.items()})
        bootstrap_env = envfile(directory, "bootstrap.env", values)
        credential_files = {}
        for role, field in (("candidates_app", "CandidatesPassword"), ("screening_app", "ScreeningPassword"),
                            ("candidates_viewer", "CandidatesViewerPassword"), ("screening_viewer", "ScreeningViewerPassword")):
            credential_files[role] = envfile(directory, role + ".env", {"PGPASSWORD": passwords[field]})

        def sql(query, role="fixture_super", db="postgres", expected=0):
            args = ["exec", "-i"]
            if role in credential_files:
                args += ["--env-file", credential_files[role]]
            args += [database, "psql", "--no-psqlrc", "-U", role, "-d", db, "-At", "-v", "ON_ERROR_STOP=1"]
            if role != "fixture_super":
                args += ["-h", "127.0.0.1"]
            return operation(*args, data=query, expected=expected)

        def bootstrap(expected=0, settings=bootstrap_env, failed_stage=None):
            try:
                output = operation("run", "--name", initializer, "--network", network,
                                   "--memory", "128m", "--cpus", "0.5", "--read-only", "--user", "1654:1654",
                                   "--tmpfs", "/tmp:uid=1654,gid=1654,mode=0700",
                                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                                   "--env-file", settings, initializer_image, "--initialize-deployment", expected=expected)
                if expected == 0:
                    assert "Deployment initialization verified." in output
                else:
                    assert "initialization verified" not in output
                    if not (failed_stage and "stage=" + failed_stage + "," in output):
                        for line in output.splitlines():
                            if line.startswith("Deployment initialization failed: stage=") and len(line) < 160:
                                print(line, flush=True)
                        raise AssertionError("Expected isolated initializer failure stage was not observed")
            finally:
                operation("rm", "-f", initializer)

        try:
            logging = [argument for name, value in SAFE_LOGGING.items() for argument in ("-c", name + "=" + value)]
            operation("run", "-d", "--name", database, "--network", network, "--memory", "256m", "--cpus", "0.5",
                      "--tmpfs", "/var/lib/postgresql:rw,size=134217728", "--env-file", postgres_env, postgres_image,
                      "postgres", "-c", "shared_buffers=16MB", "-c", "max_connections=16", *logging)
            until = time.monotonic() + 45
            while time.monotonic() < until:
                ready = subprocess.run(["docker", "exec", database, "pg_isready", "-h", "127.0.0.1", "-U", "fixture_super"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise AssertionError("Isolated fixture PostgreSQL readiness deadline exceeded")
            # SQL travels only over stdin. Explicit safe server logging is already active.
            sql("CREATE ROLE fixture_master LOGIN NOSUPERUSER CREATEDB CREATEROLE PASSWORD '" + master_password + "';")
            assert sql("SELECT NOT rolsuper AND rolcreatedb AND rolcreaterole FROM pg_roles WHERE rolname='fixture_master'") == "t"
            # Fail before role creation on unsafe logging, then restore only this isolated server setting.
            sql("ALTER ROLE fixture_master SET log_min_duration_statement=0")
            time.sleep(1)
            bootstrap(expected=1, failed_stage="logging_guard")
            assert sql("SELECT count(*) FROM pg_roles WHERE rolname IN ('candidates_app','screening_app','candidates_viewer','screening_viewer')") == "0"
            sql("ALTER ROLE fixture_master RESET log_min_duration_statement")
            time.sleep(1)
            bootstrap()
            for service in ("candidates", "screening"):
                app, viewer = service + "_app", service + "_viewer"
                assert sql("SELECT count(*) FROM schema_migrations", app, service) == "2"
                assert sql("SELECT count(*) FROM schema_migrations", viewer, service) == "2"
                service_directory = "Candidates.Api" if service == "candidates" else "Screening.Worker"
                baseline = (Path("services/Common/Schema.Baseline.sql").read_bytes()
                            + Path("services").joinpath(service_directory, "Schema.sql").read_bytes())
                expected_ledger = "1:" + hashlib.sha256(baseline).hexdigest() + "\n2:" + hashlib.sha256(
                    Path("services/Common/Schema.002.sql").read_bytes()).hexdigest()
                assert sql("SELECT version::text||':'||checksum FROM schema_migrations ORDER BY version", app, service) == expected_ledger
                assert sql("SELECT to_regclass('flow_handler_failures') IS NOT NULL", app, service) == "t"
                sql("CREATE TABLE viewer_write_probe(id integer)", viewer, service, expected=3)
                sql("DELETE FROM schema_migrations", viewer, service, expected=3)
                sql("SELECT 1", app, "screening" if service == "candidates" else "candidates", expected=2)
            assert sql("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid "
                       "JOIN pg_roles u ON u.oid=m.member WHERE u.rolname='fixture_master' "
                       "AND r.rolname IN ('candidates_app','screening_app') AND (m.set_option OR m.inherit_option)") == "0"
            owner, candidate, key = (str(uuid.uuid4()) for _ in range(3))
            sql("INSERT INTO owner_sessions(id,expires_at) VALUES('" + owner + "',now()+interval '7 days');"
                "INSERT INTO candidates(id,owner_id,request_key,payload_hash,status,profile,expires_at) VALUES('" + candidate + "','" + owner
                + "','" + key + "',decode(repeat('00',32),'hex'),'Queued','Synthetic bootstrap preservation',now()+interval '7 days');",
                "candidates_app", "candidates")
            bootstrap()
            assert sql("SELECT profile FROM candidates WHERE id='" + candidate + "'", "candidates_app", "candidates") == "Synthetic bootstrap preservation"
            changed = values.copy()
            changed["Lab__CandidatesPassword"] = secrets.token_hex(32)
            bootstrap(expected=1, settings=envfile(directory, "wrong-existing-password.env", changed), failed_stage="database_candidates")
            assert sql("SELECT count(*) FROM candidates WHERE id='" + candidate + "'", "candidates_app", "candidates") == "1"
            # The native database test uses its own empty database and the real migration transaction.
            sql("CREATE DATABASE schema_fixture OWNER fixture_master")
            schema_connection = ("Host=" + database + ";Database=schema_fixture;Username=fixture_master;Password="
                                 + master_password + ";Include Error Detail=false;Log Parameters=false")
            schema_env = envfile(directory, "schema.env", {"LAB_SCHEMA_TEST_CONNECTION": schema_connection,
                                                           "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"})
            output = operation("run", "--rm", "--network", network, "--memory", "1g", "--cpus", "2", "--env-file", schema_env,
                               "-v", str(Path.cwd()) + ":/src", "-w", "/src", SDK_IMAGE, "dotnet", "test", "CandidateScreening.slnx",
                               "-c", "Release", "--no-build", "--filter", "Category=Database", "--logger", "console;verbosity=minimal")
            assert "Passed:" in output and "Total:" in output
            print(json.dumps({"initializer": "passed", "managed_admin_emulation": "passed", "unsafe_logging_gate": "passed",
                              "separate_logins_and_read_only_viewers": "passed", "reapply_preserves_data": "passed",
                              "existing_credentials_not_rotated": "passed", "native_schema_upgrade_and_rollback": "passed",
                              "native_legacy_schema_adoption_and_rollback": "passed",
                              "postgres_image": postgres_image, "initializer_image": initializer_image,
                              "initializer_assembly_sha256": assembly_hash,
                              "initializer_assembly_sha256_scope": "Local build artifact; runtime image tracked separately",
                              "sdk_image": SDK_IMAGE}), flush=True)
        finally:
            operation("rm", "-f", database)
    print("Isolated initializer containers removed; original volumes and services untouched", flush=True)


if __name__ == "__main__":
    run()
