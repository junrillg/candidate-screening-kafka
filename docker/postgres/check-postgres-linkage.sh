#!/bin/sh
set -eu
case "${1:-}" in
    help|--help|-h)
        echo 'Usage: check-postgres-linkage.sh [absolute-install-prefix]'
        echo 'Inspect every installed ELF; require crypto/ICU/compression and reject XML/XSLT or missing libraries.'
        exit 0 ;;
esac
prefix=${1:-/usr/local}
case "$prefix" in /*) ;; *) echo 'Expected absolute install prefix' >&2; exit 2 ;; esac
umask 077
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM

# Format fields are documented by pax-utils; omit mutually exclusive -n with -F.
scanelf --recursive --nobanner --format '%F;%n' "$prefix" > "$scratch/needed"
test -s "$scratch/needed"
if grep -E 'libxml2|libxslt|libexslt|libLLVM|libperl|libpython|libtcl' "$scratch/needed" >/dev/null; then
    echo 'Unexpected optional runtime library dependency' >&2
    exit 1
fi
for required in libssl.so.3 libcrypto.so.3 libicuuc.so liblz4.so libzstd.so libz.so; do
    grep -F "$required" "$scratch/needed" >/dev/null || {
        echo 'Required crypto, ICU or compression dependency absent' >&2
        exit 1
    }
done
nm -D --defined-only "$prefix/bin/postgres" | awk 'NF >= 3 { print $NF }' | sort -u > "$scratch/exports"
test -s "$scratch/exports"
report="$prefix/share/postgresql/build-linkage.txt"
mkdir -p "$(dirname "$report")"
cp "$scratch/needed" "$report"
count=0
while IFS=';' read -r elf needed; do
    test -f "$elf"
    count=$((count + 1))
    case "$needed" in
        ''|'-')
            printf 'STATIC %s\n' "$elf" >> "$report"
            continue ;;
    esac
    status=0
    ldd "$elf" > "$scratch/ldd" 2>&1 || status=$?
    printf '\nLDD %s\n' "$elf" >> "$report"
    cat "$scratch/ldd" >> "$report"
    if grep -E 'libxml2|libxslt|libexslt|Error loading shared library|No such file or directory' "$scratch/ldd" >/dev/null; then
        echo 'Missing or unexpected shared library in installed ELF tree' >&2
        exit 1
    fi
    if [ "$status" -ne 0 ]; then
        # Loadable PostgreSQL modules resolve server API symbols in their host.
        # Each such symbol must actually be exported by THIS postgres binary.
        case "$elf" in
            "$prefix"/lib/postgresql/*.so) ;;
            *) echo 'Unexpected executable/shared-library ldd failure' >&2; exit 1 ;;
        esac
        grep '^Error' "$scratch/ldd" > "$scratch/errors" || {
            echo 'Unclassified ldd failure' >&2
            exit 1
        }
        while IFS= read -r error; do
            symbol=$(printf '%s\n' "$error" | sed -n 's/^Error relocating .*: \([^:]*\): symbol not found$/\1/p')
            if [ -z "$symbol" ] || ! grep -Fx -- "$symbol" "$scratch/exports" >/dev/null; then
                echo 'Unresolved module symbol is not a postgres host export' >&2
                exit 1
            fi
        done < "$scratch/errors"
        printf 'HOST-EXPORTS-RESOLVED %s\n' "$elf" >> "$report"
    fi
done < "$scratch/needed"
printf '\nInspected ELF files: %s\n' "$count" >> "$report"
chmod 644 "$report"
printf 'Installed ELF dependencies checked: %s\n' "$count"
