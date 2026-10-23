#!/bin/sh
set -eu
case "${1:-check}" in
    help|--help|-h) echo 'Usage: docker/kafka/check-compatibility.sh [check] (creates a temporary topic and restarts the lab broker)'; exit 0 ;;
    check) ;;
    *) echo 'Expected check or help' >&2; exit 2 ;;
esac
umask 077
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
cd "$root"
mkdir -p .artifacts
scratch=$(mktemp -d "$root/.artifacts/kafka-compat.XXXXXX")
topic="lab-compat-$(date +%s)-$$"
cleanup() {
    "$root/scripts/kafka-cli.sh" kafka-topics --bootstrap-server kafka:9092 \
        --delete --if-exists --topic "$topic" >/dev/null 2>&1 || true
    rm -rf "$scratch"
}
trap cleanup EXIT HUP INT TERM
"$root/scripts/kafka-cli.sh" kafka-topics --bootstrap-server kafka:9092 \
    --create --topic "$topic" --partitions 1 --replication-factor 1
payload='{"kind":"compatibility","text":"Unicode ✓, quote \", nested JSON","version":1}'
for compression in none gzip snappy lz4 zstd; do
    printf '%s\n' "$payload" | "$root/scripts/kafka-cli.sh" kafka-console-producer \
        --bootstrap-server kafka:9092 --topic "$topic" --sync \
        --producer-property "compression.type=$compression" --producer-property acks=all
done
read_records() {
    "$root/scripts/kafka-cli.sh" kafka-console-consumer \
        --bootstrap-server kafka:9092 --topic "$topic" --from-beginning \
        --max-messages 5 --timeout-ms 20000 > "$scratch/actual"
    test "$(wc -l < "$scratch/actual" | tr -d ' ')" = 5
    while IFS= read -r actual; do
        test "$actual" = "$payload"
    done < "$scratch/actual"
}
read_records
docker compose restart kafka
attempt=0
until "$root/scripts/kafka-cli.sh" kafka-topics \
    --bootstrap-server kafka:9092 --list >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    test "$attempt" -lt 30 || { echo 'Broker did not recover' >&2; exit 1; }
    sleep 1
done
read_records
echo 'Kafka compression round trips and persisted records after restart passed'
