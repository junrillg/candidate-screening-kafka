#!/bin/sh
set -eu
case "${1:-}" in
    [Hh][Ee][Ll][Pp]|--[Hh][Ee][Ll][Pp]|-[Hh])
        echo 'Usage: scripts/kafka-cli.sh kafka-topics|kafka-console-producer|kafka-console-consumer [native Kafka arguments]'
        echo 'Runs one bounded, disposable CLI container without sharing broker memory.'
        exit 0 ;;
    kafka-topics|kafka-console-producer|kafka-console-consumer) tool=$1; shift ;;
    *) echo 'Expected a supported Kafka tool or --help' >&2; exit 2 ;;
esac
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$root"
exec docker compose run --rm --no-deps --pull never -T \
    --env 'KAFKA_HEAP_OPTS=-Xms16m -Xmx64m' \
    --env 'KAFKA_JVM_PERFORMANCE_OPTS=-XX:ActiveProcessorCount=1 -XX:MaxMetaspaceSize=64m -XX:ReservedCodeCacheSize=16m -XX:MaxDirectMemorySize=16m' \
    --entrypoint "/opt/kafka/bin/$tool.sh" kafka-init "$@"
