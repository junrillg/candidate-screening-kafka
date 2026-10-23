"""Validate installed ELF objects in their real eager Python/Bash hosts."""

import hashlib
import importlib
import json
import os
import selectors
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOTS = ['/bin', '/sbin', '/lib', '/usr/bin', '/usr/sbin', '/usr/lib', '/usr/local', '/opt/venv']
CLEAN = {'PATH': '/opt/venv/bin:/usr/local/bin:/usr/bin:/bin'}
# CPython's installed Makefile retains this exact pinned-base object for linking an interpreter.
LINKER_INPUTS = {
    '/usr/local/lib/python3.11/config-3.11-aarch64-linux-musl/python.o': (
        'ea5a0583fd58289e9d7be78291167cadef94262b03f57cee7f6fc32a987441d0'
    )
}


class NativeFailure(RuntimeError):
    def __init__(self, category):
        super().__init__(category)
        self.category = category


def require(value, reason):
    if not value:
        raise NativeFailure(reason)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def elf_kind(path, header, needed, soname, digest):
    require(
        len(header) >= 20
        and header[:4] == b'\x7fELF'
        and header[4] == 2
        and header[5] == 1
        and int.from_bytes(header[18:20], 'little') == 183,
        'Wrong runtime ELF target',
    )
    kind = int.from_bytes(header[16:18], 'little')
    if kind == 1:
        require(
            LINKER_INPUTS.get(path) == digest and needed in ('', '-') and soname in ('', '-'),
            'Unreviewed relocatable linker input',
        )
    else:
        require(kind in (2, 3), 'Unexpected ELF object type')
    return kind


def mapped(text):
    result = {}
    for line in text.splitlines():
        fields = line.split(maxsplit=5)
        require(len(fields) >= 5, 'Invalid mapping')
        if len(fields) == 5:
            require(fields[4] == '0', 'Unnamed file mapping')
            continue
        if fields[5] in {'[heap]', '[stack]', '[vvar]', '[vvar_vclock]', '[vdso]'}:
            continue
        path = Path(fields[5])
        require(path.is_absolute(), 'Invalid mapped path')
        stat = path.stat()
        major, minor = fields[3].split(':')
        require(
            stat.st_ino == int(fields[4])
            and os.major(stat.st_dev) == int(major, 16)
            and os.minor(stat.st_dev) == int(minor, 16),
            'Mapped inode changed',
        )
        result[str(path.resolve())] = {'sha256': sha(path), 'inode': stat.st_ino}
    require(result, 'No mapped files')
    return result


def python_host(mode):
    require(sys.flags.isolated and sys.getdlopenflags() == os.RTLD_NOW, 'Normal isolated eager host required')
    require(
        not {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH'}.intersection(os.environ),
        'Unexpected loader environment',
    )
    extensions: list[ModuleType]
    if mode == 'numpy':
        import numpy as np
        import numpy.linalg._umath_linalg as linalg

        core = importlib.import_module('numpy._core._multiarray_umath')
        require(np.__version__ == '2.4.6', 'Wrong NumPy')
        require(
            np.allclose(
                np.array([[1.0, 2.0], [3.0, 4.0]]) @ np.array([[2.0, 0.0], [1.0, 2.0]]),
                [[4.0, 4.0], [10.0, 8.0]],
                rtol=0,
                atol=1e-12,
            ),
            'Native matrix operation failed',
        )
        require(
            np.allclose(np.linalg.solve([[3.0, 1.0], [1.0, 2.0]], [9.0, 8.0]), [2.0, 3.0], rtol=0, atol=1e-12),
            'Native solve failed',
        )
        extensions = [core, linalg]
    elif mode == 'psycopg2':
        import psycopg2
        import psycopg2._psycopg as psycopg2_extension
        from psycopg2 import extensions as adapters

        version = vars(psycopg2).get('__version__')
        require(
            isinstance(version, str)
            and version.split()[0] == '2.9.12'
            and adapters.libpq_version() > 0
            and adapters.adapt(42).getquoted() == b'42',
            'Psycopg2 native operation failed',
        )
        extensions = [psycopg2_extension]
    elif mode == 'psycopg3':
        import psycopg
        import psycopg_binary._psycopg as adaptation
        from psycopg import pq

        psycopg3_extension = importlib.import_module('psycopg_binary.pq')
        require(
            psycopg.__version__ == '3.3.4' and pq.__impl__ == 'binary' and pq.version() > 0,
            'Psycopg3 binary operation failed',
        )
        extensions = [psycopg3_extension, adaptation]
    elif mode == 'tkinter':
        import _tkinter as tkinter_extension
        import tkinter

        interpreter = tkinter.Tcl()
        require(
            interpreter.eval('expr {6 * 7}') == '42'
            and interpreter.call('info', 'patchlevel') == '8.6.17'
            and not interpreter.call('info', 'exists', 'tk_version'),
            'Headless Tcl operation failed',
        )
        extensions = [tkinter_extension]
    else:
        raise RuntimeError('Unknown native host')
    require(sys.getdlopenflags() == os.RTLD_NOW, 'Host binding flags changed')
    roots = []
    for module in extensions:
        if module.__file__ is None:
            raise NativeFailure('Native module file absent')
        roots.append(str(Path(module.__file__).resolve()))
    return {
        'roots': roots,
        'maps': mapped(Path('/proc/self/maps').read_text()),
        'dlopenFlags': sys.getdlopenflags(),
    }


def closure(roots, maps, inventory):
    require(
        maps and all(path in inventory and row['sha256'] == inventory[path]['sha256'] for path, row in maps.items()),
        'Unknown or changed mapped file',
    )
    seen, pending = set(), list(roots)
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        require(path in maps and path in inventory, 'Unmapped native dependency')
        seen.add(path)
        for name in inventory[path]['needed'].split(','):
            if name in {'', '-'}:
                continue
            providers = {p for p in maps if Path(p).name == name or inventory[p]['soname'] == name}
            for directory in ('/lib', '/usr/lib', '/usr/local/lib'):
                alias = Path(directory) / name
                if alias.exists() and str(alias.resolve()) in maps:
                    providers.add(str(alias.resolve()))
            require(len(providers) == 1, 'Missing or ambiguous mapped provider')
            pending.extend(providers)
    return seen


def output_record(result):
    stdout, stderr = result.stdout, result.stderr
    row = {
        'exitCode': result.returncode,
        'stdoutBytes': len(stdout),
        'stderrBytes': len(stderr),
        'stdoutSha256': hashlib.sha256(stdout).hexdigest(),
        'stderrSha256': hashlib.sha256(stderr).hexdigest(),
    }
    bounded = len(stdout) + len(stderr) <= 65536 and all(c in (9, 10, 13) or 32 <= c < 127 for c in stdout + stderr)
    row['passed'] = (
        bounded and result.returncode == 0 and b'Error ' not in stdout + stderr and b'not found' not in stdout + stderr
    )
    if bounded:
        row.update(stdout=stdout.decode(), stderr=stderr.decode())
    return row


def bash_hosts(paths, inventory):
    eager = {}
    for path in paths:
        require(Path(path).parent == Path('/usr/lib/bash') and Path(path).name.isidentifier(), 'Invalid Bash module')
        row = output_record(
            subprocess.run(
                ['/lib/ld-musl-aarch64.so.1', '--preload', path, '--list', '/bin/bash'],
                env=CLEAN,
                capture_output=True,
                timeout=10,
            )
        )
        eager[path] = row
        require(row['passed'], 'Bash eager relocation failed')
    # This module exports enable_mypid_struct rather than mypid_struct in the pinned Bash APK.
    names = {p: {'mypid': 'enable_mypid'}.get(Path(p).name, Path(p).name) for p in paths}
    script = (
        'set -e\n'
        'while (( "$#" )); do file="$1"; name="$2"; shift 2; enable -f "$file" "$name"; done\n'
        'builtin printf "ready\\n"\n'
        'IFS= builtin read -r acknowledgement\n'
        '[[ "$acknowledgement" == done ]]\n'
    )
    child = subprocess.Popen(
        [
            '/bin/bash',
            '--noprofile',
            '--norc',
            '-c',
            script,
            'native-check',
            *[item for p in paths for item in (p, names[p])],
        ],
        env=CLEAN,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with selectors.DefaultSelector() as selector:
            pipe = child.stdout
            if pipe is None:
                raise NativeFailure('Bash output pipe absent')
            selector.register(pipe, selectors.EVENT_READ)
            require(
                selector.select(timeout=10) and pipe.readline() == b'ready\n' and child.poll() is None,
                'Bash registration failed',
            )
        require(Path('/proc/' + str(child.pid) + '/exe').resolve() == Path('/bin/bash').resolve(), 'Wrong Bash host')
        maps = mapped(Path('/proc/' + str(child.pid) + '/maps').read_text())
        proven = closure(['/bin/bash', *paths], maps, inventory)
        stdout, stderr = child.communicate(input=b'done\n', timeout=10)
        require(child.returncode == 0 and not stdout and not stderr, 'Bash host failed')
        return proven, {'eager': eager, 'builtinNames': names, 'maps': maps}
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def installed_elf_files() -> set[str]:
    found: set[str] = set()
    for directory in ROOTS:
        for base, _, files in os.walk(directory):
            for name in files:
                path = Path(base) / name
                if path.is_file():
                    with path.open('rb') as stream:
                        if stream.read(4) == b'\x7fELF':
                            found.add(str(path.resolve()))
    return found


def validate():
    found = installed_elf_files()
    result = subprocess.run(
        ['scanelf', '--recursive', '--nobanner', '--format', '%F;%n;%S', *ROOTS],
        capture_output=True,
        text=True,
        timeout=60,
    )
    require(result.returncode == 0 and not result.stderr, 'Native ELF enumeration failed')
    inventory = {}
    for line in result.stdout.splitlines():
        path, needed, soname = line.split(';')
        path = str(Path(path).resolve())
        with Path(path).open('rb') as stream:
            header = stream.read(20)
        digest = sha(path)
        kind = elf_kind(path, header, needed, soname, digest)
        inventory[path] = {
            'path': path,
            'sha256': digest,
            'needed': needed,
            'soname': soname,
            'elfType': kind,
            'role': 'linker-input' if kind == 1 else 'loadable',
        }
    require(found and set(inventory) == found, 'Incomplete installed ELF inventory')
    needed = ','.join(row['needed'] for row in inventory.values())
    require(
        all(
            name in needed for name in ('libssl.so.3', 'libcrypto.so.3', 'libre2.so.11', 'libicuuc.so.78', 'libz.so.1')
        ),
        'Required native dependency absent',
    )
    failed = set()
    for path, row in inventory.items():
        if row['needed'] not in ('', '-') or row['elfType'] == 3:
            env = dict(CLEAN)
            if path.endswith('.so') and (path.startswith('/opt/venv/') or '/python3.11/lib-dynload/' in path):
                env['LD_PRELOAD'] = '/usr/local/lib/libpython3.11.so.1.0'
                row['pythonHostSha256'] = sha(env['LD_PRELOAD'])
            row['standalone'] = output_record(subprocess.run(['ldd', path], env=env, capture_output=True, timeout=10))
            if not row['standalone']['passed']:
                failed.add(path)
    proven, hosts = set(), {}
    for mode in ('numpy', 'psycopg2', 'psycopg3', 'tkinter'):
        child = subprocess.run(
            [sys.executable, '-I', '-B', str(Path(__file__).resolve()), mode],
            env=CLEAN,
            capture_output=True,
            timeout=40,
        )
        require(child.returncode == 0 and not child.stderr and len(child.stdout) <= 65536, 'Native Python host failed')
        host = json.loads(child.stdout)
        require(host['dlopenFlags'] == os.RTLD_NOW and host['roots'], 'Host proof missing')
        files = closure(host['roots'], host['maps'], inventory)
        proven.update(files)
        hosts[mode] = {**host, 'closure': sorted(files)}
    bash = sorted(p for p in failed if Path(p).parent == Path('/usr/lib/bash'))
    if bash:
        files, hosts['bash'] = bash_hosts(bash, inventory)
        proven.update(files)
    evidence = {
        'inventory': inventory,
        'hosts': hosts,
        'standaloneFailures': sorted(failed),
        'unresolved': sorted(failed - proven),
    }
    Path('/usr/share/open-webui-derivative/native-linkage.json').write_text(
        json.dumps(evidence, sort_keys=True, indent=2) + '\n'
    )
    require(not evidence['unresolved'], 'Unresolved installed ELF dependency')


if __name__ == '__main__':
    print(json.dumps(python_host(sys.argv[1]), sort_keys=True))
