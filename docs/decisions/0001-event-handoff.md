# ADR 0001: durable event handoff and browser status

**Status: PLANNED. Date: 2026-10-19.** Ollama 0.40.6 with `qwen2.5:1.5b-instruct` is selected locally; a narrow provider boundary permits a planned Bedrock adapter/configuration reference without AWS calls. This decision defines transport and durable state ownership independently of inference.

## Context

Candidate submission must be accepted without awaiting screening. Work and its eventual status must survive process restart. Kafka and two .NET services are explicit requirements; an SSE connection cannot be the source of durable truth.

## Decision

Each service commits its business state and outbox in one PostgreSQL transaction. A bounded relay publishes to Kafka and records confirmation; ambiguous failure may cause redelivery. The receiving service uses an inbox/event uniqueness constraint and a unique result/version to commit repeat-safe effects before advancing its consumer offset. This is at-least-once delivery with idempotent committed effects, not exactly-once execution.

Candidates owns submission/status/notifications; Screening owns validated evidence/results. Each uses its own database and role. Neither reads the other's tables. AI calls occur outside database transactions and receive only bounded synthetic input; untrusted output must pass source-evidence/schema checks before deterministic server scoring. The model does not make a hiring decision.

Completion consumption atomically updates terminal status, one owner-scoped durable notification and a change outbox. Kafka change signals wake each API replica's bounded owner channels. Authenticated SSE delivers minimal change signals; initial/reconnect database reconciliation closes delivery gaps. Register the subscription before bounded catch-up and drain queued changes afterward; duplicates cause no duplicate notice. No idle stream holds a database connection or performs periodic database reads.

The browser moves from acceptance to a processing route with navigation available. A completion may transition only the same currently viewed candidate/session. Elsewhere it adds an explicit result link without redirecting or stealing focus. An offline/closed browser recovers saved state on return; no email or OS push is implied.

## Alternatives

| Alternative | Why not selected |
| --- | --- |
| Database write followed by direct publish | A crash in the gap can lose accepted work; broker transactions do not atomically commit PostgreSQL state. |
| Direct API → worker HTTP | Worker latency/outage would become part of admission; no retained event/replay demonstration. |
| One consumer group shared by all SSE-serving replicas | Only one group member receives each event; other replicas with that owner connected can miss the wakeup. Per-replica wakeup groups deliberately trade broker fan-out for clear local routing. |
| WebSockets | Bidirectional messaging is unnecessary for this feature; owner authorization and durable reconciliation would still be required. |
| Periodic refresh | Repeated unchanged reads add unnecessary work; durable state reconciliation still covers missed live signals. |

## Consequences and verification

Outbox/inbox storage, retry/exhaustion operations, retained-event policy and consumer-group lifecycle add operational work. Fan-out and reconciliation follow the [delivery and recovery design](../design.md#delivery-and-recovery). Local persistence does not establish deployment failover.

Required tests cover ambiguous publication, duplicate events, concurrent HTTP replay, restart after result commit/before offset commit, rebalance, poison input, bounded exhaustion/manual recovery, off-route completion, slow-client disconnect and reconnect reconciliation. Owner/cookie/antiforgery and stale-session tests guard both HTTP and SSE boundaries. The complete flow is not yet verified.

Sources: [Kafka design](https://kafka.apache.org/43/design/design/), [Confluent .NET overview](https://docs.confluent.io/kafka-clients/dotnet/current/overview.html), [transactional outbox guidance](https://learn.microsoft.com/en-us/azure/architecture/databases/guide/transactional-out-box-cosmos), [SSE browser mechanics](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events). Accessed 2026-10-19; these support mechanisms, not this implementation's verification.
