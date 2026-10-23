#!/bin/sh
set -eu
# Fail before starting if authentication material is missing or incompatible.
# Do not print it, accept it in argv, or interpolate it into generated files.
fail() { printf '%s\n' 'Viewer configuration rejected.' >&2; exit 1; }
[ -n "${AKHQ_JWT_SECRET:-}" ] || fail
[ -n "${AKHQ_VIEWER_PASSWORD_HASH:-}" ] || fail
[ "${#AKHQ_JWT_SECRET}" -ge 64 ] 2>/dev/null || fail
[ "${#AKHQ_VIEWER_PASSWORD_HASH}" -eq 60 ] 2>/dev/null || fail
case "$AKHQ_VIEWER_PASSWORD_HASH" in
  '$2a$12$'*) ;;
  *) fail ;;
esac
exec /__cacert_entrypoint.sh java \
  -Xms32m -Xmx160m -XX:MaxMetaspaceSize=128m \
  -Djava.io.tmpdir=/tmp -Dlogback.configurationFile=/app/logback.xml \
  -jar /app/akhq.jar
