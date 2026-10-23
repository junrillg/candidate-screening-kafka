#!/bin/sh
set -eu
for topic in candidate-submitted screening-completed candidate-changed screening-failed; do
    /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --create --if-not-exists \
        --topic "$topic" --partitions 32 --replication-factor 1 \
        --config retention.ms=86400000 --config retention.bytes=8388608 \
        --config max.message.bytes=65536 --config segment.bytes=8388608
done
