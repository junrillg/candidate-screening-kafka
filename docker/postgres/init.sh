#!/bin/sh
set -eu
# Passwords are generated hex; never place them in process arguments or log statements.
for name in CANDIDATES_PASSWORD SCREENING_PASSWORD CANDIDATES_VIEW_PASSWORD SCREENING_VIEW_PASSWORD; do
    value=$(printenv "$name")
    case "$value" in ''|*[!0-9a-f]*) echo 'Invalid database credential format' >&2; exit 1 ;; esac
    [ "${#value}" -eq 64 ] || exit 1
done
psql --no-psqlrc --username "$POSTGRES_USER" --dbname postgres -v ON_ERROR_STOP=1 <<'SQL'
\getenv candidates_password CANDIDATES_PASSWORD
\getenv screening_password SCREENING_PASSWORD
\getenv candidates_view_password CANDIDATES_VIEW_PASSWORD
\getenv screening_view_password SCREENING_VIEW_PASSWORD
CREATE ROLE candidates_app LOGIN CONNECTION LIMIT 12 PASSWORD :'candidates_password';
CREATE ROLE screening_app LOGIN CONNECTION LIMIT 12 PASSWORD :'screening_password';
CREATE ROLE candidates_viewer LOGIN CONNECTION LIMIT 3 PASSWORD :'candidates_view_password';
CREATE ROLE screening_viewer LOGIN CONNECTION LIMIT 3 PASSWORD :'screening_view_password';
CREATE DATABASE candidates OWNER candidates_app;
CREATE DATABASE screening OWNER screening_app;
REVOKE ALL ON DATABASE candidates FROM PUBLIC;
REVOKE ALL ON DATABASE screening FROM PUBLIC;
GRANT CONNECT ON DATABASE candidates TO candidates_app, candidates_viewer;
GRANT CONNECT ON DATABASE screening TO screening_app, screening_viewer;
\connect candidates
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO candidates_viewer;
ALTER DEFAULT PRIVILEGES FOR ROLE candidates_app IN SCHEMA public GRANT SELECT ON TABLES TO candidates_viewer;
\connect screening
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO screening_viewer;
ALTER DEFAULT PRIVILEGES FOR ROLE screening_app IN SCHEMA public GRANT SELECT ON TABLES TO screening_viewer;
SQL
