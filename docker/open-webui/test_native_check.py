"""Focused trust-boundary checks for the production native validator."""

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().with_name('native_check.py')
spec = importlib.util.spec_from_file_location('native_check', SOURCE)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeChecks(unittest.TestCase):
    def test_help_stops_without_lock_or_task_actions(self):
        hook = (
            'import os, pathlib, sys\n'
            'path = pathlib.Path(sys.argv[1])\n'
            'source = path.read_text()\n'
            'directory = str(path.parent) + os.sep\n'
            'argument = sys.argv[2]\n'
            'def audit(event, args):\n'
            "    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', "
            "'os.mkdir', 'os.remove', 'os.rename', 'os.rmdir'}:\n"
            "        raise RuntimeError('help attempted a task action')\n"
            "    if event == 'open':\n"
            '        target, _, flags = args\n'
            '        if isinstance(target, (str, bytes, os.PathLike)) and '
            'os.fsdecode(target).startswith(directory):\n'
            "            raise RuntimeError('help read a workspace input')\n"
            '        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):'
            '\n'
            "            raise RuntimeError('help attempted a write')\n"
            'sys.argv = [str(path), argument]\n'
            'sys.addaudithook(audit)\n'
            "exec(compile(source, str(path), 'exec'), {'__name__': '__main__', '__file__': "
            'str(path)})\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'check.py'
            script.write_bytes(SOURCE.with_name('check.py').read_bytes())
            for argument in ('help', '--help', '-h', 'HeLp'):
                result = subprocess.run(
                    [sys.executable, '-I', '-B', '-c', hook, str(script), argument],
                    cwd=directory,
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0)
                self.assertFalse(result.stderr)
                self.assertLess(len(result.stdout), 65536)
                self.assertIn(b'Purpose: check the pinned Open WebUI', result.stdout)
            self.assertEqual({path.name for path in Path(directory).iterdir()}, {'check.py'})

    def test_only_exact_pinned_cpython_linker_object_is_nonloadable(self):
        path, digest = next(iter(native.LINKER_INPUTS.items()))
        header = bytearray(20)
        header[:6] = b'\x7fELF\x02\x01'
        header[16:18] = (1).to_bytes(2, 'little')
        header[18:20] = (183).to_bytes(2, 'little')
        self.assertEqual(native.elf_kind(path, header, '', '', digest), 1)
        for args in [
            (path + '.other', header, '', '', digest),
            (path, header, '', '', 'changed'),
            (path, header, 'libmissing.so', '', digest),
            (path, header, '', 'libunknown.so', digest),
        ]:
            with self.assertRaisesRegex(RuntimeError, 'Unreviewed relocatable'):
                native.elf_kind(*args)
        header[18:20] = (62).to_bytes(2, 'little')
        with self.assertRaisesRegex(RuntimeError, 'Wrong runtime ELF target'):
            native.elf_kind(path, header, '', '', digest)

    def test_compiler_fingerprint_ignores_runtime_but_detects_build_change(self):
        root = SOURCE.parent
        function = next(
            n
            for n in ast.parse((root / 'check.py').read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == 'native_build_inputs'
        )
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name in ('Dockerfile', 'check.py', 'native-build-lock.json', 'bootstrap.lock', 'build-tools.lock'):
                (work / name).write_bytes((root / name).read_bytes())
            namespace = {
                'HERE': work,
                'ast': ast,
                'hashlib': hashlib,
                'sha': lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
                'require': native.require,
            }
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(root / 'check.py'), 'exec'), namespace)
            fingerprint = namespace['native_build_inputs']
            expected = json.loads((root / 'source-lock.json').read_text())['nativeWheel']['buildInputs']
            self.assertEqual(fingerprint(), expected)
            recipe = work / 'Dockerfile'
            recipe.write_text(recipe.read_text() + '\n# runtime-only edit\n')
            self.assertEqual(fingerprint(), expected)
            checker = work / 'check.py'
            checker.write_text(checker.read_text().replace("PYTHONHASHSEED='0'", "PYTHONHASHSEED='1'"))
            self.assertNotEqual(fingerprint(), expected)

    def test_closure_requires_exact_mapped_provider(self):
        inventory = {
            '/fixture/module.so': {'sha256': 'a', 'needed': 'libfixture.so', 'soname': '-'},
            '/fixture/libfixture.so': {'sha256': 'b', 'needed': '-', 'soname': 'libfixture.so'},
        }
        maps = {p: {'sha256': row['sha256']} for p, row in inventory.items()}
        self.assertEqual(native.closure(['/fixture/module.so'], maps, inventory), set(inventory))
        del maps['/fixture/libfixture.so']
        with self.assertRaisesRegex(RuntimeError, 'Missing or ambiguous'):
            native.closure(['/fixture/module.so'], maps, inventory)
        maps['/fixture/libfixture.so'] = {'sha256': 'changed'}
        with self.assertRaisesRegex(RuntimeError, 'changed mapped'):
            native.closure(['/fixture/module.so'], maps, inventory)

    def test_ambiguous_or_unknown_provider_is_rejected(self):
        inv = {
            '/fixture/module.so': {'sha256': 'a', 'needed': 'libfixture.so', 'soname': '-'},
            '/fixture/one.so': {'sha256': 'b', 'needed': '-', 'soname': 'libfixture.so'},
            '/fixture/two.so': {'sha256': 'c', 'needed': '-', 'soname': 'libfixture.so'},
        }
        maps = {p: {'sha256': r['sha256']} for p, r in inv.items()}
        with self.assertRaisesRegex(RuntimeError, 'Missing or ambiguous'):
            native.closure(['/fixture/module.so'], maps, inv)
        maps['/fixture/unknown.so'] = {'sha256': 'unknown'}
        with self.assertRaisesRegex(RuntimeError, 'Unknown or changed'):
            native.closure(['/fixture/module.so'], maps, inv)

    def test_standalone_limit_preserves_failure_and_hashes(self):
        row = native.output_record(subprocess.CompletedProcess([], 127, b'', b'x' * 65537))
        self.assertFalse(row['passed'])
        self.assertEqual(row['stderrBytes'], 65537)
        self.assertEqual(row['exitCode'], 127)
        self.assertEqual(len(row['stderrSha256']), 64)
        self.assertNotIn('stderr', row)
        self.assertFalse(native.output_record(subprocess.CompletedProcess([], 0, b'', b'Error relocating'))['passed'])

    def test_host_refuses_loader_environment_before_import(self):
        fake_sys = types.SimpleNamespace(
            flags=types.SimpleNamespace(isolated=1), getdlopenflags=lambda: native.os.RTLD_NOW
        )
        with (
            patch.object(native, 'sys', fake_sys),
            patch.dict(native.os.environ, {'LD_LIBRARY_PATH': '/fixture'}, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected loader'):
                native.python_host('psycopg2')


if __name__ == '__main__':
    unittest.main()
