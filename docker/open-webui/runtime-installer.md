# Runtime installer source route

The application needs pip for its existing Function/Tool dependency installer. This local pip derivative keeps that API and replaces its bundled urllib3 with 2.8.0 and msgpack with 1.2.1. It retains pip's nine upstream vendoring patches, the complete regenerated CycloneDX SBOM, the setuptools 70.3.0 `pkg_resources` subset, and all upstream/vendor licenses. The compiler and application bootstrap continue to use their original pinned installer inputs.

`runtime-installer.json` supplies public immutable source/artifact URLs and hashes, the 23-package build tool closure, all 18 vendor inputs, exact retained licenses and independently reproduced whole-wheel output hashes. `runtime-vendor.txt` stays in the format consumed by pip's own vendoring/debug code. `runtime-installer-tools.lock` admits only hash-pinned wheels. No personal directory or locally built binary is an input to this route.

From the repository root, the standalone source route is:

```sh
docker buildx build --platform linux/arm64 -f docker/open-webui/Dockerfile.runtime-installer \
  --target runtime-installer-export --output type=local,dest=runtime-installer-output .
```

Run it in the same externally bounded single-worker builder used for the main image. It fetches exact source/tool/vendor artifacts, admits build-only Git with normal Alpine signature verification, records full before/after APK inventories and enforces the exact reviewed base and 12-package addition checksums/versions/architectures, then runs the standard `vendoring sync -v` and PEP517 build offline. Any base mutation/removal or unexpected tool delta fails before artifact building. Pure Python msgpack uses the publisher-supported `MSGPACK_PUREPYTHON=1`. The helper rejects different output wheel hashes and incomplete/changed licenses, SBOM or RECORD. The output includes only the two wheels, their generated installation file inventory and build-only APK admission records. This source route has not yet passed a new execution gate; the output hashes come from the earlier exact-input artifact proof.

Helper usage (all directory arguments required; no implicit fetch/build):

```text
python runtime_installer.py help|--help|-h
python runtime_installer.py fetch INPUTS
python -I -B runtime_installer.py admit-apks ADMISSION_DIRECTORY
/opt/tools/bin/python -I runtime_installer.py build INPUTS OUTPUT
python -I -B runtime_installer.py verify INSTALLED_FILES_JSON
```

Help stops before any manifest read or file/network action. `fetch` requires a new directory, downloads only admitted HTTPS artifacts and copies the two checked text inputs; it never resolves dependencies. `admit-apks` reads the two full APK databases and Git version from the required admission directory, enforces the pinned manifest's exact base/additions and writes the complete checked diff. `build` requires the exact isolated CPython 3.11.17 musl arm64 tool environment and network disabled; OUTPUT must be new. `verify` requires an isolated `/usr/local` or `/opt/venv` interpreter with bytecode writes disabled (`-I -B`), a generated exact wheel-file inventory and the installed derivative. It rejects every `.pyc`/`.pyo` in the admitted pip tree, including sourceless modules and caches alongside unchanged source. There are no credential arguments or approval prompts. Each failing external operation has a bounded timeout and the CLI prints authored categories. The enclosing runner must enforce the accepted cgroup, wall-time, disk, network-ingress and output budgets.

## Application integration contract

The main Dockerfile has not adopted this route yet. Its eventual integration must use this same public stage/helper and exact manifest, after `FROM base AS dependencies`, so no compiler recipe/bootstrap input is changed by runtime vendoring. Keep all source/hash/tool/SBOM/license checks mandatory. Do not copy the Git/tool stage's Python prefix or APK libraries into the runtime.

After the ordinary application dependencies have been installed and `/opt/venv` has been copied into runtime-files, install the exact derivative wheel into **both** `/usr/local` and `/opt/venv`. Use a BuildKit read-only mount from the admitted installer stage to avoid leaving wheel archives as extra scanner inputs. Both commands must use `python -I -B -m pip install --no-index --no-deps --no-compile`, use the exact mounted wheel and verify its whole-wheel hash immediately before installation. The normal pip upgrade removes its old RECORD-owned files/caches; do not erase unexpected files to make admission pass. Retain every source file, license, SBOM and metadata entry. Do not rerun the compiler's `tools()` verifier against the intentionally changed runtime pip; keep that verifier unchanged for the base/binding stages and call the separate runtime verifier in the final stage.

Copy only `installed-files.json` to `/usr/share/open-webui-derivative/runtime-installer-files.json`. For each interpreter execute:

```sh
/usr/local/bin/python -I -B /checks/runtime_installer.py verify /usr/share/open-webui-derivative/runtime-installer-files.json
/opt/venv/bin/python -I -B /checks/runtime_installer.py verify /usr/share/open-webui-derivative/runtime-installer-files.json
/usr/local/bin/python -I -B -m unittest discover -s /checks -p test_runtime_installer.py
/opt/venv/bin/python -I -B -m unittest discover -s /checks -p test_runtime_installer.py
/usr/local/bin/python -I -B -m pip check
/opt/venv/bin/python -I -B -m pip check
```

The standard unittest discovery calls make `/checks` importable explicitly. Do not add `PYTHONPATH` or alter loader variables. `-I` ignores Python environment settings, so explicit `-B` is required for verifier/fixture/pip processes that touch the admitted subtree, including fixture children. The final Docker default target must also retain `test_native_check.py`, the complete native ELF/host proof, auth fixtures and offline app import. Run `test_dependency_runtime.py` with `-I -B` in the application interpreter for Pillow 13/MCP 1.30/application and vendored urllib3 behavior; rerun its vendored HTTP fixture in the other prefix. Recheck both installer inventories after the other final Python gates so any unexpected cache or shadow module fails. These new fixtures are source-prepared and have not yet executed against the repaired image.

Formatter/static/type commands from the repository root are:

```sh
ruff format --check --config docker/open-webui/ruff.toml docker/open-webui
ruff check --config docker/open-webui/ruff.toml docker/open-webui
python -m mypy --config-file docker/open-webui/mypy.ini
python -m mypy --config-file docker/open-webui/mypy.ini docker/open-webui/check.py docker/open-webui/native_check.py
```

Use `quality-tools.lock` in an isolated CPython 3.11 musl arm64 tool environment. It pins upstream incumbent Ruff 0.15.5 and the proposed mypy 2.4 closure; installation and diagnostics remain pending. The mypy configuration is strict for the new installer and its standard-library fixtures. Existing application/native checker annotation debt and third-party fixture interfaces still require a separately reviewed check scope; a scoped pass must not be reported as full typing coverage. The upstream configured Ruff profile applies to all authored Python without broad ignores. CI must call these same gates, and final build tests must execute rather than merely ship.

## Scan caller boundary

Raw all-severity vulnerability and secret reports remain mandatory and unchanged. This helper does not classify/ignore advisories or emit VEX. The new pip wheel's SBOM still names setuptools 70.3.0 truthfully; any narrow false-positive admission must separately bind a candidate's actual archive/config/rootfs, both Python prefixes, exact wheel-derived file inventory, all metadata occurrences, retained subset and absence of vulnerable modules. A new package, shadow distribution, module, version or hash must fail admission. Native scanner VEX behavior and normal scan/CI integration need their own reviewed execution proof before any caller contract can be adopted. Real findings, including the application Pillow/MCP changes and pip's real urllib3/msgpack repair, remain blocking until the new checked candidate and fresh scan pass.
