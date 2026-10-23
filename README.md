# Candidate Screening · Event-driven workbench

Submit a synthetic candidate profile against explicit job requirements and receive an explainable screening result. The Candidates API accepts the work durably; a separate .NET worker extracts evidence with Ollama, validates it and publishes the result through Kafka. React keeps navigation available while processing and presents a saved notification when the result is ready.

*Local inference with Ollama. The AWS deployment reference has not been deployed.*

**Learning project.** Validation uses scoped local fixtures and synthetic input. Target-scale capacity and a clean full-stack rebuild from a fresh clone remain unverified.

![Candidate submission flows through the Candidates API, Kafka and Screening worker; validated evidence returns as saved status and a browser notification.](docs/assets/flow-summary.svg)

## One screening, end to end

1. **Submit:** React sends a bounded profile narrative, job requirements and a retry key.
2. **Accept:** the Candidates API validates the request and commits candidate state with an outbox record before returning an acceptance receipt.
3. **Publish:** an outbox relay sends the submitted event to Kafka and waits for broker confirmation.
4. **Screen:** the worker extracts evidence with Ollama, checks exact source quotes and assigns conservative review states.
5. **Save:** the worker commits the result and completion outbox together; the Candidates API consumes completion into owned status and a saved notification.
6. **Read:** an authorized event stream signals the browser to fetch changed state. Notifications elsewhere leave the current page intact; reopening the app reconciles saved results.

An acceptance receipt confirms stored work, not a finished screening. Duplicate messages and request retries must preserve the same committed outcome. [Handoff and recovery →](docs/decisions/0001-event-handoff.md)

## Components

| Component | Responsibility |
| --- | --- |
| React | Submission, processing, results and notification views |
| Candidates API · ASP.NET Core | Request validation, ownership, durable acceptance and visible status |
| Screening worker · .NET | Evidence extraction, validation and conservative review states |
| Kafka | Submitted, completed and change events |
| PostgreSQL | Separate service databases with transactional outboxes and inboxes |
| Ollama | Local inference behind a provider boundary prepared for AWS Bedrock |

The model proposes evidence. Application code verifies it; a person reviews the result. [AI provider boundary →](docs/decisions/0002-ai-provider-boundary.md)

## Run locally

Use Docker Compose on a Linux ARM64 engine:

```sh
./scripts/start.sh
```

The source builds require network access for pinned upstream sources and the initial model download. See the [operations guide](docs/operations.md) for credentials and verification commands.

Open the [workbench](http://127.0.0.1:8094). Use synthetic text, submit a profile and requirements, then continue elsewhere while screening runs. Open the saved notice to review the result. See [operations](docs/operations.md) for service viewers and access.

## Engineering references

- [Architecture](docs/architecture.md): the user-to-result flow and source boundaries.
- [Design](docs/design.md): ownership, events, browser updates and recovery.
- [AWS reference](docs/aws.md): the same flow on Fargate, MSK, RDS and Bedrock.
- [Technical sources](docs/research.md): implementation choices and official references.
- [Detailed flow diagram](docs/assets/overview.svg) · [Editable source](docs/assets/overview.mmd).

**Limits:** synthetic text input only. Source text, results and notices expire after seven days; the owner can delete sooner. Proposed evidence does not establish candidate quality or make a hiring decision. Local inference is the default; cloud deployment and Bedrock calls require explicit configuration.

## Verification scope

Initializer, historical-restore, idle-stream and load checks exercise different boundaries; retain each result and its exact source/image identities. A small synthetic load does not establish the planned production capacity.

Keep raw dependency findings when reviewing applicability. The AKHQ base retains medium/low vendor findings, including a [deferred Expat fix](https://ubuntu.com/security/CVE-2025-66382). Pip's vendor inventory can also match setuptools advisories whose affected modules are absent from the shipped subset; inspect the actual installed and bundled files before classifying those findings.
