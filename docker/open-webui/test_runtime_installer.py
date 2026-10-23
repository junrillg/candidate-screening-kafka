"""Boundary and ordinary pip install/uninstall fixtures; run in both runtime prefixes."""

import base64
import csv
import hashlib
import importlib.metadata
import io
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import runtime_installer as installer


def make_wheel(destination: Path, *, corrupt: bool = False, nested_record: bool = False) -> None:
    metadata = 'derivative_probe-1.0.dist-info/'
    files = {
        'derivative_probe.py': b'VALUE = 42\n',
        metadata + 'METADATA': b'Metadata-Version: 2.1\nName: derivative-probe\nVersion: 1.0\n',
        metadata + 'WHEEL': b'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    if nested_record:
        files['vendored/nested-1.0.dist-info/RECORD'] = b'vendored record; hashed by the outer wheel RECORD\n'
    record = metadata + 'RECORD'
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator='\n')
    for name, value in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(value).digest()).decode().rstrip('=')
        writer.writerow([name, 'sha256=' + digest, str(len(value))])
    writer.writerow([record, '', ''])
    files[record] = stream.getvalue().encode()
    if corrupt:
        files['derivative_probe.py'] = b'VALUE = 0\n'
    with zipfile.ZipFile(destination, 'w') as wheel:
        for name, value in files.items():
            wheel.writestr(name, value)


class InstallerBoundaries(unittest.TestCase):
    def test_ensurepip_rejects_source_constants_and_extra_bundle_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / '_bundled'
            bundled.mkdir()
            (root / '__pycache__').mkdir()
            source = root / '__init__.py'
            original = '_PIP_VERSION = "24.0"\n_SETUPTOOLS_VERSION = "79.0.1"\n'
            source.write_text(original)
            wheels: dict[str, object] = {}
            for name, version in [('pip', '24.0'), ('setuptools', '79.0.1')]:
                path = bundled / f'{name}-{version}-py3-none-any.whl'
                path.write_bytes(name.encode())
                wheels[name] = {'filename': path.name, 'sha256': installer.sha(path)}
            spec: dict[str, object] = {
                'originalSourceSha256': installer.sha(source),
                'moduleSha256': {},
                'originalWheels': wheels,
                'directories': ['_bundled', '__pycache__'],
            }
            manifest: dict[str, object] = {'ensurepip': spec}
            with mock.patch.object(installer, 'ENSUREPIP', root):
                installer.ensurepip_inventory(manifest, repaired=False)
                source.write_text(original + '# changed\n')
                with self.assertRaisesRegex(installer.InstallerFailure, 'ensurepip_file_changed'):
                    installer.ensurepip_inventory(manifest, repaired=False)
                source.write_text(original.replace('24.0', '25.0'))
                spec['originalSourceSha256'] = installer.sha(source)
                with self.assertRaisesRegex(installer.InstallerFailure, 'ensurepip_version_constants'):
                    installer.ensurepip_inventory(manifest, repaired=False)
                source.write_text(original)
                spec['originalSourceSha256'] = installer.sha(source)
                cached = root / '__pycache__/unexpected.pyc'
                cached.write_bytes(b'cached code')
                with self.assertRaisesRegex(installer.InstallerFailure, 'ensurepip_file_inventory'):
                    installer.ensurepip_inventory(manifest, repaired=False)
                cached.unlink()
                (bundled / 'unexpected.whl').write_bytes(b'extra')
                with self.assertRaisesRegex(installer.InstallerFailure, 'ensurepip_file_inventory'):
                    installer.ensurepip_inventory(manifest, repaired=False)
                (bundled / 'unexpected.whl').unlink()
                os.mkfifo(root / 'unexpected')
                with self.assertRaisesRegex(installer.InstallerFailure, 'ensurepip_special_file'):
                    installer.ensurepip_inventory(manifest, repaired=False)

    def test_bootstrap_wheel_metadata_cannot_inherit_a_matching_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.whl'
            make_wheel(path)
            row: dict[str, object] = {'sha256': installer.sha(path), 'bytes': path.stat().st_size}
            with self.assertRaisesRegex(installer.InstallerFailure, 'bootstrap_wheel_metadata'):
                installer.validate_bootstrap_wheel(path, 'pip', row, {})

    def test_outer_wheel_record_covers_vendored_record_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.whl'
            make_wheel(path, nested_record=True)
            self.assertIn('vendored/nested-1.0.dist-info/RECORD', installer.wheel_files(path))
            make_wheel(path, corrupt=True, nested_record=True)
            with self.assertRaisesRegex(installer.InstallerFailure, 'wheel_record_hash'):
                installer.wheel_files(path)

    def test_sourceless_or_cached_shadow_bytecode_is_rejected(self) -> None:
        for relative in ('pip/_vendor/setuptools/package_index.pyc', 'pip/__pycache__/__init__.cpython-311.pyc'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'pip').mkdir()
                (root / 'pip/__init__.py').write_text('VALUE = 42\n')
                self.assertEqual(set(installer.pip_tree(root)), {'pip/__init__.py'})
                source = root / 'shadow.py'
                source.write_text('SHADOW = True\n')
                shadow = root / relative
                shadow.parent.mkdir(parents=True, exist_ok=True)
                py_compile.compile(str(source), cfile=str(shadow), doraise=True)
                source.unlink()
                with self.assertRaisesRegex(installer.InstallerFailure, 'runtime_pip_bytecode'):
                    installer.pip_tree(root)

    def test_build_apk_admission_rejects_base_changes_and_unknown_tools(self) -> None:
        base = 'P:base\nV:1\nC:Q1fixture\nA:aarch64\n\n'
        added = 'P:git\nV:2.54.0-r0\nC:Q1gitfixture\nA:aarch64\n\n'
        manifest: dict[str, object] = {
            'buildApkAdmission': {'base': installer.apk_rows(base), 'added': installer.apk_rows(added)}
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'base-apk-database.txt').write_text(base)
            after = root / 'tool-apk-database.txt'
            after.write_text(base + added)
            (root / 'git-version.txt').write_text('git version 2.54.0\n')
            installer.admit_apks(root, manifest)
            after.write_text(base.replace('V:1', 'V:2') + added)
            with self.assertRaisesRegex(installer.InstallerFailure, 'build_apk_base_mutated'):
                installer.admit_apks(root, manifest)
            after.write_text(added)
            with self.assertRaisesRegex(installer.InstallerFailure, 'build_apk_base_mutated'):
                installer.admit_apks(root, manifest)
            after.write_text(base + added.replace('Q1gitfixture', 'Q1changed'))
            with self.assertRaisesRegex(installer.InstallerFailure, 'build_apk_tool_delta_changed'):
                installer.admit_apks(root, manifest)

    def test_changed_wheel_record_and_path_traversal_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.whl'
            make_wheel(path)
            self.assertIn('derivative_probe.py', installer.wheel_files(path))
            make_wheel(path, corrupt=True)
            with self.assertRaisesRegex(installer.InstallerFailure, 'wheel_record_hash'):
                installer.wheel_files(path)
            with zipfile.ZipFile(path, 'w') as wheel:
                wheel.writestr('../outside', b'payload')
            with self.assertRaisesRegex(installer.InstallerFailure, 'wheel_paths'):
                installer.wheel_files(path)

    def test_vulnerable_setuptools_module_cannot_inherit_subset_admission(self) -> None:
        files = {
            'pip/_vendor/pkg_resources/__init__.py': b'fixture',
            'pip/_vendor/setuptools/package_index.py': b'new module',
        }
        manifest: dict[str, object] = {'pkgResourcesSha256': hashlib.sha256(b'fixture').hexdigest()}
        with self.assertRaisesRegex(installer.InstallerFailure, 'unexpected_setuptools_subtree'):
            installer.validate_pip_files(files, manifest)

    def test_artifact_rejects_changed_hash_or_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact'
            path.write_bytes(b'expected')
            row: dict[str, object] = {'sha256': installer.sha(path), 'bytes': path.stat().st_size}
            installer.checked_artifact(path, row)
            path.write_bytes(b'modified')
            with self.assertRaisesRegex(installer.InstallerFailure, 'artifact_changed'):
                installer.checked_artifact(path, row)
            alias = path.with_name('alias')
            alias.symlink_to(path)
            with self.assertRaisesRegex(installer.InstallerFailure, 'artifact_missing_or_symlink'):
                installer.checked_artifact(alias, row)


class RuntimeInstaller(unittest.TestCase):
    def test_fresh_venv_bootstraps_repaired_ensurepip_without_network(self) -> None:
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'PIP_CONFIG_FILE': '/dev/null'}
        probe = (
            'import hashlib,importlib.metadata as m,importlib.util,json,pathlib,sys;'
            f'sys.path.insert(0,{str(installer.HERE)!r});import runtime_installer as r;'
            'import pip._vendor.urllib3 as u;import pip._vendor.msgpack as p;'
            f'assert m.version("pip") == {installer.VERSION!r};'
            'assert m.version("setuptools") == "84.2.1";'
            'assert u.__version__ == "2.8.0" and p.__version__ == "1.2.1";'
            'manifest=r.load(r.HERE/"runtime-installer.json");'
            'wheels={"pip":r.mapping(r.mapping(manifest["outputs"])["pip"]),'
            '"setuptools":r.rows(manifest,"tools")["setuptools"]};'
            'root=pathlib.Path(sys.prefix)/"lib/python3.11/site-packages";'
            'expected={name:data for row in wheels.values() for name,data in '
            'r.wheel_files(r.ENSUREPIP/"_bundled"/r.string(row["filename"])).items() '
            'if not (name.endswith(".dist-info/RECORD") and "/" not in name.split(".dist-info/")[0])};'
            'assert all((root/name).read_bytes()==data for name,data in expected.items());'
            'caches=list(root.rglob("*.pyc"));'
            'assert all(pathlib.Path(importlib.util.source_from_cache(str(path))).is_file() for path in caches);'
            'print(json.dumps({"cacheFiles":len(caches),"wheelSourceFilesVerified":len(expected)}))'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env['HOME'] = str(root)
            python = str(root / 'venv/bin/python')
            commands = [
                [sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', str(root / 'venv')],
                [python, '-I', '-B', '-m', 'ensurepip', '--upgrade', '--default-pip'],
                [python, '-I', '-B', '-m', 'pip', 'check'],
                [python, '-I', '-B', '-c', probe],
            ]
            for argv in commands:
                result = subprocess.run(argv, env=env, capture_output=True, timeout=30, check=False)
                self.assertLessEqual(len(result.stdout) + len(result.stderr), 65536)
                self.assertEqual(result.returncode, 0, 'bounded ensurepip bootstrap fixture failed')
        installer.ensurepip_inventory(installer.load(installer.HERE / 'runtime-installer.json'), repaired=True)
        self.assertFalse(os.path.exists(directory))

    def test_actual_runtime_pip_installs_and_uninstalls_local_wheel(self) -> None:
        self.assertEqual(importlib.metadata.version('pip'), installer.VERSION)
        env = {
            'PATH': '/opt/venv/bin:/usr/local/bin:/usr/bin:/bin',
            'PIP_CONFIG_FILE': '/dev/null',
            'PIP_NO_INDEX': '1',
            'PIP_DISABLE_PIP_VERSION_CHECK': '1',
            'PYTHONDONTWRITEBYTECODE': '1',
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env['HOME'] = str(root)
            wheel = root / 'derivative_probe-1.0-py3-none-any.whl'
            make_wheel(wheel)
            commands = [
                [sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', str(root / 'venv')],
                [
                    sys.executable,
                    '-I',
                    '-B',
                    '-m',
                    'pip',
                    '--python',
                    str(root / 'venv'),
                    'install',
                    '--no-index',
                    '--no-deps',
                    '--no-compile',
                    str(wheel),
                ],
                [
                    str(root / 'venv/bin/python'),
                    '-I',
                    '-B',
                    '-c',
                    'import derivative_probe;assert derivative_probe.VALUE == 42',
                ],
                [
                    sys.executable,
                    '-I',
                    '-B',
                    '-m',
                    'pip',
                    '--python',
                    str(root / 'venv'),
                    'uninstall',
                    '-y',
                    'derivative-probe',
                ],
                [
                    str(root / 'venv/bin/python'),
                    '-I',
                    '-B',
                    '-c',
                    'import importlib.util;assert importlib.util.find_spec("derivative_probe") is None',
                ],
            ]
            for argv in commands:
                result = subprocess.run(argv, env=env, capture_output=True, timeout=30, check=False)
                self.assertLessEqual(len(result.stdout) + len(result.stderr), 65536)
                self.assertEqual(result.returncode, 0, 'bounded local installer fixture failed')
        self.assertFalse(os.path.exists(directory))


if __name__ == '__main__':
    unittest.main()
