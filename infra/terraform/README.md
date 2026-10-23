# AWS deployment reference

This root defines the AWS deployment. It has not been deployed. Mock plans establish configuration invariants, not AWS availability, TLS behavior, inference quality, capacity or recovery.

Public HTTPS reaches an Application Load Balancer. `/api/*` goes directly to the candidates API; other application routes go to the web container. `/internal` and `/internal/*` always return 404. Each ALB target uses HTTPS to its Service Connect TLS proxy on port 8080. API and worker use task-local `http://candidates:8080` and `http://screening:8080` aliases; the proxies encrypt the cross-task connection. Application tokens remain mandatory. The ALB does not verify target certificate identity independently. See [AWS's supported ALB/TLS configuration](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-connect-tls.html).

Fargate tasks, RDS and MSK have no public addresses. Private endpoints provide ECR, Logs, Secrets Manager, SSM and Bedrock access; the S3 gateway permits ECR image-layer reads only. There is no NAT/internet route from private subnets. The worker alone invokes regional Nova Micro. The API alone persists shared cookie keys through encrypted SSM parameters. Task roles and secret-injection execution roles are separate; only the initializer receives the managed RDS master-secret fields. CloudWatch receives safe application JSON and proxy operational logs; access logging with URLs/query parameters is not enabled. Alarms are visible in CloudWatch; external notification delivery is not configured.

## Local configuration checks

Use Terraform 1.16.6 and the committed AWS provider 6.69.1 lock file. From this directory:

```sh
terraform fmt -check -recursive
terraform init -backend=false -input=false -lockfile=readonly
terraform validate
AWS_EC2_METADATA_DISABLED=true terraform test -filter=tests/deployment.tftest.hcl
```

All test runs use `command = plan` and `mock_provider "aws"`. There are no AWS data sources, provisioners, provider aliases or image-build hooks. Initialization downloads the provider from its registry. An ordinary `terraform plan` is a live-account operation and is outside these checks.

## Account prerequisites

Actual deployment requires a separately authorized account, region and resource budget. Before a real plan, prepare:

- Authorized deployment identity, its limited PassRole permissions from `deployment_pass_role_policy`, ECS/Application Auto Scaling service-linked-role prerequisites, and permissions to create the declared resources and service-managed secret/key grants. The account ID is an explicit input and provider allowlist.
- Three confirmed us-east-1 AZs and a non-overlapping private VPC range. Verify RDS PostgreSQL 18.6, selected instance classes, MSK 4.1.x, Service Connect TLS/Fargate, endpoint services and regional Nova Micro access/terms/quotas in that account. No fallback model or cross-region inference profile is selected.
- An owned public hostname, an issued same-account/region ACM certificate covering it, and authority to create its DNS alias/CNAME. DNS/certificate validation is external; the root never guesses a hosted zone.
- Reviewed Linux AMD64 images for each ECR repository. Images must contain the implemented `Aws` profile and initializer. The backend images include the official RDS CA bundle at `/app/certs/rds-global-bundle.pem`; retain its source/hash provenance. No insecure certificate-verification escape hatch is supported.
- Each image prepares `/tmp` for its nonroot runtime UID with mode 0700 and declares `VOLUME ["/tmp"]`. Each ECS task mounts its own ephemeral `scratch` volume there and keeps the root filesystem read-only. The absolute paths must match: [Fargate copies the prepared image directory into the volume](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html). No host path, shared persistent disk or privileged init container is used. Verify permissions/startup against the published image and actual Fargate task; a mock plan cannot prove that copy behavior. Local Compose overrides the image volume with a `/tmp` tmpfs whose explicit UID/GID and mode 0700 match the container's runtime user.
- Approved private CA subject. This reference uses an AWS-generated P-256 key, SHA256 ECDSA, a five-year root certificate, short-lived service certificates and ECS-managed rotation. Root renewal/replacement needs a deliberate service-trust rollout before expiry; Terraform does not automatically renew this root.
- An external encrypted, access-controlled, versioned Terraform state backend with locking. This root intentionally has no account-specific backend configuration. Configure the selected backend before real use; never place state, plans or real tfvars in Git. Local state is suitable only for the credential-free checks.

Copy `example.tfvars` to an ignored deployment tfvars file and replace every synthetic value. The image inputs are digest strings, never floating tags. No secret values belong in tfvars or Terraform environment variables.

## First deployment and bootstrap

These steps describe future operator actions; writing this reference does not authorize executing them. Review each real plan before its separately approved apply. Keep `public_ingress_enabled=false` until all gates pass; the public host returns 503 while target groups remain attached.

1. Set `deployment_phase="register"`, `bootstrap_verified=false`, and `public_ingress_enabled=false`. Apply the reviewed infrastructure plan. This creates empty ECR repositories and task/service definitions with zero service tasks. Publish reviewed images into those repositories externally, replace input digests with the actual published digests, then review/apply the task-definition update. No image has to exist merely to register its task definition; it must exist before a task runs.
2. Securely seed the five **empty secret containers** using Secrets Manager's approved console/provisioning workflow. `candidates`, `screening`, `candidates-viewer`, and `screening-viewer` each require a JSON `password` field containing a separately generated 64-hex credential. `internal` requires a JSON `token` field containing a separate 64-hex service token shared by API/worker. Never seed secret versions through Terraform. RDS generates and manages its own master secret with `username` and `password` fields. Never inspect or print those values for this runbook.
3. Wait for RDS/MSK readiness, image publication and secret seeding. Run the actual one-shot initializer task, capture its task ARN, and inspect **only** exit codes and bounded reason categories. No HTTP success substitutes for task exit zero. The image's entrypoint is `dotnet service.dll`; its ECS command is `--initialize-deployment`.

The following commands contain identifiers only and belong in the future authorized operator shell. They never retrieve secret values. `jq` is used only on Terraform metadata, not secret payloads:

```sh
umask 077
BOOTSTRAP_DIR=$(mktemp -d)
terraform output -json bootstrap > "$BOOTSTRAP_DIR/bootstrap.json"
jq '{cluster, taskDefinition: .task_definition, launchType: "FARGATE", platformVersion: "1.4.0", networkConfiguration: .network_configuration, count: 1}' \
  "$BOOTSTRAP_DIR/bootstrap.json" > "$BOOTSTRAP_DIR/run-task.json"
TASK_ARN=$(aws ecs run-task --region us-east-1 --cli-input-json "file://$BOOTSTRAP_DIR/run-task.json" \
  --query 'tasks[0].taskArn' --output text)
test -n "$TASK_ARN" && test "$TASK_ARN" != None
aws ecs wait tasks-stopped --region us-east-1 \
  --cluster "$(jq -r .cluster "$BOOTSTRAP_DIR/bootstrap.json")" --tasks "$TASK_ARN"
aws ecs describe-tasks --region us-east-1 \
  --cluster "$(jq -r .cluster "$BOOTSTRAP_DIR/bootstrap.json")" --tasks "$TASK_ARN" \
  --query 'tasks[].{status:lastStatus,containers:containers[].{name:name,exitCode:exitCode}}' --output json
```

Require the `initializer` container's actual exitCode to equal 0. A failed `run-task`, absent task ARN, timeout, absent exit code or nonzero exit is a stop condition. Inspect only approved safe initializer log events; never dump a task definition, environment, SQL exception or secret. The initializer preserves existing database ownership/passwords/topics; unexpected ownership, privileges, credentials or topic settings fail rather than silently mutate/delete data. AWS topic replication is three with minimum ISR two; local replication remains one.

## Initial Service Connect discovery

[AWS documents that existing deployments do not learn later endpoints](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-connect-concepts-deploy.html). Terraform resource ordering alone does not solve mutual API/worker discovery.

1. List namespace service **names only** after registering all three ECS services:

   ```sh
   NAMESPACE_ID=$(terraform output -raw namespace_id)
   aws servicediscovery list-services --region us-east-1 \
     --filters "Name=NAMESPACE_ID,Values=$NAMESPACE_ID,Condition=EQ" \
     --query 'Services[].Name' --output json
   ```

2. Verify `candidates`, `screening` and `web` exist. Zero-task registration must be observed, never assumed. If all exist and the initializer succeeded, set `bootstrap_verified=true`, `deployment_phase="active"`, keeping public ingress false, then review/apply. If endpoints are absent, the initializer must still have succeeded first; activate once with ingress false so ECS can register the endpoint deployments. No user jobs are accepted during this repair. Re-list names; if any remain absent, stop and repair the service deployment rather than enabling traffic.
3. After **all** names exist, force a fresh deployment of candidates and screening (and web), then wait for stability. This explicitly makes each task deployment capture the complete namespace. Redeploy again whenever endpoint membership changes; routine task restarts alone are not the documented endpoint-update mechanism.

   ```sh
   CLUSTER_ARN=$(terraform output -json bootstrap | jq -r .cluster)
   for NAME in candidates screening web; do
     SERVICE_NAME=$(terraform output -json services | jq -r --arg name "$NAME" '.[$name]')
     aws ecs update-service --region us-east-1 --cluster "$CLUSTER_ARN" \
       --service "$SERVICE_NAME" --force-new-deployment --query 'service.serviceName' --output text
     aws ecs wait services-stable --region us-east-1 --cluster "$CLUSTER_ARN" --services "$SERVICE_NAME"
   done
   ```

4. Verify HTTPS target health, expected safe logs and dependency readiness while the public host still returns 503. Then review/apply `public_ingress_enabled=true` for the controlled smoke test before announcing the endpoint. Verify the complete browser session → submission → private API/worker authorization → result → persisted notification → deletion flow at the owned hostname. Confirm real SSE heartbeat bytes remain below both ALB/SC idle timeouts and streams survive the complete-response deadline setting. Confirm RDS hostname verification, MSK IAM, shared SSM/KMS keys, Nova access/validation, CloudWatch delivery and closed internal routes in the real account. If the smoke fails, restore maintenance immediately without deleting state. These are live checks; mocks cannot prove them.

`bootstrap_verified` is a deliberate operator attestation, not a substitute for those observations. It attests initializer success before either activation path; complete endpoint discovery and fresh deployments remain mandatory before public traffic.

## Updates, rotation and recovery

Publish new reviewed digests, inspect the plan and deploy through the circuit breaker. CPU target tracking remains within per-service validated limits; increasing the backend maximum beyond its accepted input range requires a reviewed database-role/pool configuration change. Worker lag, missing tasks, API target/error state, database storage and Kafka offline partitions have CloudWatch alarms. Consumer lag may be absent before its group begins consuming. Confirm alarm emissions and arrange an authorized notification channel before relying on alerts. A Terraform apply deliberately reconciles desired counts to configured minimums; inspect any count change when services have autoscaled.

Secret injection happens at task creation. Credential rotation requires an explicit coordinated database/secret change and replacement tasks, not just editing a secret. The initializer verifies existing passwords and does not rotate them. Shared Data Protection keys persist across replicas/redeployments; do not delete the SSM prefix or KMS key to repair startup. ECS owns Service Connect certificate secrets/rotation; business roles cannot read its `ecs-sc!` private-key secrets. Do not disable rotation, add secret replicas or delete the CA/role before service draining finishes.

Active application data expires independently of encrypted backup retention. Automated RDS backups age out after the explicitly configured bounded retention; historical snapshots/final snapshots need an approved separate retention review. Deleting an active record does not physically erase each backup copy immediately.

RDS requires a final snapshot and deletion protection. Terraform `prevent_destroy` additionally protects data, key, secret, registry, CA and log resources. There is no automatic teardown command. Retirement requires a separate data-retention/backup decision, drained/deleted services first, explicit protection changes and review of the resulting destructive plan. MSK has no RDS-style restore snapshot; retain durable database outbox/inbox state and reconcile before replay. Keys and CA must outlive the data/services that depend on them.

A historical restore stays under maintenance with public traffic disabled until both databases and pending/replayed events have reconciled expiration and deletion. `expiresAt` survives snapshots and supports expiry denial/purge. A snapshot cannot reveal an early deletion that happened after its timestamp: require a consistent latest recovery point or later authoritative deletion state and operator-verified reconciliation. If that authority is unavailable, keep ingress closed; do not claim old data is safe merely because the initializer or readiness probe succeeded. No additional deletion-journal datastore is provisioned by this reference. The initializer preserves ownership/topic invariants; it does not prove restore reconciliation. Never run it with incompatible credentials/ownership as a repair shortcut. Cloud restore and reconciliation have not been exercised.

## Source references

- [Pinned AWS provider resource documentation](https://github.com/hashicorp/terraform-provider-aws/tree/v6.69.1/website/docs/r), especially ECS Service Connect TLS fields, RDS managed master secrets, MSK IAM/encryption and Private CA certificate association.
- [ECR private endpoint/image-layer requirements](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html).
- [MSK IAM action/resource prerequisites](https://docs.aws.amazon.com/msk/latest/developerguide/iam-access-control-use-cases.html) and [supported CloudWatch metrics](https://docs.aws.amazon.com/msk/latest/developerguide/metrics-details.html).
- [ECS secret JSON-key injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html), [RDS TLS verification](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html) and [SSM SecureString encryption](https://docs.aws.amazon.com/systems-manager/latest/userguide/secure-string-parameter-kms-encryption.html).
- [PostgreSQL 18 logging controls](https://www.postgresql.org/docs/18/runtime-config-logging.html) and [RDS's managed superuser boundary](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Roles.rds_superuser.html). The parameter group disables statement/bind-value logging and detailed error context; the initializer must verify these settings before credential-bearing operations, rather than assume RDS grants PostgreSQL-superuser SET privileges.
- [Terraform mock-provider semantics](https://developer.hashicorp.com/terraform/language/tests/mocking).
