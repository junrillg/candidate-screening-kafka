# From submission to saved result

A submission returns a durable receipt before inference begins. Separate database ownership, transactional event handoffs and an authorized browser stream connect the React workbench to the result without holding navigation open.

![Submission, separate service databases, asynchronous screening and saved-result delivery.](assets/flow-summary.svg)

The numbered journey is shared with the [overview](../README.md) and [AWS reference](aws.md). Solid arrows are calls; dotted arrows are asynchronous delivery. Inspection and telemetry sit beside the business path.

The [detailed service map](assets/overview.svg) includes internal fetches, processing navigation and operational boundaries; its [editable source](assets/overview.mmd) uses the same steps.

## Execution boundaries

| Step | Caller → receiver and committed effect | Source |
| --- | --- | --- |
| 1 · Submit | The form validates its fields and retains one retry key for unchanged input. Its submit handler sends JSON, cookie credentials and an antiforgery header through the same-origin proxy. A receipt changes the route to that candidate's processing view. | [React form and routes](../frontend/src/App.tsx), [HTTP client and response validation](../frontend/src/api.ts), [proxy](../docker/frontend/default.conf) |
| 2 · Accept | Kestrel receives HTTP; ASP.NET Core routes, middleware and generated binding reach the mapped handler. Browser middleware supplies server-derived ownership. The handler validates input and atomically stores candidate state and its submission outbox before returning `202`. | [API startup wiring](../services/Candidates.Api/Program.cs), [host and request pipeline](../services/Common/FoundationHost.cs), [browser authorization](../services/Candidates.Api/BrowserSession.cs), [candidate endpoints](../services/Candidates.Api/CandidatesFlow.cs) |
| 3 · Publish | The relay claims an outbox lease, publishes its stable event identity to Kafka and waits for acknowledgment before marking delivery. An uncertain acknowledgment may result in the same event being published again. | [Relay and consumer loops](../services/Common/FlowTransport.cs), [Kafka client configuration](../services/Common/KafkaConfiguration.cs), [event contracts](../services/Common/FlowContracts.cs) |
| 4 · Screen | The worker commits a unique inbox/job before advancing its consumed position. A processing loop claims the job, fetches authorized source and calls inference outside a database transaction. Strict validation precedes an atomic result and completion-outbox commit. | [Worker registration](../services/Screening.Worker/Program.cs), [job ownership and processing](../services/Screening.Worker/ScreeningFlow.cs), [Ollama request](../services/Screening.Worker/Inference.cs), [evidence validation](../services/Common/EvidenceValidation.cs) |
| 5 · Save | The Candidates API consumes completion, fetches and revalidates the worker's result, then commits its inbox, visible status, stable notification and change outbox. Neither service reads the other's database. | [Completion handler](../services/Candidates.Api/CandidatesCompletion.cs), [owned API schema](../services/Candidates.Api/Schema.sql), [owned worker schema](../services/Screening.Worker/Schema.sql) |
| 6 · Read | Each API replica consumes change events into bounded owner-scoped channels. The browser verifies its owner, reconciles stored state and drains queued wakeups. An off-route completion adds an inbox notice without changing the page, focus or draft. | [Stream and read endpoints](../services/Candidates.Api/CandidatesFlow.cs), [bounded channels](../services/Common/ChangeHub.cs), [reconciliation hook](../frontend/src/useWorkbench.ts), [result and inbox views](../frontend/src/App.tsx) |

`Program.cs` configures startup dependencies and route handlers; it is not a new request hop on every call. React state updates lead to render and commit; browser network activity and event delivery remain asynchronous. No model call or idle event stream holds an application database transaction open.

## State and failure

The visible journey is `Queued` → `Screening` → `Completed` or `Failed`. `Deleting` hides active content immediately while durable cleanup reaches the worker. Expiry and deletion fences reject late results and prevent retry keys from creating the same removed work again.

Request idempotency binds the owner, key and canonical input. Repeating unchanged input returns its original acceptance; conflicting key reuse is rejected. Event inboxes prevent duplicate committed effects, while leases make interrupted publication and processing recoverable. Kafka delivery remains at least once.

Provider output must have the expected shape, unique allowed requirement indexes and exact source quotes. Negation, irrelevant mentions and instructions inside the source do not establish positive evidence. A validated evidence gap remains a review outcome. Exhausted technical work becomes a visible failure with durable operator recovery; it never becomes invented evidence.

Browser signals are hints to fetch authoritative state. Subscription precedes database catch-up, and queued wakeups are drained afterward. Slow readers disconnect and reconcile; reconnects, reloads and a previously closed browser recover through persisted status and notices. External email or push delivery is not configured.

## Trust and operations

Browser requests use a protected synthetic session, exact mutation origin checks and antiforgery validation. Every status, result, list, notice acknowledgment and stream is owner-authorized. Internal source/result endpoints require a separate service token and are excluded from the frontend proxy. Responses from an old owner scope are cancelled or ignored.

Application logs contain safe identifiers and state categories, excluding candidate narratives, quotes, prompts, credentials and raw provider exceptions. OpenTelemetry exports local logs to OpenObserve; telemetry may drop during an extended outage and cannot substitute for durable business state. The [operations reference](operations.md) lists separate authenticated service viewers.

The [real-flow check](../tests/integration/flow.py) exercises the application entry point. The [recovery check](../tests/integration/recovery.py) covers selected replay, outage and retention boundaries. Their scope does not establish cloud failover or deployed-provider behavior. See [handoff semantics](decisions/0001-event-handoff.md) and [the AI boundary](decisions/0002-ai-provider-boundary.md).
