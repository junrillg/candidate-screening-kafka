# Candidate Screening · Event-driven workbench

Submit a synthetic candidate profile against explicit job requirements and receive an explainable screening result. The Candidates API accepts the work durably; a separate .NET worker extracts evidence with Ollama, validates it and publishes the result through Kafka. React keeps navigation available while processing and presents a saved notification when the result is ready.

*Local inference with Ollama. The AWS deployment reference has not been deployed.*

**Learning draft.** The core flow has passed scoped local recovery and browser checks. Viewer derivatives and a clean rebuild of the current full stack remain unfinished.

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

The full-stack command is currently blocked by unfinished viewer validation. Open WebUI's native-build provenance needs refreshing; AKHQ's dependency and runtime changes still need a completed build. Existing local runtime checks do not establish that a fresh clone builds successfully.

Open the [workbench](http://127.0.0.1:8094). Use synthetic text, submit a profile and requirements, then continue elsewhere while screening runs. Open the saved notice to review the result. See [operations](docs/operations.md) for service viewers and access.

## Engineering references

- [Architecture](docs/architecture.md): the user-to-result flow and source boundaries.
- [Design](docs/design.md): ownership, events, browser updates and recovery.
- [AWS reference](docs/aws.md): the same flow on Fargate, MSK, RDS and Bedrock.
- [Technical sources](docs/research.md): implementation choices and official references.
- [Detailed flow diagram](docs/assets/overview.svg) · [Editable source](docs/assets/overview.mmd).

**Limits:** synthetic text input only. Source text, results and notices expire after seven days; the owner can delete sooner. Proposed evidence does not establish candidate quality or make a hiring decision. Local inference is the default; cloud deployment and Bedrock calls require explicit configuration.

## Remaining validation

- Complete the AKHQ and Open WebUI builds, native compatibility checks, and dependency/security repairs. Known image vulnerabilities remain unresolved.
- Verify the rebuilt initializer against a fresh broker, then finish historical-restore, idle-stream SQL and scoped load checks.
- Run hosted CI against the pushed revision. Current local evidence covers specific source/image snapshots rather than the complete current checkout.
