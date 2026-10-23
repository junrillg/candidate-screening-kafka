# ADR 0002: Local evidence extraction with an explicit Bedrock boundary

Status: Accepted.

Research checked: 2026-10-20.

## Context

The narrow flow accepts a synthetic candidate narrative, extracts evidence for a
job's required skills, and presents an explainable result for human review.
Real model generation and deterministic server validation are required.

## Decision

Use local **Ollama 0.40.6** and **qwen2.5:1.5b-instruct** for the runnable flow.
[Ollama](https://github.com/ollama/ollama/releases/tag/v0.40.6) is MIT licensed;
the [model](https://ollama.com/library/qwen2.5:1.5b-instruct) is Apache-2.0,
with the Q4_K_M artifact. Verify the pulled
model against this [registry manifest](https://registry.ollama.ai/v2/library/qwen2.5/manifests/1.5b-instruct)
SHA-256 rather than trusting the mutable tag:

```text
65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b
```

Call `/api/chat` with `stream: false`, a JSON Schema in `format`, and temperature
zero. [Structured output](https://docs.ollama.com/capabilities/structured-outputs)
constrains shape, not factual correctness. Disable cloud features with `OLLAMA_NO_CLOUD=1`. Bound context, generated output,
concurrency and queues in runtime configuration. Apply worker deadlines, cancellation,
bounded retries and response limits.

One application contract returns requirement evidence, abstentions and safe provider/model
metadata. Both providers pass through the same .NET boundary validation: allowed
requirement indexes, exact source snippets, uniqueness, lengths and valid completion. The server
assigns `EvidenceFound`, `NeedsReview` or `NotEvidenced`; every result requires human review. Snippet occurrence alone
does not establish positive evidence: negation and irrelevant mentions need explicit
evaluation cases. Neither provider makes a hiring decision or receives tool access.

## Bedrock implementation

Use **AWSSDK.BedrockRuntime 4.0.101.12** for the adapter. Select the AWS deployment profile explicitly; the local default neither
constructs an AWS client nor discovers credentials. There is no automatic fallback.

The scoped reference uses [Nova Micro](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-micro.html),
`amazon.nova-micro-v1:0`, through nonstreaming Converse in `us-east-1`. This active
model supports Converse but **does not support native structured outputs**. Request
JSON through the prompt and reject responses that fail the shared validation; do not
claim schema-constrained generation. Other models require a fresh capability review.

[Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
requires only `bedrock:InvokeModel` for this direct base-model path. The worker's task-role policy grants that operation on the selected model:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel"],
    "Resource": ["arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"]
  }]
}
```

The [Terraform deployment reference](../../infra/terraform/README.md) pins AWS provider 6.69.1 and defines compute, private networking, service identity, durable storage, Kafka, encryption and telemetry. Runtime task roles and secret-injection execution roles are separate. The API has no model-invocation grant. Credential-free [provider mock tests](https://developer.hashicorp.com/terraform/language/tests/mocking) cover configuration invariants; they do not establish a live deployment.

## Consequences and verification

Docker on macOS uses CPU inference; the [official FAQ](https://docs.ollama.com/faq)
states that Docker Desktop lacks GPU passthrough. Download size is not a RAM ceiling.
Model size alone does not establish runtime behavior. Verify real generation,
negation/injection cases, invalid output, cancellation and restart recovery locally.

Local execution uses Ollama. The AWS profile selects Bedrock with explicit regional configuration and AWS access. SDK compilation, response-validation tests and configuration inspection cover the integration boundary; they do not establish live inference behavior. The AWS reference has not been deployed.
