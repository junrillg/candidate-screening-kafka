#!/bin/sh
set -eu
umask 077
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$root"
docker image inspect candidate-screening-kafka/viewer-auth:local >/dev/null 2>&1 || {
    echo 'Build the viewer auth helper with scripts/start.sh or scripts/check.sh first.' >&2; exit 1
}
[ ! -L .secrets ] || { echo '.secrets must not be a symlink' >&2; exit 1; }
mkdir -p .secrets
chmod 700 .secrets
for name in postgres candidates screening candidates-view screening-view admin openobserve kafbat akhq-jwt openwebui internal; do
    file=".secrets/$name.password"
    [ ! -L "$file" ] || { echo 'Secret symlinks are not supported' >&2; exit 1; }
    if [ ! -f "$file" ]; then
        if [ "$name" = openobserve ]; then
            random=$(openssl rand -hex 32)
            printf '%sAa1!\n' "$random" > "$file"
        else
            openssl rand -hex 32 > "$file"
        fi
    fi
    value=$(cat "$file")
    if [ "$name" = openobserve ]; then
        case "$value" in *Aa1!) value=${value%Aa1!} ;; *) echo 'Invalid log-viewer credential format' >&2; exit 1 ;; esac
    fi
    case "$value" in ''|*[!0-9a-f]*) echo 'Invalid local credential format' >&2; exit 1 ;; esac
    [ "${#value}" -eq 64 ] || { echo 'Invalid local credential length' >&2; exit 1; }
    chmod 600 "$file"
done
postgres=$(cat .secrets/postgres.password)
candidates=$(cat .secrets/candidates.password)
screening=$(cat .secrets/screening.password)
candidates_view=$(cat .secrets/candidates-view.password)
screening_view=$(cat .secrets/screening-view.password)
admin=$(cat .secrets/admin.password)
openobserve=$(cat .secrets/openobserve.password)
openwebui=$(cat .secrets/openwebui.password)
internal=$(cat .secrets/internal.password)
for target in postgres.env candidates.env screening.env admin.env observability.env akhq.hash akhq.env openwebui.env pgpass servers.json; do
    [ ! -L ".secrets/$target" ] || { echo 'Generated configuration symlinks are not supported' >&2; exit 1; }
    [ ! -e ".secrets/$target" ] || chmod 600 ".secrets/$target"
done
# Keep the existing viewer password; BCrypt receives it only on standard input.
docker run --rm -i --network none --read-only --memory=128m --memory-swap=128m \
    --cpus=0.5 --pids-limit=64 --cap-drop ALL --security-opt no-new-privileges:true \
    candidate-screening-kafka/viewer-auth:local < .secrets/kafbat.password > .secrets/akhq.hash
LC_ALL=C grep -Eq '^\$2a\$12\$[./A-Za-z0-9]{53}$' .secrets/akhq.hash || {
    echo 'Viewer password hashing failed.' >&2; exit 1
}
cat > .secrets/postgres.env <<EOF
POSTGRES_PASSWORD=$postgres
CANDIDATES_PASSWORD=$candidates
SCREENING_PASSWORD=$screening
CANDIDATES_VIEW_PASSWORD=$candidates_view
SCREENING_VIEW_PASSWORD=$screening_view
EOF
for service in candidates screening; do
    if [ "$service" = candidates ]; then password=$candidates; else password=$screening; fi
    printf 'Lab__DatabaseConnection=Host=postgres;Database=%s;Username=%s_app;Password=%s;Maximum Pool Size=8;Timeout=3;Command Timeout=3;Include Error Detail=false;GSS Encryption Mode=Disable\n' "$service" "$service" "$password" > ".secrets/$service.env"
    printf 'Lab__InternalSecret=%s\n' "$internal" >> ".secrets/$service.env"
done
printf 'PGADMIN_DEFAULT_PASSWORD=%s\n' "$admin" > .secrets/admin.env
printf 'ZO_ROOT_USER_PASSWORD=%s\nOPENOBSERVE_PASSWORD=%s\n' "$openobserve" "$openobserve" > .secrets/observability.env
# Compose env files interpolate dollar signs unless the value is single-quoted.
printf "AKHQ_VIEWER_PASSWORD_HASH='" > .secrets/akhq.env
tr -d '\n' < .secrets/akhq.hash >> .secrets/akhq.env
printf "'\nAKHQ_JWT_SECRET=%s\n" "$(cat .secrets/akhq-jwt.password)" >> .secrets/akhq.env
printf 'WEBUI_ADMIN_PASSWORD=%s\n' "$openwebui" > .secrets/openwebui.env
printf 'postgres:5432:candidates:candidates_viewer:%s\npostgres:5432:screening:screening_viewer:%s\n' "$candidates_view" "$screening_view" > .secrets/pgpass
cat > .secrets/servers.json <<'EOF'
{"Servers":{"1":{"Name":"Candidates (read only)","Group":"Lab","Host":"postgres","Port":5432,"MaintenanceDB":"candidates","Username":"candidates_viewer","SSLMode":"prefer","PassFile":".pgpass"},"2":{"Name":"Screening (read only)","Group":"Lab","Host":"postgres","Port":5432,"MaintenanceDB":"screening","Username":"screening_viewer","SSLMode":"prefer","PassFile":".pgpass"}}}
EOF
for target in postgres.env candidates.env screening.env admin.env observability.env akhq.hash akhq.env openwebui.env pgpass servers.json; do
    chmod 600 ".secrets/$target"
done
echo 'Owner-only local configuration is ready; credential values were not printed.'
