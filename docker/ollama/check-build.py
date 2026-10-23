#!/usr/bin/env python3
"""Fail if pinned source, patched dependency resolution or Go build identity drifts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

HERE = Path("/build")
LOCK = json.loads((HERE / "source-lock.json").read_text())


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if sys.argv[1:] == ["source"]:
    require(digest(HERE / "source/go.mod") == LOCK["upstream"]["originalGoModSha256"], "Upstream go.mod mismatch")
    require(digest(HERE / "source/go.sum") == LOCK["upstream"]["originalGoSumSha256"], "Upstream go.sum mismatch")
    require(digest(HERE / "security-dependencies.patch") == LOCK["patchSha256"], "Dependency patch mismatch")
elif sys.argv[1:] in (["graph"], ["binary"]):
    for name in ("go.mod", "go.sum"):
        require(digest(HERE / "source" / name) == LOCK["patchedModules"][name + "Sha256"], "Locked patched module file changed: " + name)
    text = Path("/out/modules.json").read_text()
    decoder = json.JSONDecoder()
    modules = {}
    while text.strip():
        item, end = decoder.raw_decode(text.lstrip())
        text = text.lstrip()[end:]
        require("Error" not in item and "Replace" not in item, "Unexpected module error or replacement")
        modules[item["Path"]] = item
    for name, version in LOCK["dependencies"].items():
        require(modules.get(name, {}).get("Version") == version, "Unexpected resolved dependency: " + name)
    if sys.argv[1:] == ["graph"]:
        raise SystemExit(0)
    info = Path("/out/build-info.txt").read_text()
    require(info.splitlines()[0].endswith("go" + LOCK["go"]["version"]), "Go build version mismatch")
    require("CGO_ENABLED=1" in info and "-buildmode=pie" in info, "Native build settings mismatch")
    for name in ("github.com/buger/jsonparser", "golang.org/x/crypto", "golang.org/x/image", "golang.org/x/mod", "golang.org/x/net", "golang.org/x/text"):
        pattern = r"^\s*dep\s+" + re.escape(name) + r"\s+" + re.escape(LOCK["dependencies"][name]) + r"\s"
        require(re.search(pattern, info, re.MULTILINE), "Missing patched binary metadata: " + name)
    def symbols(path, undefined):
        output = subprocess.check_output(["readelf", "--dyn-syms", "--wide", str(path)], text=True)
        found = set()
        for line in output.splitlines():
            if (" UND " in line) == undefined:
                match = re.search(r" ([\w.$]+)@{1,2}((?:GLIBC|GLIBCXX|CXXABI|GCC)_[0-9.]+)", line)
                if match:
                    found.add(match.groups())
        return found

    needed = symbols(Path("/out/ollama"), True)
    runtime_libraries = sorted((HERE / "runtime-libs").iterdir())
    require(runtime_libraries, "No retained runtime libraries captured")
    provided = set().union(*(symbols(path, False) for path in runtime_libraries))
    require(needed, "No GLIBC symbol requirements found")
    def check_symbol_compatibility(required, available):
        missing = required - available
        require(not missing, "Retained runtime libraries lack required versioned symbols: " + repr(sorted(missing)))
        return missing

    missing = check_symbol_compatibility(needed, provided)
    sentinel = ("__ollama_missing_symbol_negative_fixture", "GLIBC_999.0")
    require(sentinel not in provided, "Negative fixture unexpectedly exists")
    try:
        check_symbol_compatibility(needed | {sentinel}, provided)
    except SystemExit:
        negative_check = "rejected missing symbol and version"
    else:
        raise SystemExit("Missing-symbol negative check did not reject")
    def elf_metadata(path):
        program = subprocess.check_output(["readelf", "--program-headers", "--wide", str(path)], text=True)
        dynamic = subprocess.check_output(["readelf", "--dynamic", "--wide", str(path)], text=True)
        interpreter = re.search(r"Requesting program interpreter: ([^\]]+)", program)
        require(interpreter, "ELF interpreter missing")
        return {"interpreter": interpreter.group(1),
                "needed": re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dynamic)}
    original_elf = elf_metadata(HERE / "original-ollama")
    application_elf = elf_metadata(Path("/out/ollama"))
    require(application_elf["interpreter"] == original_elf["interpreter"], "ELF interpreter differs from retained runtime")
    require(set(application_elf["needed"]) <= {path.name for path in runtime_libraries}, "New DT_NEEDED entry absent from retained closure")
    Path("/out/elf-versions.json").write_text(json.dumps({
        "runtimeLibcSha256": digest(HERE / "runtime-libc.so.6"),
        "runtimeGlibc": (HERE / "runtime-glibc.txt").read_text().strip(),
        "runtimeLibraries": {path.name: digest(path) for path in runtime_libraries},
        "requiredVersionedSymbols": sorted(needed), "missing": sorted(missing),
        "originalElf": original_elf, "applicationElf": application_elf,
        "negativeCheck": negative_check,
    }, indent=2) + "\n")
    binary_hash = digest(Path("/out/ollama"))
    def package(identifier, name, version):
        return {"SPDXID": identifier, "name": name, "versionInfo": version,
                "downloadLocation": "NOASSERTION", "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION", "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION"}
    application = package("SPDXRef-Application", "ollama", "0.40.6-local.1")
    application["checksums"] = [{"algorithm": "SHA256", "checksumValue": binary_hash}]
    packages = [application, package("SPDXRef-Go", "Go standard library", LOCK["go"]["version"])]
    for line in info.splitlines():
        fields = line.split()
        if fields and fields[0] == "dep":
            name, version = fields[1:3]
            item = package("SPDXRef-Module-" + str(len(packages)), name, version)
            item["externalRefs"] = [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl",
                                    "referenceLocator": "pkg:golang/" + quote(name, safe="/") + "@" + quote(version, safe="")}]
            packages.append(item)
    document = {
        "spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
        "name": "Ollama Go application dependency inventory",
        "documentNamespace": "https://spdx.org/spdxdocs/ollama-" + binary_hash,
        "creationInfo": {"creators": ["Tool: ollama-check-build.py"],
                         "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "packages": packages,
        "relationships": [{"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES",
                           "relatedSpdxElement": "SPDXRef-Application"}] +
                         [{"spdxElementId": "SPDXRef-Application", "relationshipType": "DEPENDS_ON",
                           "relatedSpdxElement": item["SPDXID"]} for item in packages[1:]],
    }
    Path("/out/application.spdx.json").write_text(json.dumps(document, indent=2) + "\n")
else:
    raise SystemExit("Usage: check-build.py source|graph|binary")
