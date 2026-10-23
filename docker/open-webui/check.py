#!/usr/bin/env python3
"""Focused source/lock/install/auth/import checks; no running viewer/data mount is used."""

import ast
import asyncio
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Union

USAGE = (
    'Purpose: check the pinned Open WebUI slim derivative at its build boundaries.\n'
    'Usage: check.py source SOURCE_ROOT | lock | config | tools | toolchain | native | '
    'wheel WHEEL_DIR\n'
    '       check.py binding SOURCE_ROOT | installed APP_ROOT | auth BACKEND_ROOT | '
    'imports BACKEND_ROOT\n'
    '       check.py help | --help | -h\n'
    'Modes: source checks original bytes and the exact four-pin input; lock refuses '
    'unresolved\n'
    'or unreviewed locks; installed checks the complete reviewed package inventory/assets;'
    '\n'
    'auth tests extracted unchanged auth functions against installed libraries; imports '
    'loads\n'
    'native main in a disposable synthetic SQLite directory. Runtime modes need a granted\n'
    'isolated build lane and imports MUST run with external networking disabled.\n'
    'config checks native build inputs; tools checks installer prefixes; toolchain records'
    ' APK inputs; binding builds/tests only RE2;\n'
    'wheel records a single-build artifact (not reproducibility); native inventories every'
    ' installed ELF.\n'
    'Paths are required except for lock, config, tools, toolchain and native. No default '
    'mode. No original volume or credentials.\n'
    'Examples: check.py help; check.py source /source; check.py lock; check.py auth '
    '/app/backend\n'
    'Reference: source-lock.json, native-build-lock.json, bootstrap.lock, '
    'build-tools.lock;\n'
    'complete requirements-musl.lock/dependency-musl-lock.json and reproduced native wheel'
    ' proof are required.\n'
)
if len(sys.argv) > 1 and sys.argv[1].casefold() in {'help', '--help', '-h'}:
    print(USAGE)
    raise SystemExit(0)
if (
    len(sys.argv) < 2
    or sys.argv[1]
    not in {
        'source',
        'lock',
        'config',
        'tools',
        'toolchain',
        'native',
        'wheel',
        'binding',
        'installed',
        'auth',
        'imports',
    }
    or len(sys.argv) != (2 if sys.argv[1] in {'lock', 'config', 'tools', 'toolchain', 'native'} else 3)
):
    print(USAGE)
    raise SystemExit(1)


HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / 'source-lock.json').read_text())


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def checked_lock():
    resolution = LOCK['resolution']
    require(
        resolution['status'] == 'resolved-reviewed-musl-arm64-python311',
        'Dependency resolution remains unverified',
    )
    lock = HERE / resolution['file']
    manifest = json.loads((HERE / resolution['manifest']).read_text())
    require(sha(HERE / resolution['manifest']) == resolution['manifestSha256'], 'Resolved manifest checksum mismatch')
    require(sha(lock) == resolution['sha256'] == manifest['requirementsSha256'], 'Resolved lock checksum mismatch')
    require(
        manifest['inputSha256'] == LOCK['requirementsInputSha256'] == sha(HERE / 'requirements-slim.in'),
        'Resolved input mismatch',
    )
    require(
        manifest['target']
        == {'os': 'linux', 'architecture': 'aarch64', 'python': '3.11', 'libc': 'musl'},
        'Wrong lock target',
    )
    require(
        manifest['resolver'] == 'pip'
        and manifest['resolverVersion'] == build_lock()['tools']['pip']['version'],
        'Resolver provenance mismatch',
    )
    config()
    native = LOCK['nativeWheel']
    require(re.fullmatch('[0-9a-f]{64}', native['sha256'] or ''), 'RE2 wheel hash required')
    require(
        re.fullmatch('[0-9a-f]{64}', native['recipeSha256'])
        and native['buildInputs'] == native_build_inputs()
        and native['nativeBuildLockSha256'] == LOCK['nativeBuildLockSha256']
        and native['sourceDateEpoch'] == build_lock()['source']['sourceDateEpoch'],
        'RE2 artifact provenance mismatch',
    )
    require(
        native['toolchainInventorySha256'] == build_lock()['reproducibility']['toolchainInventorySha256']
        and re.fullmatch('[0-9a-f]{64}', native['toolchainInventorySha256'] or ''),
        'RE2 toolchain remains unfrozen',
    )
    artifact = manifest['wheels']['google-re2']
    require(
        artifact['filename'] == native['filename'] and artifact['sha256'] == native['sha256'],
        'RE2 lock artifact mismatch',
    )
    text = lock.read_text()
    logical = text.replace('\\\n', ' ')
    entries = [line.strip() for line in logical.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    require(entries, 'Empty resolved lock')
    for line in entries:
        require(
            re.match('^[A-Za-z0-9_.-]+(?:\\[[A-Za-z0-9_,.-]+\\])?==[^\\s;]+', line),
            'Only exact package requirements allowed',
        )
        require(re.search(r'--hash=sha256:[0-9a-f]{64}(?:\s|$)', line), 'Every requirement needs an artifact hash')
        require('://' not in line, 'Unexpected direct URL or index override')
    packages = {canonical(name): version for name, version in manifest['packages'].items()}
    require(
        manifest['baseImage'] == LOCK['pythonBase']['image']
        and manifest['pythonVersion'] == '3.11.17'
        and manifest['upstreamCommit'] == LOCK['upstream']['commit'],
        'Dependency provenance target changed',
    )
    parsed = {}
    for line in entries:
        match = re.match(r'^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s;]+)', line)
        if match is None:
            raise RuntimeError('Invalid exact locked requirement')
        name, version = canonical(match[1]), match[2]
        require(name not in parsed, 'Duplicate locked package')
        artifact = manifest['wheels'][name]
        require(
            artifact['version'] == version and '--hash=sha256:' + artifact['sha256'] in line,
            'Wheel hash inventory mismatch',
        )
        filename = artifact['filename']
        tags = filename.removesuffix('.whl').rsplit('-', 1)[-1].split('.')
        require(
            name == 'google-re2'
            or filename.endswith('-none-any.whl')
            or all(re.fullmatch(r'musllinux_1_[12]_aarch64', tag) for tag in tags),
            'Non-musl wheel in native target',
        )
        parsed[name] = version
    require(parsed == packages, 'Requirement list differs from package inventory')
    require(packages and len(packages) == len(manifest['packages']), 'Invalid complete inventory')
    for name, version in LOCK['patchedDirectDependencies'].items():
        require(packages.get(canonical(name)) == version, 'Patched package missing from resolved graph')
    return packages


def source(root):
    for name, expected in LOCK['upstream']['files'].items():
        require(sha(root / name) == expected, 'Pinned upstream source changed: ' + name)
    require(sha(HERE / 'security-dependencies.patch') == LOCK['patchSha256'], 'Patch changed')
    original = (root / 'backend/requirements-slim.txt').read_text()
    patched = original
    for old, new in [
        ('cryptography==48.0.0', 'cryptography==50.0.2'),
        ('PyJWT[crypto]==2.13.0', 'PyJWT[crypto]==2.15.1'),
        ('mcp==1.27.2', 'mcp==1.28.1'),
        ('aiohttp==3.14.3', 'aiohttp==3.14.4'),
    ]:
        require(patched.count(old) == 1, 'Unexpected upstream dependency input')
        patched = patched.replace(old, new)
    require(patched == (HERE / 'requirements-slim.in').read_text(), 'Changes beyond reviewed four pins')
    require(sha(HERE / 'requirements-slim.in') == LOCK['requirementsInputSha256'], 'Input digest changed')
    require(json.loads((root / 'package.json').read_text())['version'] == LOCK['upstream']['version'], 'Wrong release')


def installed(root):
    config()
    tools()
    expected = checked_lock()
    actual = {canonical(d.metadata['Name']): d.version for d in importlib.metadata.distributions()}
    require(
        {n: v for n, v in actual.items() if n in expected or n not in {'pip', 'setuptools'}} == expected,
        'Installed graph differs from reviewed lock',
    )
    excluded = {
        'torch',
        'transformers',
        'sentence-transformers',
        'chromadb',
        'unstructured',
        'pyarrow',
        'pypdf',
        'fsspec',
    }
    require(not excluded.intersection(actual), 'Unexpected excluded slim runtime')
    require(
        json.loads((HERE / 'asset-package.json').read_text()) == json.loads((root / 'package.json').read_text()),
        'Built assets have a different release package',
    )
    for name in ['backend/start.sh', 'backend/open_webui/utils/auth.py']:
        require(sha(root / name) == LOCK['upstream']['files'][name], 'Application/auth bytes changed')
    require(
        sha(HERE / 'asset-auth.py') == LOCK['upstream']['files']['backend/open_webui/utils/auth.py'],
        'Existing release image auth differs from pinned source',
    )
    require(
        sha(HERE / 'asset-start.sh') == LOCK['upstream']['files']['backend/start.sh'],
        'Existing release image startup differs from pinned source',
    )
    require((root / 'build/index.html').is_file(), 'Frontend not present')
    checksums = {}
    for directory in ['build', 'backend/open_webui/static']:
        for path in sorted((root / directory).rglob('*')):
            require(not path.is_symlink(), 'Unexpected asset symlink')
            if path.is_file():
                checksums[str(path.relative_to(root))] = sha(path)
    require(checksums, 'No asset provenance')
    target = Path('/usr/share/open-webui-derivative')
    (target / 'assets-sha256.json').write_text(json.dumps(checksums, sort_keys=True, indent=2) + '\n')
    (target / 'python-packages.json').write_text(json.dumps(actual, sort_keys=True, indent=2) + '\n')


def build_lock():
    require(sha(HERE / 'native-build-lock.json') == LOCK['nativeBuildLockSha256'], 'Native build input changed')
    return json.loads((HERE / 'native-build-lock.json').read_text())


def native_build_inputs():
    # Bind compilation inputs separately from later runtime checks; preserve historical whole-recipe hashes.
    recipe = (HERE / 'Dockerfile').read_bytes().split(b'\nFROM base AS dependencies\n')
    require(len(recipe) == 2, 'Native recipe boundary changed')
    names = {'require', 'sha', 'canonical', 'build_lock', 'config', 'tools', 'apk_inventory', 'toolchain', 'binding'}
    non_build = {'checked_lock', 'source', 'installed', 'native', 'auth', 'imports', 'wheel', 'native_build_inputs'}
    nodes = [
        node
        for node in ast.parse((HERE / 'check.py').read_text()).body
        if not isinstance(node, ast.FunctionDef) or node.name not in non_build
    ]
    require(
        {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == names, 'Native build function missing'
    )
    return {
        'recipePrefixSha256': hashlib.sha256(recipe[0]).hexdigest(),
        'checkerBuildAstSha256': hashlib.sha256(
            ast.dump(ast.Module(body=nodes, type_ignores=[])).encode()
        ).hexdigest(),
        'nativeBuildLockSha256': sha(HERE / 'native-build-lock.json'),
        'bootstrapSha256': sha(HERE / 'bootstrap.lock'),
        'buildToolsSha256': sha(HERE / 'build-tools.lock'),
    }


def config():
    data = build_lock()
    require(
        sys.version_info[:3] == (3, 11, 17)
        and platform.machine() == 'aarch64'
        and Path('/lib/ld-musl-aarch64.so.1').is_file(),
        'Native musl Python target required',
    )
    require(
        sha(HERE / 'bootstrap.lock') == data['bootstrapSha256']
        and sha(HERE / 'build-tools.lock') == data['buildToolsSha256'],
        'Bootstrap lock changed',
    )


def tools():
    config()
    data = build_lock()
    for name in ('pip', 'setuptools'):
        require(importlib.metadata.version(name) == data['tools'][name]['version'], 'Installer version mismatch')
    distribution = importlib.metadata.distribution('setuptools')
    vendor = distribution.locate_file('setuptools/_vendor')
    if not isinstance(vendor, Path):
        raise RuntimeError('Installer vendor path is not a filesystem path')
    versions = {}
    for path in vendor.glob('*.dist-info/METADATA'):
        metadata = Parser().parsestr(path.read_text())
        versions[canonical(metadata['Name'])] = metadata['Version']
    require(
        versions.get('jaraco-context') == '6.1.0' and versions.get('wheel') == '0.46.3',
        'Embedded installer component mismatch',
    )
    require(
        (vendor / 'jaraco/context/__init__.py').is_file() and (vendor / 'wheel/__init__.py').is_file(),
        'Embedded component code absent',
    )
    require(distribution.read_text('RECORD'), 'Installer RECORD absent')
    target = Path('/usr/share/open-webui-derivative')
    target.mkdir(parents=True, exist_ok=True)
    inventory = {canonical(d.metadata['Name']): d.version for d in importlib.metadata.distributions()}
    (target / ('installer-' + Path(sys.prefix).name + '.json')).write_text(
        json.dumps(
            {'prefix': sys.prefix, 'packages': inventory, 'embeddedSetuptools': versions},
            sort_keys=True,
            indent=2,
        )
        + '\n'
    )


def apk_inventory():
    # Include installed package checksums and file records, not just package-version labels.
    blocks = Path('/lib/apk/db/installed').read_text().strip().split('\n\n')
    require(
        blocks and all(re.search('^P:.+$', block, re.M) and re.search('^C:.+$', block, re.M) for block in blocks),
        'Incomplete APK checksum inventory',
    )
    records = []
    for block in blocks:
        match = re.search(r'^P:(.+)$', block, re.M)
        if match is None:
            raise RuntimeError('Incomplete APK package name')
        records.append((match[1], block))
    return '\n\n'.join(block for _, block in sorted(records, key=lambda row: row[0])) + '\n'


def toolchain():
    config()
    data = build_lock()
    for name, package in data['compilerPackages'].items():
        result = subprocess.run(
            ['apk', 'info', '--exists', name + '=' + package['version']], capture_output=True, timeout=15
        )
        require(result.returncode == 0, 'Compiler package version mismatch')
    inventory = apk_inventory()
    digest = hashlib.sha256(inventory.encode()).hexdigest()
    expected = data['reproducibility']['toolchainInventorySha256']
    require(expected is None or digest == expected, 'Complete native toolchain inventory drift')
    Path('/out').mkdir(exist_ok=True)
    Path('/out/toolchain-packages.txt').write_text(inventory)
    Path('/out/toolchain.json').write_text(
        json.dumps({'inventorySha256': digest, 'frozen': expected is not None}, sort_keys=True) + '\n'
    )
    return digest


def binding(root):
    config()
    data = build_lock()
    require(
        data['reproducibility']['toolchainInventorySha256'] is not None,
        'Observe and freeze toolchain before compiling',
    )
    require(toolchain() == data['reproducibility']['toolchainInventorySha256'], 'Native toolchain mismatch')
    require(root == Path(data['reproducibility']['buildPath']), 'Fixed RE2 build path required')
    require(
        sha(Path('/build/google-re2.tar.gz')) == data['source']['sha256']
        and sha(Path('/build/re2_test.py')) == data['source']['testSha256'],
        'RE2 source/test changed',
    )
    for name, expected in data['tools'].items():
        require(importlib.metadata.version(name) == expected['version'], 'Build tool version mismatch')
    require('GITHUB_ACTIONS' not in os.environ, 'Bazel branch forbidden for this build')
    env = dict(
        os.environ,
        SOURCE_DATE_EPOCH=str(data['source']['sourceDateEpoch']),
        PYTHONHASHSEED='0',
        TZ='UTC',
        LC_ALL='C.UTF-8',
        CFLAGS=data['reproducibility']['compilerFlags'],
        CXXFLAGS=data['reproducibility']['compilerFlags'],
    )
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'pip',
            'wheel',
            '--use-pep517',
            '--no-build-isolation',
            '--no-deps',
            '--no-index',
            '--wheel-dir',
            '/out',
            str(root),
        ],
        env=env,
        capture_output=True,
        timeout=180,
    )
    require(result.returncode == 0, 'Controlled native RE2 build failed')
    wheels = list(Path('/out').glob('*.whl'))
    require(len(wheels) == 1, 'Expected one controlled native wheel')
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-index', str(wheels[0])],
        capture_output=True,
        timeout=30,
    )
    require(result.returncode == 0, 'Native RE2 test install failed')
    result = subprocess.run(
        [sys.executable, '/build/re2_test.py'], cwd='/build', env=env, capture_output=True, timeout=90
    )
    require(result.returncode == 0, 'Unchanged upstream RE2 tests failed')


def wheel(root):
    config()
    wheels = list(root.glob('*.whl'))
    require(len(wheels) == 1, 'Expected one native wheel')
    path = wheels[0]
    require(
        re.fullmatch('google_re2-1\\.1\\.20251105-cp311-cp311-linux_aarch64\\.whl', path.name),
        'Unexpected native wheel tag',
    )
    with zipfile.ZipFile(path) as archive:
        metadata = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        require(len(metadata) == 1, 'Native distribution metadata missing')
        fields = Parser().parsestr(archive.read(metadata[0]).decode())
        require(
            fields['Name'] == 'google-re2' and fields['Version'] == '1.1.20251105',
            'Native package identity changed',
        )
        require(any(name.endswith('/RECORD') for name in archive.namelist()), 'Native RECORD absent')
        require(any('LICENSE' in name for name in archive.namelist()), 'Native license absent')
    inventory = apk_inventory()
    expected = build_lock()['reproducibility']['toolchainInventorySha256']
    actual = hashlib.sha256(inventory.encode()).hexdigest()
    require(expected is None or expected == actual, 'Native toolchain inventory drift')
    proof = {
        'status': 'single-build-only',
        'filename': path.name,
        'sha256': sha(path),
        'toolchainInventorySha256': actual,
        'recipeSha256': sha(HERE / 'Dockerfile'),
        'nativeBuildLockSha256': LOCK['nativeBuildLockSha256'],
        'sourceDateEpoch': build_lock()['source']['sourceDateEpoch'],
        'buildInputs': native_build_inputs(),
    }
    (root / 'binding.json').write_text(json.dumps(proof, sort_keys=True, indent=2) + '\n')
    (root / 'toolchain-packages.txt').write_text(inventory)


def native():
    config()
    from native_check import NativeFailure, validate

    try:
        validate()
    except NativeFailure as error:
        # Categories are fixed literals from our boundary checks, never library exception text.
        print('Native ELF boundary failed:', error.category, file=sys.stderr)
        raise


def auth(backend):
    # Use unchanged production function AST, not an alternative token/password implementation.
    import bcrypt
    import jwt
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from pytz import UTC

    path = backend / 'open_webui/utils/auth.py'
    require(sha(path) == LOCK['upstream']['files']['backend/open_webui/utils/auth.py'], 'Wrong auth source')
    names = {'create_token', 'decode_token', 'get_password_hash', 'verify_password'}
    functions: list[ast.stmt] = [
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    require(
        {n.name for n in functions if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))} == names,
        'Missing production auth function',
    )
    ns = {
        'asyncio': asyncio,
        'bcrypt': bcrypt,
        'jwt': jwt,
        'datetime': dt.datetime,
        'timedelta': dt.timedelta,
        'Union': Union,
        'uuid': uuid,
        'UTC': UTC,
        'SESSION_SECRET': 'synthetic-build-only-key-' * 3,
        'ALGORITHM': 'HS256',
        'PASSWORD_HASH_ALGORITHM': 'bcrypt',
        'PASSWORD_BCRYPT_MAX_BYTES': 72,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), ns)
    token = ns['create_token']({'id': 'synthetic-user'}, dt.timedelta(minutes=1))
    require(ns['decode_token'](token)['id'] == 'synthetic-user', 'JWT roundtrip failed')
    require(
        ns['decode_token'](ns['create_token']({'id': 'synthetic-user'}, dt.timedelta(seconds=-1))) is None,
        'Expired token accepted',
    )
    require(
        ns['decode_token'](
            jwt.encode({'id': 'synthetic-user'}, 'other-synthetic-key-' * 4, algorithm='HS256')
        )
        is None,
        'Wrong signing key accepted',
    )
    require(
        ns['decode_token'](jwt.encode({'id': 'synthetic-user'}, ns['SESSION_SECRET'], algorithm='HS384'))
        is None,
        'Unexpected algorithm accepted',
    )
    for algorithm in ['bcrypt', 'argon2']:
        ns['PASSWORD_HASH_ALGORITHM'] = algorithm
        value = asyncio.run(ns['get_password_hash']('synthetic-test-password'))
        require(asyncio.run(ns['verify_password']('synthetic-test-password', value)), 'Password roundtrip failed')
        require(not asyncio.run(ns['verify_password']('wrong-synthetic-password', value)), 'Wrong password accepted')
    cipher = AESGCM(b'a' * 32)
    sealed = cipher.encrypt(b'b' * 12, b'synthetic-build-only', b'context')
    require(cipher.decrypt(b'b' * 12, sealed, b'context') == b'synthetic-build-only', 'AESGCM roundtrip failed')
    try:
        cipher.decrypt(b'b' * 12, sealed, b'other-context')
    except InvalidTag:
        pass
    else:
        raise RuntimeError('Unauthenticated AESGCM data accepted')


def imports(backend):
    # A build-stage check only: never point it at existing data or execute it on a host.
    require(os.environ.get('USE_SLIM_DOCKER') == 'true', 'Slim mode required')
    with tempfile.TemporaryDirectory(prefix='open-webui-import-') as work:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(('AWS_', 'WEBUI_ADMIN_', 'ADMIN_USER_'))
            and k
            not in {
                'DATABASE_URL',
                'WEBUI_SECRET_KEY',
                'WEBUI_JWT_SECRET_KEY',
                'WEBUI_SECRET_KEY_FILE',
                'DATA_DIR',
                'SPACE_ID',
            }
        }
        env.update(
            {
                'DATA_DIR': work,
                'DATABASE_URL': 'sqlite:///' + work + '/webui.db',
                'WEBUI_SECRET_KEY': 'synthetic-offline-import-key-' * 3,
                'OFFLINE_MODE': 'true',
                'HF_HUB_OFFLINE': '1',
                'ENABLE_PERSISTENT_CONFIG': 'false',
                'ENABLE_VERSION_UPDATE_CHECK': 'false',
                'AWS_EC2_METADATA_DISABLED': 'true',
            }
        )
        code = (
            'import open_webui.main; from open_webui.retrieval.vector import factory; assert '
            'factory.VECTOR_DB_CLIENT is None'
        )
        result = subprocess.run([sys.executable, '-c', code], cwd=backend, env=env, capture_output=True, timeout=90)
        require(result.returncode == 0, 'Native offline slim import failed; no output or data disclosed')


try:
    mode = sys.argv[1]
    if mode in {'lock', 'config', 'tools', 'toolchain', 'native'}:
        {'lock': checked_lock, 'config': config, 'tools': tools, 'toolchain': toolchain, 'native': native}[mode]()
    else:
        globals()[mode](Path(sys.argv[2]))
    print('Open WebUI focused check passed:', mode)
except Exception as error:
    # Never surface configuration/credential-bearing exception text.
    print('Open WebUI focused check failed:', sys.argv[1], type(error).__name__, file=sys.stderr)
    raise SystemExit(1)
