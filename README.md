# Candidate Screening · Event-driven workbench

Submit a synthetic candidate profile against explicit job requirements and receive an explainable screening result. The Candidates API accepts the work durably; a separate .NET worker extracts evidence with Ollama, validates it and publishes the result through Kafka. React keeps navigation available while processing and presents a saved notification when the result is ready.

*Planned architecture; the complete application flow is not yet verified.*

![Candidate submission flows through the Candidates API, Kafka and Screening worker; validated evidence returns as saved status and a browser notification.](docs/assets/flow-summary.svg)

## One screening, end to end

1. **Submit:** React sends a bounded profile narrative, job requirements and a retry key.
2. **Accept:** the Candidates API validates the request and commits candidate state with an outbox record before returning an acceptance receipt.
3. **Publish:** an outbox relay sends the submitted event to Kafka and waits for broker confirmation.
4. **Screen:** the worker extracts evidence with Ollama, checks source quotes and calculates a deterministic result.
5. **Save:** the worker commits the result and completion outbox together; the Candidates API consumes completion into owned status and a saved notification.
6. **Read:** an authorized event stream signals the browser to fetch changed state. Notifications elsewhere leave the current page intact; reopening the app reconciles saved results.

An acceptance receipt confirms stored work, not a finished screening. Duplicate messages and request retries must preserve the same committed outcome. [Handoff and recovery →](docs/decisions/0001-event-handoff.md)

## Components

| Component | Responsibility |
| --- | --- |
| React | Submission, processing, results and notification views |
| Candidates API · ASP.NET Core | Request validation, ownership, durable acceptance and visible status |
| Screening worker · .NET | Evidence extraction, validation and reproducible scoring |
| Kafka | Submitted, completed and change events |
| PostgreSQL | Separate service databases with transactional outboxes and inboxes |
| Ollama | Local inference behind a provider boundary prepared for AWS Bedrock |

The model proposes evidence. Application code verifies it; a person reviews the result. [AI provider boundary →](docs/decisions/0002-ai-provider-boundary.md)

## Run locally

A verified startup command and service addresses are not available yet. See the [architecture](docs/design.md) for the intended service boundaries and event flow.

## Engineering references

- [Architecture](docs/design.md): ownership, events, browser updates and recovery.
- [Technical sources](docs/research.md): implementation choices and official references.
- [Detailed flow diagram](docs/assets/overview.svg) · [Editable source](docs/assets/overview.mmd).

**Limits:** synthetic text input only. Evidence coverage does not establish candidate quality or make a hiring decision. Local inference is the default; the Bedrock integration is a separate, inactive provider path.
