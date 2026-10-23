# Technical sources and decisions

The proposed feature is candidate submission → AI-assisted evidence extraction → validated screening status. Synthetic narratives and explicit job requirements are the source material. These references support the design; they do not establish a verified application.

## Sources

| Primary reference | Application to this design |
| --- | --- |
| [.NET support policy](https://dotnet.microsoft.com/en-us/platform/support/policy/dotnet-core) and [release metadata](https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json) | Target the .NET 10 release family and pin the build toolchain separately from the host installation. |
| [ASP.NET Core practices](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/best-practices?view=aspnetcore-10.0) and [code analysis](https://learn.microsoft.com/en-us/dotnet/fundamentals/code-analysis/overview) | Async I/O, cancellation, correct service lifetimes, nullable types and analyzer enforcement. |
| [Antiforgery](https://learn.microsoft.com/en-us/aspnet/core/security/anti-request-forgery?view=aspnetcore-10.0) and [Data Protection](https://learn.microsoft.com/en-us/aspnet/core/security/data-protection/configuration/overview?view=aspnetcore-10.0) | Protect cookie-backed mutations explicitly and persist the key ring for restart compatibility. |
| [Rules of React](https://react.dev/reference/rules) and [strict TypeScript](https://www.typescriptlang.org/tsconfig/strict.html) | Pure rendering, effect cleanup, strict types and stale-response protection; runtime validation remains necessary. |
| [Vite documentation](https://vite.dev/guide/) | Build the React client separately from application type checking. |
| [Kafka design](https://kafka.apache.org/43/design/design/) | Retained events, partition ordering and independently managed consumer offsets. |
| [Confluent .NET client](https://docs.confluent.io/kafka-clients/dotnet/current/overview.html) | Confirm publication and advance business consumer offsets only after durable effects. |
| [Transactional outbox](https://learn.microsoft.com/en-us/azure/architecture/databases/guide/transactional-out-box-cosmos) | Commit state and dispatch intent atomically. The example uses Cosmos; this design applies the pattern using PostgreSQL transactions. |
| [Server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) | Browser change signals, reconnect and heartbeat behavior, combined with durable reconciliation. |
| [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) and [FAQ](https://docs.ollama.com/faq) | Request structured evidence while treating factual correctness as an application responsibility. |
| [Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) | A separate inference adapter behind the same evidence-validation boundary. |
| [AKHQ authentication](https://akhq.io/docs/configuration/authentifications/basic-auth.html) and [API roles](https://akhq.io/docs/configuration/authentifications/groups.html), [pgAdmin containers](https://www.pgadmin.org/docs/pgadmin4/latest/container_deployment.html) and [OpenObserve ingestion](https://openobserve.ai/docs/ingestion/logs/otlp/) | Authenticate inspection and enforce read-only broker permissions with a JWT signing secret. Inspect service-owned records and safe correlated logs. Viewer state does not prove business completion. |

## Design choices

| Choice | Alternative | Reason and tradeoff |
| --- | --- | --- |
| Separate Candidates API and Screening worker | Modular monolith | Makes ownership and asynchronous handoff explicit, with additional operational complexity. |
| Kafka | Direct worker HTTP | Acceptance remains independent of worker availability; retained events support replay. |
| Transactional outbox and inbox | Database write followed by direct publish | Closes the accepted-work handoff gap while allowing repeat delivery and deduplicated effects. |
| SSE with saved notifications | WebSockets or periodic refresh | The browser needs server-to-client signals; saved state covers missed signals and disconnected sessions. |
| Separate databases and roles in local PostgreSQL | Shared application tables | Preserves service ownership; the local instance remains a shared failure domain. |
| Synthetic text input | Uploaded documents | Keeps the feature focused without introducing file parsing and original-file lifecycle responsibilities. |
| Evidence extraction plus server scoring | Model-generated final verdict | Separates untrusted model suggestions from reproducible validation and human review. |

Provider details and artifact identity are recorded in [ADR 0002](decisions/0002-ai-provider-boundary.md). Delivery semantics and recovery are recorded in [ADR 0001](decisions/0001-event-handoff.md).
