#!/bin/sh
set -eu
umask 077
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
mkdir "$scratch/scripts"
cp "$root/scripts/setup.sh" "$scratch/scripts/setup.sh"
sh "$scratch/scripts/setup.sh" >/dev/null
cp "$scratch/.secrets/candidates.password" "$scratch/expected"
cp "$scratch/.secrets/internal.password" "$scratch/expected-internal"
cp "$scratch/.secrets/kafbat.password" "$scratch/expected-viewer"
cp "$scratch/.secrets/akhq-jwt.password" "$scratch/expected-jwt"
for service in candidates screening; do
    sed -n 's/^Lab__InternalSecret=//p' "$scratch/.secrets/$service.env" > "$scratch/internal-$service"
    cmp "$scratch/expected-internal" "$scratch/internal-$service"
done
# Server mode resolves passfiles inside the user's storage; absolute mount paths fail.
if grep -Eq '"PassFile":"/' "$scratch/.secrets/servers.json"; then
    echo 'Viewer passfiles must use server-mode storage paths' >&2; exit 1
fi
grep -Fq '"PassFile":".pgpass"' "$scratch/.secrets/servers.json"
sh "$scratch/scripts/setup.sh" >/dev/null
cmp "$scratch/expected" "$scratch/.secrets/candidates.password"
cmp "$scratch/expected-internal" "$scratch/.secrets/internal.password"
cmp "$scratch/expected-viewer" "$scratch/.secrets/kafbat.password"
cmp "$scratch/expected-jwt" "$scratch/.secrets/akhq-jwt.password"
python3 - "$root" "$scratch" <<'PY'
import json
from pathlib import Path
import stat
import subprocess
import sys
import uuid

root, scratch = map(Path, sys.argv[1:])
config = subprocess.run(
    ['docker', 'compose', '--project-directory', str(scratch), '-f', str(root / 'compose.yaml'),
     'config', '--no-env-resolution', '--format', 'json'], check=True, capture_output=True, text=True)
viewer = json.loads(config.stdout)['services']['akhq']
# Inspect actual container configuration: Compose's rendered JSON escapes dollars.
name = 'lab-setup-' + uuid.uuid4().hex
image = 'candidate-screening-kafka/viewer-auth:local'
assert not subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'], text=True).strip(), 'Fixture name is already used'
image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
fixture = scratch / 'compose-auth-check.json'
fixture.write_text(json.dumps({'services': {'auth-env': {
    'image': image, 'container_name': name, 'env_file': viewer['env_file'],
    'environment': viewer.get('environment', {}), 'labels': {'lab.setup.fixture': name},
    'network_mode': 'none', 'read_only': True, 'mem_limit': '128m', 'pids_limit': 64,
    'cpus': 0.5, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
}}}))
try:
    subprocess.run(['docker', 'compose', '-p', name, '-f', str(fixture), 'create', '--no-build', '--pull', 'never'], check=True, capture_output=True)
    captured = subprocess.check_output(['docker', 'inspect', name], text=True)
    container = json.loads(captured)[0]
    assert container['Image'] == image_id and container['State']['Status'] == 'created', 'Fixture identity or state changed'
    environment = dict(item.split('=', 1) for item in container['Config']['Env'])
    assert environment['AKHQ_VIEWER_PASSWORD_HASH'] == (scratch / '.secrets/akhq.hash').read_text().strip(), 'Compose changed the password hash'
    assert environment['AKHQ_JWT_SECRET'] == (scratch / 'expected-jwt').read_text().strip(), 'Compose changed the signing key'
finally:
    captured = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
    if captured.returncode == 0:
        container = json.loads(captured.stdout)[0]
        assert container['Image'] == image_id and container['Config']['Labels'].get('lab.setup.fixture') == name, 'Fixture cleanup identity changed'
        subprocess.run(['docker', 'rm', name], check=True, capture_output=True)
    else:
        assert not subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'], text=True).strip(), 'Fixture cleanup could not verify absence'
assert stat.S_IMODE((scratch / '.secrets').stat().st_mode) == 0o700, 'Secret directory permissions'
assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (scratch / '.secrets').iterdir()), 'Secret file permissions'
PY
rm "$scratch/.secrets/akhq.hash"
ln -s "$scratch/expected-viewer" "$scratch/.secrets/akhq.hash"
if sh "$scratch/scripts/setup.sh" >/dev/null 2>&1; then
    echo 'Setup accepted a generated-file symlink' >&2; exit 1
fi
cmp "$scratch/expected-viewer" "$scratch/.secrets/kafbat.password"
rm "$scratch/.secrets/akhq.hash"
printf 'invalid\n' > "$scratch/.secrets/candidates.password"
if sh "$scratch/scripts/setup.sh" >/dev/null 2>&1; then
    echo 'Setup accepted malformed credentials' >&2; exit 1
fi
rm "$scratch/.secrets/candidates.password"
ln -s "$scratch/expected" "$scratch/.secrets/candidates.password"
if sh "$scratch/scripts/setup.sh" >/dev/null 2>&1; then
    echo 'Setup accepted a secret symlink' >&2; exit 1
fi
echo 'Setup boundary checks passed'
