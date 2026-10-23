# Runtime installer source route

Open WebUI's Function/Tool dependency installer requires pip. This local derivative preserves that API and replaces its bundled urllib3 with 2.8.0 and msgpack with 1.2.1. It retains pip's upstream vendoring patches, regenerated CycloneDX SBOM, the setuptools 70.3.0 `pkg_resources` subset and all upstream/vendor licenses. Compiler and application bootstrap stages use their original pinned installer inputs.

`runtime-installer.json` defines immutable source/artifact URLs and hashes, the build-tool closure, vendor inputs, retained licenses and whole-wheel output hashes. `runtime-vendor.txt` uses pip's native vendoring/debug format. `runtime-installer-tools.lock` admits only hash-pinned wheels; the source route requires no personal directories or prebuilt local binaries.

From the repository root:

```sh
docker buildx build --platform linux/arm64 -f docker/open-webui/Dockerfile.runtime-installer \
  --target runtime-installer-export --output type=local,dest=runtime-installer-output .
```

Use an externally bounded single-worker builder. The recipe fetches exact source/tool/vendor artifacts and admits build-only Git through normal Alpine signature verification. Complete APK inventories enforce the reviewed base and exact build-only additions, including checksums, versions and architectures; unexpected changes or removals fail before artifact building. Standard `vendoring sync -v` and PEP517 builds run offline. Pure Python msgpack uses the publisher-supported `MSGPACK_PUREPYTHON=1`. Different wheel bytes or incomplete licenses, SBOM or RECORD fail. Outputs contain only the wheels, generated installation-file inventory and build-only APK admission records.

## Helper commands

All directory arguments are required; fetching and building are explicit operations:

```text
python runtime_installer.py help|--help|-h
python runtime_installer.py fetch INPUTS
python -I -B runtime_installer.py admit-apks ADMISSION_DIRECTORY
/opt/tools/bin/python -I runtime_installer.py build INPUTS OUTPUT
python -I -B runtime_installer.py verify INSTALLED_FILES_JSON
```

Help stops before any manifest read, file action or network call. `fetch` requires a new directory and downloads only admitted HTTPS artifacts; it never resolves dependencies. `admit-apks` reads complete APK databases and Git version, enforces the manifest's base/additions and records the checked diff. `build` requires the exact isolated CPython 3.11.17 musl arm64 tool environment, disabled networking and a new output directory.

`verify` requires an isolated `/usr/local` or `/opt/venv` interpreter with bytecode writes disabled (`-I -B`), the exact generated wheel-file inventory and the installed derivative. Every `.pyc`/`.pyo` in the admitted pip tree fails, including sourceless modules and caches beside unchanged source. External operations have bounded timeouts and authored error categories. The enclosing builder must enforce memory, CPU, wall-time, disk, network-ingress and output limits. No credential arguments or approval prompts are part of the helper.

## Application integration

The main Dockerfile includes the same public installer stage/helper and exact manifest. Build-only Git/tool Python prefixes and APK libraries are never copied into the runtime. Runtime vendoring does not change compiler/bootstrap inputs.

After installing application dependencies, the recipe installs the exact derivative wheel into both `/usr/local` and `/opt/venv`. A read-only BuildKit stage mount supplies installation inputs without an extra staging copy in the runtime. Both installers use `python -I -B -m pip install --no-index --no-deps --no-compile` after a whole-wheel hash check. Normal pip replacement removes its own old RECORD-managed files; unexpected files must fail admission rather than be erased. Source, licenses, SBOM and metadata remain intact.

The same runtime-only stage repairs the base standard library's ensurepip bootstrap payloads. It admits the exact original source and two complete bundled wheels, changes only the pip/setuptools version constants, and replaces those complete wheels with the same pip derivative and pinned setuptools84.2.1. Module hashes, wheel metadata, the complete outer RECORD (including nested vendor RECORD files) and licenses are verified. The standalone installer export includes this unchanged setuptools wheel. Compiler/bootstrap stages retain their original inputs.

The final image also bootstraps a fresh disposable virtual environment through ensurepip with networking disabled. It checks actual package versions, every installed wheel source/metadata/license byte, repaired urllib3/msgpack and `pip check`. Ordinary ensurepip compilation may create caches inside the disposable environment; the fixture accounts for those separately and removes that environment afterward. Both original runtime prefixes and the strict ensurepip source/bundle inventories are checked again. ensurepip's ordinary behavior is retained.

The compiler's `tools()` verifier applies before runtime pip replacement. The separate runtime verifier then checks both prefixes against `/usr/share/open-webui-derivative/runtime-installer-files.json`:

```sh
/usr/local/bin/python -I -B /checks/runtime_installer.py verify /usr/share/open-webui-derivative/runtime-installer-files.json
/opt/venv/bin/python -I -B /checks/runtime_installer.py verify /usr/share/open-webui-derivative/runtime-installer-files.json
/usr/local/bin/python -I -B -m unittest discover -s /checks -p test_runtime_installer.py
/opt/venv/bin/python -I -B -m unittest discover -s /checks -p test_runtime_installer.py
/usr/local/bin/python -I -B -m pip check
/opt/venv/bin/python -I -B -m pip check
```

Unittest discovery makes `/checks` importable explicitly; no `PYTHONPATH` or loader-variable overrides are needed. `-I` ignores Python environment settings, so admitted-tree verifier/fixture/pip processes and fixture children also require `-B`.

The default final image requires native-validator boundary tests, complete installed ELF/linkage checks in their actual Python/Bash hosts, production authentication fixtures and offline application imports. `test_dependency_runtime.py` checks Pillow, MCP and application/vendored urllib3 behavior in the application interpreter; its vendored HTTP fixture also runs in the other prefix. Both installer inventories are checked again afterward, rejecting caches and shadow modules introduced by any other check.

## Authored-code quality checks

The main Dockerfile's `quality` target installs `bootstrap.lock` and `quality-tools.lock` into `/opt/quality`. Its separate application interpreter uses the actual complete musl dependency graph plus the maintained matching stubs in `quality-stubs.lock`. The default runtime target copies only the quality marker from that stage and the original application prefix from `dependencies`; quality tools and stubs never enter the shipped runtime.

Run the reproducible check target from the repository root:

```sh
docker buildx build --platform linux/arm64 --target quality \
  -f docker/open-webui/Dockerfile .
```

The target executes these commands from `/workspace`, where the canonical sources are available at `docker/open-webui`:

```sh
/opt/quality/bin/ruff format --check --no-cache --config docker/open-webui/ruff.toml docker/open-webui
/opt/quality/bin/ruff check --no-cache --config docker/open-webui/ruff.toml docker/open-webui
/opt/quality/bin/python -I -B -m mypy --config-file docker/open-webui/mypy.ini
/opt/quality/bin/python -I -B -m mypy --config-file docker/open-webui/mypy.ini \
  --python-executable /opt/venv/bin/python docker/open-webui/check.py docker/open-webui/native_check.py
```

Ruff uses the pinned upstream profile for all authored Python. Mypy is strict for the installer and its standard-library fixtures; existing checker bodies use the separate configured profile and actual target-package typing context. A scoped typing check does not establish full strict coverage or runtime/security compatibility. Both quality and runtime fixtures execute as required dependencies of the default image build, including CI's ordinary `docker compose build openwebui`.

## Dependency and scanner boundaries

The derivative changes five upstream application pins: cryptography 50.0.2, PyJWT 2.15.1, aiohttp 3.14.4, MCP 1.30.0 and Pillow 13.0.0. `requirements-musl.lock` and `dependency-musl-lock.json` bind the complete target graph and wheel hashes to those inputs. `history/four-pin-basis/` preserves the original source, patch and resolved graph as technical provenance.

The RE2 artifact requires independent matching builds and complete source/toolchain provenance. Compilation inputs are bound separately from later runtime-only checks; historical whole-recipe hashes remain unchanged. Dependency locking and native artifact reproducibility do not establish application behavior or image security.

Raw all-severity vulnerability and secret reports remain required. This helper neither ignores advisories nor emits VEX. Its SBOM truthfully retains setuptools 70.3.0 metadata. Any narrow false-positive classification must bind the actual archive/config/rootfs, both Python prefixes, exact wheel-derived inventory, every metadata occurrence, retained subset and absence of vulnerable modules. New packages, shadow distributions, modules, versions or hashes fail admission. Genuine findings block adoption until repaired and checked in the actual final image.
