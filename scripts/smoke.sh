#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$root"
for endpoint in http://127.0.0.1:8095/health/ready http://127.0.0.1:8096/api/auths http://127.0.0.1:8097/misc/ping http://127.0.0.1:8098/healthz http://127.0.0.1:8099/health; do
    curl --fail --silent --output /dev/null --max-time 5 "$endpoint"
done
curl --fail --silent --max-time 5 http://127.0.0.1:8096/api/auths | \
    python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("loginEnabled") is True and d.get("formEnabled") is True, "Viewer authentication is not enabled"'
docker compose exec -T postgres psql --no-psqlrc -U lab_admin -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF has_database_privilege('candidates_app', 'screening', 'CONNECT')
     OR has_database_privilege('screening_app', 'candidates', 'CONNECT')
     OR has_database_privilege('candidates_viewer', 'screening', 'CONNECT')
     OR has_database_privilege('screening_viewer', 'candidates', 'CONNECT')
     OR has_database_privilege('candidates_viewer', 'candidates', 'CREATE')
     OR has_database_privilege('screening_viewer', 'screening', 'CREATE')
     OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN
       ('candidates_app', 'screening_app', 'candidates_viewer', 'screening_viewer')
       AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
  THEN RAISE EXCEPTION 'Service database isolation failed';
  END IF;
END $$;
SQL
docker compose exec -T postgres psql --no-psqlrc -U lab_admin -d candidates -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
SET ROLE candidates_app;
CREATE TABLE public.foundation_probe (id integer NOT NULL);
INSERT INTO public.foundation_probe VALUES (1);
SET ROLE candidates_viewer;
DO $$ BEGIN
  IF (SELECT COUNT(*) FROM public.foundation_probe) <> 1 THEN
    RAISE EXCEPTION 'Viewer could not read its service data';
  END IF;
  BEGIN
    INSERT INTO public.foundation_probe VALUES (2);
    RAISE EXCEPTION 'Viewer unexpectedly wrote data';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
ROLLBACK;
SQL
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
for topic in candidate-submitted screening-completed candidate-changed screening-failed; do
    ./scripts/kafka-cli.sh kafka-topics \
        --bootstrap-server kafka:9092 --describe --topic "$topic" > "$scratch/topic"
    grep -q 'PartitionCount: 32' "$scratch/topic"
    grep -q 'retention.ms=86400000' "$scratch/topic"
    grep -q 'retention.bytes=8388608' "$scratch/topic"
done
docker compose exec -T ollama ollama list | grep -q qwen2.5:1.5b-instruct
echo 'Foundation connectivity, role isolation and topic configuration checks passed'
