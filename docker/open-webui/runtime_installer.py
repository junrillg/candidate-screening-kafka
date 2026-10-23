"""Reproduce and verify the runtime-only pip security derivative from pinned sources."""

import argparse
import base64
import csv
import email.parser
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve().parent
VERSION = '26.2.2+local.openwebui.1'
ENSUREPIP = Path('/usr/local/lib/python3.11/ensurepip')
MAX_ARTIFACT = 16 * 1024 * 1024


class InstallerFailure(RuntimeError):
    """An authored category, safe to print without library/path/environment payloads."""


def require(value: object, category: str) -> None:
    if not value:
        raise InstallerFailure(category)


def mapping(value: object) -> dict[str, object]:
    require(isinstance(value, dict), 'invalid_object')
    if not isinstance(value, dict):
        raise InstallerFailure('invalid_object')
    result: dict[str, object] = {}
    for key, item in value.items():
        require(isinstance(key, str), 'invalid_object_key')
        if isinstance(key, str):
            result[key] = item
    return result


def string(value: object) -> str:
    if not isinstance(value, str):
        raise InstallerFailure('invalid_string')
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise InstallerFailure('invalid_integer')
    return value


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load(path: Path) -> dict[str, object]:
    require(path.is_file() and not path.is_symlink() and path.stat().st_size < 1024 * 1024, 'invalid_json_file')
    value: object = json.loads(path.read_text())
    return mapping(value)


def rows(manifest: dict[str, object], group: str) -> dict[str, dict[str, object]]:
    return {name: mapping(row) for name, row in mapping(manifest[group]).items()}


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def apk_rows(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for block in text.strip().split('\n\n'):
        fields = dict(
            line.split(':', 1) for line in block.splitlines() if len(line) > 1 and line[1] == ':' and line[0] in 'PVCA'
        )
        require({'P', 'V', 'C', 'A'} <= fields.keys() and fields['P'] not in result, 'apk_inventory_shape')
        result[fields['P']] = {key: fields[key] for key in ('V', 'C', 'A')}
    return result


def admit_apks(directory: Path, manifest: dict[str, object]) -> None:
    before = apk_rows((directory / 'base-apk-database.txt').read_text())
    after = apk_rows((directory / 'tool-apk-database.txt').read_text())
    expected = mapping(manifest['buildApkAdmission'])
    require(before == expected['base'], 'build_apk_base_changed')
    removed = sorted(before.keys() - after.keys())
    changed = {
        name: {'before': before[name], 'after': after[name]}
        for name in before.keys() & after.keys()
        if before[name] != after[name]
    }
    added = {name: after[name] for name in after.keys() - before.keys()}
    require(not removed and not changed, 'build_apk_base_mutated')
    require(added == expected['added'], 'build_apk_tool_delta_changed')
    require((directory / 'git-version.txt').read_text().strip() == 'git version 2.54.0', 'build_git_version')
    save(
        directory / 'tool-apk-diff.json',
        {
            'added': added,
            'changed': changed,
            'removed': removed,
            'base': before,
            'tools': after,
            'normalApkSignatureVerificationRequired': True,
        },
    )


def checked_artifact(path: Path, row: dict[str, object]) -> None:
    require(path.is_file() and not path.is_symlink(), 'artifact_missing_or_symlink')
    require(path.stat().st_size == integer(row['bytes']) and sha(path) == string(row['sha256']), 'artifact_changed')


def fetch_one(destination: Path, row: dict[str, object]) -> None:
    url = string(row['url'])
    require(
        url.startswith(('https://files.pythonhosted.org/packages/', 'https://codeload.github.com/pypa/pip/')),
        'artifact_origin',
    )
    require(0 < integer(row['bytes']) <= MAX_ARTIFACT and not destination.exists(), 'artifact_destination')
    with urllib.request.urlopen(url, timeout=30) as response, destination.open('xb') as stream:
        total = 0
        while data := response.read(65536):
            total += len(data)
            require(total <= integer(row['bytes']), 'artifact_size_bound')
            stream.write(data)
    checked_artifact(destination, row)


def fetch(inputs: Path, manifest: dict[str, object]) -> None:
    """Only this mode has network access; it does not invoke a package resolver."""
    inputs.mkdir()
    for group in ('tools', 'vendor'):
        (inputs / group).mkdir()
        for row in rows(manifest, group).values():
            if group != 'vendor' or not row['needsPurePythonBuild']:
                filename = string(row['filename'])
                require(Path(filename).name == filename, 'artifact_filename')
                fetch_one(inputs / group / filename, row)
    fetch_one(
        inputs / 'pip-source.tar.gz',
        {
            'url': manifest['pipArchiveUrl'],
            'bytes': manifest['pipArchiveBytes'],
            'sha256': manifest['pipArchiveSha256'],
        },
    )
    fetch_one(inputs / 'msgpack-1.2.1.tar.gz', rows(manifest, 'vendor')['msgpack'])
    for source, target, key in [
        ('runtime-installer-tools.lock', 'tools.lock', 'toolsLockSha256'),
        ('runtime-vendor.txt', 'vendor.txt', 'vendorInputSha256'),
    ]:
        require(sha(HERE / source) == string(manifest[key]), 'recipe_input_changed')
        shutil.copyfile(HERE / source, inputs / target)


def extract(source: Path, target: Path, *, pip: bool = False) -> None:
    target.mkdir()
    with tarfile.open(source) as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            require(parts and not Path(member.name).is_absolute() and '..' not in parts, 'source_archive_path')
            relative = Path(*parts[1:])
            selected = (
                not pip
                or str(relative) in {'pyproject.toml', 'README.rst', 'AUTHORS.txt', 'LICENSE.txt'}
                or str(relative).startswith(('src/pip/', 'tools/vendoring/patches/'))
            )
            if str(relative) == '.' or not selected:
                continue
            require(member.isdir() or member.isfile(), 'source_archive_special_file')
            path = target / relative
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                require(member.size <= MAX_ARTIFACT, 'source_member_size')
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                require(stream is not None, 'source_member_missing')
                if stream is not None:
                    with stream:
                        path.write_bytes(stream.read())


def wheel_files(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        require(
            len(names) == len(set(names))
            and all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in names),
            'wheel_paths',
        )
        require(sum(row.file_size for row in wheel.infolist()) < 32 * 1024 * 1024, 'wheel_expansion_bound')
        files = {name: wheel.read(name) for name in names if not name.endswith('/')}
    records = [name for name in files if name.endswith('.dist-info/RECORD') and '/' not in name.split('.dist-info/')[0]]
    require(len(records) == 1, 'wheel_record_count')
    record = records[0]
    inventory = list(csv.reader(io.StringIO(files[record].decode())))
    require(all(len(row) == 3 for row in inventory), 'wheel_record_shape')
    require({row[0] for row in inventory} == set(files) and len(inventory) == len(files), 'wheel_record_inventory')
    for name, digest, size in inventory:
        expected = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).decode().rstrip('=')
        require(
            (not digest and not size) if name == record else digest == expected and size == str(len(files[name])),
            'wheel_record_hash',
        )
    require(not any(name.endswith(('.so', '.c', '.cpp', '.pyx')) for name in files), 'non_pure_wheel')
    return files


def validate_pip_files(files: dict[str, bytes], manifest: dict[str, object]) -> None:
    require(
        hashlib.sha256(files['pip/_vendor/pkg_resources/__init__.py']).hexdigest()
        == string(manifest['pkgResourcesSha256']),
        'pkg_resources_changed',
    )
    require(not any(name.startswith('pip/_vendor/setuptools/') for name in files), 'unexpected_setuptools_subtree')
    for name, digest in mapping(manifest['retainedLicenseFiles']).items():
        require(name in files and hashlib.sha256(files[name]).hexdigest() == string(digest), 'vendor_license_changed')
    sbom: object = json.loads(files['pip/_vendor/bom.cdx.json'])
    components = mapping(sbom)['components']
    require(isinstance(components, list), 'sbom_components')
    if not isinstance(components, list):
        raise InstallerFailure('sbom_components')
    versioned = [mapping(row) for row in components if 'version' in mapping(row)]
    actual = {string(row['name']).lower(): string(row['version']) for row in versioned}
    expected = {name.lower(): string(row['version']) for name, row in rows(manifest, 'vendor').items()}
    require(actual == expected and len(versioned) == len(expected), 'complete_sbom_versions')
    require(
        hashlib.sha256(files['pip/_vendor/bom.cdx.json']).hexdigest() == string(manifest['sbomSha256']), 'sbom_changed'
    )


def command(argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> None:
    # Build steps use ordinary upstream tools and contain no credentials. The outer
    # admitted builder supplies cgroup/time/disk limits; these are per-call bounds.
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout, check=False)
    require(len(result.stdout) + len(result.stderr) <= 1024 * 1024, 'build_output_bound')
    require(result.returncode == 0, 'offline_build_failed')


def build(inputs: Path, output: Path, manifest: dict[str, object]) -> None:
    require(sys.version_info[:3] == (3, 11, 17) and os.uname().machine == 'aarch64', 'build_target')
    require(
        {d.metadata['Name'].lower().replace('_', '-'): d.version for d in importlib.metadata.distributions()}
        == {name: string(row['version']) for name, row in rows(manifest, 'tools').items()},
        'build_tool_graph',
    )
    for group in ('tools', 'vendor'):
        expected = {
            string(row['filename']): row
            for row in rows(manifest, group).values()
            if group != 'vendor' or not row['needsPurePythonBuild']
        }
        require({p.name for p in (inputs / group).iterdir()} == set(expected), 'input_inventory')
        for name, row in expected.items():
            checked_artifact(inputs / group / name, row)
    require(
        sha(inputs / 'pip-source.tar.gz') == string(manifest['pipArchiveSha256'])
        and sha(inputs / 'msgpack-1.2.1.tar.gz') == string(manifest['msgpackSourceSha256']),
        'source_hash',
    )
    require(
        sha(inputs / 'tools.lock') == string(manifest['toolsLockSha256'])
        and sha(inputs / 'vendor.txt') == string(manifest['vendorInputSha256']),
        'recipe_input_changed',
    )
    output.mkdir()
    work = output / 'work'
    work.mkdir()
    env = {
        'PATH': '/opt/tools/bin:/usr/local/bin:/usr/bin:/bin',
        'HOME': '/tmp/home',
        'TMPDIR': '/tmp',
        'PIP_CONFIG_FILE': '/dev/null',
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'PIP_NO_INDEX': '1',
        'PIP_ONLY_BINARY': ':all:',
        'PYTHONHASHSEED': '0',
        'SOURCE_DATE_EPOCH': str(integer(manifest['epoch'])),
        'TZ': 'UTC',
        'LC_ALL': 'C.UTF-8',
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    Path('/tmp/home').mkdir(exist_ok=True)
    extract(inputs / 'msgpack-1.2.1.tar.gz', work / 'msgpack')
    command(
        [
            sys.executable,
            '-I',
            '-m',
            'pip',
            'wheel',
            '--no-index',
            '--no-cache-dir',
            '--no-deps',
            '--no-build-isolation',
            '--wheel-dir',
            str(output),
            str(work / 'msgpack'),
        ],
        work,
        {**env, 'MSGPACK_PUREPYTHON': '1'},
        60,
    )
    msgpack = mapping(mapping(manifest['outputs'])['msgpack'])
    checked_artifact(output / string(msgpack['filename']), msgpack)
    wheel_files(output / string(msgpack['filename']))
    shutil.copytree(inputs / 'vendor', work / 'vendor')
    shutil.copyfile(output / string(msgpack['filename']), work / 'vendor' / string(msgpack['filename']))
    build_pip(inputs, output, work, env, manifest)
    setuptools = rows(manifest, 'tools')['setuptools']
    wheel = inputs / 'tools' / string(setuptools['filename'])
    validate_bootstrap_wheel(wheel, 'setuptools', setuptools, manifest)
    shutil.copyfile(wheel, output / wheel.name)
    shutil.rmtree(work)


def build_pip(inputs: Path, output: Path, work: Path, env: dict[str, str], manifest: dict[str, object]) -> None:
    project = work / 'pip'
    extract(inputs / 'pip-source.tar.gz', project, pip=True)
    patches = mapping(manifest['upstreamPatches'])
    for name, digest in patches.items():
        require(sha(project / name) == string(digest), 'upstream_patch_changed')
    version = project / 'src/pip/__init__.py'
    original = version.read_text()
    require(original.count('__version__ = "26.2.2"') == 1, 'pip_upstream_version')
    version.write_text(original.replace('__version__ = "26.2.2"', '__version__ = "' + VERSION + '"'))
    shutil.copyfile(inputs / 'vendor.txt', project / 'src/pip/_vendor/vendor.txt')
    vendor = work / 'vendor'
    expected = {p.name: sha(p) for p in vendor.iterdir()}
    command(['/opt/tools/bin/vendoring', 'sync', '-v'], project, {**env, 'PIP_FIND_LINKS': str(vendor)}, 120)
    require({p.name: sha(p) for p in vendor.iterdir()} == expected, 'vendor_wheelhouse_changed')
    provenance = {
        'upstreamCommit': manifest['pipCommit'],
        'sourceArchiveSha256': manifest['pipArchiveSha256'],
        'version': VERSION,
        'sourceDateEpoch': manifest['epoch'],
        'vendorArtifacts': expected,
        'upstreamPatchSha256': patches,
    }
    save(project / 'src/pip/_vendor/runtime-derivative.json', provenance)
    with (project / 'src/pip/_vendor/README.rst').open('a') as stream:
        stream.write(
            '\n'
            'Local runtime security derivative: upstream urllib3 2.8.0 and msgpack 1.2.1; '
            'unchanged upstream vendoring patches, retained licenses and complete regenerated '
            'SBOM. Exact source/artifact provenance is in runtime-derivative.json.\n'
        )
    command(
        [
            sys.executable,
            '-I',
            '-c',
            'from flit_core.buildapi import build_wheel;print(build_wheel(' + repr(str(output)) + '))',
        ],
        project,
        env,
        60,
    )
    pip = mapping(mapping(manifest['outputs'])['pip'])
    wheel = output / string(pip['filename'])
    checked_artifact(wheel, pip)
    files = wheel_files(wheel)
    validate_pip_files(files, manifest)
    hashes = {
        name: hashlib.sha256(data).hexdigest() for name, data in files.items() if not name.endswith('.dist-info/RECORD')
    }
    require(json_hash(hashes) == string(manifest['installedFilesSha256']), 'installed_file_manifest_changed')
    save(output / 'installed-files.json', {'wheelSha256': pip['sha256'], 'files': hashes})


def pip_tree(root: Path) -> dict[str, Path]:
    require((root / 'pip').is_dir() and not (root / 'pip').is_symlink(), 'runtime_pip_directory')
    paths = list((root / 'pip').rglob('*'))
    require(not any(path.is_symlink() for path in paths), 'runtime_pip_symlink')
    require(not any(path.suffix in {'.pyc', '.pyo'} for path in paths), 'runtime_pip_bytecode')
    return {str(path.relative_to(root)): path for path in paths if path.is_file()}


def validate_bootstrap_wheel(path: Path, name: str, row: dict[str, object], manifest: dict[str, object]) -> None:
    checked_artifact(path, row)
    require(path.stat().st_nlink == 1, 'bootstrap_wheel_hardlink')
    files = wheel_files(path)
    version = VERSION if name == 'pip' else '84.2.1'
    metadata = [
        value
        for key, value in files.items()
        if key.endswith('.dist-info/METADATA') and '/' not in key.split('.dist-info/')[0]
    ]
    require(len(metadata) == 1, 'bootstrap_wheel_metadata_count')
    parsed = email.parser.BytesParser().parsebytes(metadata[0])
    require(parsed['Name'] == name and parsed['Version'] == version, 'bootstrap_wheel_metadata')
    if name == 'pip':
        validate_pip_files(files, manifest)
    else:
        require(row['version'] == version, 'bootstrap_setuptools_version')
        for license_path, digest in mapping(row['licenseFiles']).items():
            require(
                license_path in files and hashlib.sha256(files[license_path]).hexdigest() == string(digest),
                'bootstrap_license_changed',
            )


def ensurepip_inventory(manifest: dict[str, object], *, repaired: bool) -> dict[str, str]:
    spec = mapping(manifest['ensurepip'])
    require(ENSUREPIP.is_dir() and not ENSUREPIP.is_symlink(), 'ensurepip_directory')
    paths = list(ENSUREPIP.rglob('*'))
    require(not any(path.is_symlink() for path in paths), 'ensurepip_symlink')
    require(
        all(stat.S_ISREG(path.lstat().st_mode) or stat.S_ISDIR(path.lstat().st_mode) for path in paths),
        'ensurepip_special_file',
    )
    require(
        spec['directories'] == ['_bundled', '__pycache__']
        and {str(path.relative_to(ENSUREPIP)) for path in paths if path.is_dir()} == {'_bundled', '__pycache__'},
        'ensurepip_directories',
    )
    wheels = (
        {'pip': mapping(mapping(manifest['outputs'])['pip']), 'setuptools': rows(manifest, 'tools')['setuptools']}
        if repaired
        else rows(spec, 'originalWheels')
    )
    expected = {name: string(digest) for name, digest in mapping(spec['moduleSha256']).items()}
    expected['__init__.py'] = string(spec['repairedSourceSha256' if repaired else 'originalSourceSha256'])
    expected.update({'_bundled/' + string(row['filename']): string(row['sha256']) for row in wheels.values()})
    actual = {str(path.relative_to(ENSUREPIP)): path for path in paths if path.is_file()}
    require(set(actual) == set(expected), 'ensurepip_file_inventory')
    require(
        all(path.stat().st_nlink == 1 and sha(path) == expected[name] for name, path in actual.items()),
        'ensurepip_file_changed',
    )
    source = actual['__init__.py'].read_text()
    versions = {'pip': VERSION, 'setuptools': '84.2.1'} if repaired else {'pip': '24.0', 'setuptools': '79.0.1'}
    require(
        all(
            source.count('_' + name.upper() + '_VERSION = "' + version + '"') == 1 for name, version in versions.items()
        ),
        'ensurepip_version_constants',
    )
    return expected


def repair_ensurepip(wheels: Path, manifest: dict[str, object]) -> None:
    """Replace only the exact base runtime's two obsolete complete bootstrap wheels."""
    ensurepip_inventory(manifest, repaired=False)
    replacements = {
        'pip': mapping(mapping(manifest['outputs'])['pip']),
        'setuptools': rows(manifest, 'tools')['setuptools'],
    }
    for name, row in replacements.items():
        validate_bootstrap_wheel(wheels / string(row['filename']), name, row, manifest)
    source = ENSUREPIP / '__init__.py'
    updated = (
        source.read_text()
        .replace('_PIP_VERSION = "24.0"', '_PIP_VERSION = "' + VERSION + '"')
        .replace('_SETUPTOOLS_VERSION = "79.0.1"', '_SETUPTOOLS_VERSION = "84.2.1"')
    )
    spec = mapping(manifest['ensurepip'])
    require(
        hashlib.sha256(updated.encode()).hexdigest() == string(spec['repairedSourceSha256']),
        'ensurepip_repaired_source',
    )
    for row in rows(spec, 'originalWheels').values():
        (ENSUREPIP / '_bundled' / string(row['filename'])).unlink()
    for row in replacements.values():
        shutil.copyfile(wheels / string(row['filename']), ENSUREPIP / '_bundled' / string(row['filename']))
    source.write_text(updated)
    ensurepip_inventory(manifest, repaired=True)


def verify(inventory: Path, manifest: dict[str, object]) -> dict[str, object]:
    require(
        sys.flags.isolated
        and sys.dont_write_bytecode
        and not {'LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH'}.intersection(os.environ),
        'isolated_runtime_required',
    )
    require(sys.prefix in {'/usr/local', '/opt/venv'}, 'unexpected_runtime_prefix')
    require(
        importlib.metadata.version('pip') == VERSION and importlib.metadata.version('setuptools') == '84.2.1',
        'runtime_installer_version',
    )
    for name in ('pip', 'setuptools'):
        distributions = [d for d in importlib.metadata.distributions() if d.metadata['Name'].lower() == name]
        require(len(distributions) == 1, 'runtime_installer_distribution_count')
    frozen = load(inventory)
    require(frozen['wheelSha256'] == mapping(mapping(manifest['outputs'])['pip'])['sha256'], 'runtime_wheel_changed')
    hashes = mapping(frozen['files'])
    require(json_hash(hashes) == string(manifest['installedFilesSha256']), 'runtime_inventory_changed')
    distribution = importlib.metadata.distribution('pip')
    root = distribution.locate_file('')
    if not isinstance(root, Path):
        raise InstallerFailure('runtime_distribution_location')
    require(root.resolve() == Path(sys.prefix) / 'lib/python3.11/site-packages', 'runtime_distribution_location')
    actual = pip_tree(root)
    require(set(actual) == {name for name in hashes if name.startswith('pip/')}, 'runtime_pip_file_inventory')
    files: dict[str, bytes] = {}
    for name, digest in hashes.items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'runtime_file_path')
        path = root / name
        require(path.is_file() and not path.is_symlink() and sha(path) == string(digest), 'runtime_file_changed')
        if name.startswith('pip/'):
            files[name] = path.read_bytes()
    validate_pip_files(files, manifest)
    bootstrap = ensurepip_inventory(manifest, repaired=True)
    return {
        'prefix': sys.prefix,
        'pipVersion': VERSION,
        'wheelSha256': frozen['wheelSha256'],
        'fileInventorySha256': json_hash(hashes),
        'ensurepipFileSha256': bootstrap,
        'packages': {
            d.metadata['Name'].lower().replace('_', '-'): d.version for d in importlib.metadata.distributions()
        },
    }


def fail() -> NoReturn:
    print('runtime_installer_failed', file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog='Build requires the pinned isolated /opt/tools environment and network disabled; '
        'verify runs once in each runtime interpreter.',
    )
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('fetch', help='fetch exact public source/artifact hashes, without resolving').add_argument(
        'inputs', type=Path
    )
    sub.add_parser('admit-apks', help='enforce exact signed build-only APK inventory/delta').add_argument(
        'directory', type=Path
    )
    build_parser = sub.add_parser('build', help='build offline artifacts; enforce reviewed whole-wheel output hashes')
    build_parser.add_argument('inputs', type=Path)
    build_parser.add_argument('output', type=Path)
    sub.add_parser('verify', help='verify current runtime prefix against the exact built wheel').add_argument(
        'inventory', type=Path
    )
    sub.add_parser('repair-ensurepip', help='repair the two exact runtime-only bootstrap wheels').add_argument(
        'wheels', type=Path
    )
    arguments = sys.argv[1:]
    if arguments and arguments[0].lower() in {'help', '--help', '-h'}:
        parser.print_help()
        return
    args = parser.parse_args(arguments)
    try:
        manifest = load(HERE / 'runtime-installer.json')
        if args.mode == 'fetch':
            fetch(args.inputs.resolve(), manifest)
        elif args.mode == 'admit-apks':
            admit_apks(args.directory.resolve(), manifest)
        elif args.mode == 'build':
            build(args.inputs.resolve(), args.output.resolve(), manifest)
        elif args.mode == 'repair-ensurepip':
            repair_ensurepip(args.wheels.resolve(), manifest)
        else:
            print(json.dumps(verify(args.inventory, manifest), sort_keys=True))
    except InstallerFailure as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError):
        fail()


if __name__ == '__main__':
    main()
