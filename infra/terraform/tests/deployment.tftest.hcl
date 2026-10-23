# Every run plans against this mock; no real provider aliases or data sources exist.
mock_provider "aws" {
  override_during = plan
  mock_resource "aws_vpc" {
    defaults = { id = "vpc-0123456789abcdef0" }
  }
  mock_resource "aws_subnet" {
    defaults = { id = "subnet-0123456789abcdef0" }
  }
  mock_resource "aws_security_group" {
    defaults = { id = "sg-0123456789abcdef0" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-4000-8000-000000000001" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/synthetic/logs" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:123456789012:repository/synthetic"
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/synthetic"
    }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:synthetic-other-123456" }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "synthetic.abcdefghijkl.us-east-1.rds.amazonaws.com"
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds!synthetic-master-123456"
        kms_key_id    = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-4000-8000-000000000001"
        secret_status = "active"
      }]
    }
  }
  mock_resource "aws_msk_cluster" {
    defaults = {
      arn                        = "arn:aws:kafka:us-east-1:123456789012:cluster/synthetic/00000000-0000-4000-8000-000000000001-1"
      bootstrap_brokers_sasl_iam = "b-1.synthetic.kafka.us-east-1.amazonaws.com:9098,b-2.synthetic.kafka.us-east-1.amazonaws.com:9098,b-3.synthetic.kafka.us-east-1.amazonaws.com:9098"
    }
  }
  mock_resource "aws_msk_configuration" {
    defaults = { arn = "arn:aws:kafka:us-east-1:123456789012:configuration/synthetic/00000000-0000-4000-8000-000000000001-1", latest_revision = 1 }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/synthetic" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/synthetic/0000000000000001", arn_suffix = "app/synthetic/0000000000000001" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/synthetic/0000000000000001", arn_suffix = "targetgroup/synthetic/0000000000000001" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:listener/app/synthetic/0000000000000001/0000000000000001" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:cluster/synthetic", id = "arn:aws:ecs:us-east-1:123456789012:cluster/synthetic" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/synthetic:1" }
  }
  mock_resource "aws_service_discovery_http_namespace" {
    defaults = { arn = "arn:aws:servicediscovery:us-east-1:123456789012:namespace/ns-0123456789abcdef", id = "ns-0123456789abcdef" }
  }
  mock_resource "aws_acmpca_certificate_authority" {
    defaults = { arn = "arn:aws:acm-pca:us-east-1:123456789012:certificate-authority/00000000-0000-4000-8000-000000000001" }
  }
  mock_resource "aws_acmpca_certificate" {
    defaults = { certificate = "synthetic-public-root-certificate" }
  }
  mock_resource "aws_vpc_endpoint" {
    defaults = { prefix_list_id = "pl-01234567" }
  }
  override_resource {
    target = aws_secretsmanager_secret.app["candidates"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:synthetic-candidates-123456" }
  }
  override_resource {
    target = aws_secretsmanager_secret.app["screening"]
    values = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:synthetic-screening-123456" }
  }
  override_resource {
    target = aws_route_table.public
    values = { id = "rtb-0123456789abcdef0" }
  }
  override_resource {
    target = aws_route_table.private
    values = { id = "rtb-0123456789abcdef1" }
  }
  override_resource {
    target = aws_security_group.task["candidates"]
    values = { id = "sg-0123456789abcdef1" }
  }
  override_resource {
    target = aws_security_group.task["screening"]
    values = { id = "sg-0123456789abcdef2" }
  }
  override_resource {
    target = aws_security_group.alb
    values = { id = "sg-0123456789abcdef3" }
  }
  override_resource {
    target = aws_iam_role.task["candidates"]
    values = { arn = "arn:aws:iam::123456789012:role/synthetic-candidates-task" }
  }
  override_resource {
    target = aws_iam_role.task["screening"]
    values = { arn = "arn:aws:iam::123456789012:role/synthetic-screening-task" }
  }
  override_resource {
    target = aws_iam_role.mesh
    values = { arn = "arn:aws:iam::123456789012:role/synthetic-mesh-infrastructure" }
  }
}

variables {
  account_id                = "123456789012"
  availability_zones        = ["us-east-1a", "us-east-1b", "us-east-1c"]
  public_host               = "screening.example.com"
  public_certificate_arn    = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-4000-8000-000000000001"
  ca_common_name            = "Synthetic service transport"
  final_snapshot_identifier = "synthetic-final-snapshot"
  image_digests = {
    candidates = "sha256:0000000000000000000000000000000000000000000000000000000000000001"
    screening  = "sha256:0000000000000000000000000000000000000000000000000000000000000002"
    web        = "sha256:0000000000000000000000000000000000000000000000000000000000000003"
  }
}

run "registration_is_closed_and_private" {
  command = plan
  assert {
    condition     = alltrue([for service in aws_ecs_service.app : service.desired_count == 0 && !service.network_configuration[0].assign_public_ip]) && aws_lb_listener_rule.maintenance[0].action[0].fixed_response[0].status_code == "503"
    error_message = "Registration must start no workload tasks and expose only maintenance at the public host."
  }
  assert {
    condition     = alltrue([for subnet in concat(values(aws_subnet.tasks), values(aws_subnet.data)) : !subnet.map_public_ip_on_launch]) && aws_route.public.route_table_id != aws_route_table.private.id
    error_message = "Private workloads/data must not inherit the internet route or public addresses."
  }
  assert {
    condition = alltrue([for endpoint in aws_vpc_endpoint.interface : endpoint.private_dns_enabled && endpoint.vpc_endpoint_type == "Interface"]) && (
      jsondecode(aws_vpc_endpoint.interface["bedrock-runtime"].policy).Statement[0].Resource == "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
    ) && jsondecode(aws_vpc_endpoint.s3.policy).Statement[0].Resource == "arn:aws:s3:::prod-us-east-1-starport-layer-bucket/*"
    error_message = "Private endpoints must restrict runtime/model access and ECR S3 layer reads."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.alb_targets["candidates"].security_group_id == aws_security_group.task["candidates"].id &&
      aws_vpc_security_group_ingress_rule.alb_targets["candidates"].referenced_security_group_id == aws_security_group.alb.id &&
      aws_vpc_security_group_ingress_rule.alb_targets["candidates"].from_port == 8080 &&
      aws_vpc_security_group_ingress_rule.mesh["candidates"].referenced_security_group_id == aws_security_group.task["screening"].id &&
      aws_vpc_security_group_ingress_rule.mesh["screening"].referenced_security_group_id == aws_security_group.task["candidates"].id &&
      aws_vpc_security_group_ingress_rule.data["candidates-postgres"].from_port == 5432 &&
      aws_vpc_security_group_ingress_rule.data["screening-kafka"].from_port == 9098 &&
      aws_vpc_security_group_egress_rule.data["candidates-endpoints"].from_port == 443 &&
      jsondecode(aws_vpc_endpoint.interface["ssm"].policy).Statement[0].Condition.ArnEquals["aws:PrincipalArn"] == [aws_iam_role.task["candidates"].arn] &&
      jsondecode(aws_vpc_endpoint.interface["bedrock-runtime"].policy).Statement[0].Condition.ArnEquals["aws:PrincipalArn"] == [aws_iam_role.task["screening"].arn]
    )
    error_message = "Ingress/egress must connect intended peers on exact proxy/data/endpoint ports; endpoint policies retain service-role boundaries."
  }
  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_listener.https.certificate_arn == var.public_certificate_arn && aws_lb_listener.https.ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06" && alltrue([for target in aws_lb_target_group.public : target.protocol == "HTTPS" && target.port == 8080 && target.health_check[0].protocol == "HTTPS"])
    error_message = "ALB must terminate public HTTPS and reach SC TLS listeners, including health probes."
  }
  assert {
    condition     = aws_lb_listener_rule.internal_denied.priority < aws_lb_listener_rule.api.priority && aws_lb_listener_rule.internal_denied.action[0].fixed_response[0].status_code == "404" && toset(one(aws_lb_listener_rule.internal_denied.condition).path_pattern[0].values) == toset(["/internal", "/internal/*"])
    error_message = "Public internal endpoints must be rejected before any forwarding."
  }
}

run "runtime_security_and_wiring" {
  command = plan
  variables {
    deployment_phase       = "active"
    bootstrap_verified     = true
    public_ingress_enabled = true
  }
  assert {
    condition = alltrue([for name, service in aws_ecs_service.app :
      service.desired_count == var.service_limits[name].min && service.service_connect_configuration[0].enabled &&
      service.service_connect_configuration[0].namespace == aws_service_discovery_http_namespace.mesh.arn &&
      service.service_connect_configuration[0].service[0].port_name == name &&
      service.service_connect_configuration[0].service[0].client_alias[0].dns_name == name &&
      service.service_connect_configuration[0].service[0].tls[0].role_arn == aws_iam_role.mesh.arn &&
      service.service_connect_configuration[0].service[0].tls[0].issuer_cert_authority[0].aws_pca_authority_arn == aws_acmpca_certificate_authority.mesh.arn
    ]) && aws_ecs_service.app["candidates"].service_connect_configuration[0].service[0].timeout[0].per_request_timeout_seconds == 0
    error_message = "Each service must use the named SC endpoint/CA/infra role; SSE must have no complete-response deadline."
  }
  assert {
    condition     = aws_acmpca_certificate_authority.mesh.tags.AmazonECSManaged == "true" && aws_acmpca_certificate_authority.mesh.usage_mode == "SHORT_LIVED_CERTIFICATE" && aws_acmpca_certificate_authority_certificate.mesh.certificate == aws_acmpca_certificate.root.certificate && strcontains(aws_iam_role_policy_attachment.mesh.policy_arn, "AmazonECSInfrastructureRolePolicyForServiceConnectTransportLayerSecurity")
    error_message = "The actual CA certificate association and managed TLS role policy must be wired."
  }
  assert {
    condition = alltrue([for name, task in aws_ecs_task_definition.service :
      jsondecode(task.container_definitions)[0].image == "${aws_ecr_repository.app[name].repository_url}@${var.image_digests[name]}" &&
      jsondecode(task.container_definitions)[0].readonlyRootFilesystem &&
      jsondecode(task.container_definitions)[0].mountPoints == [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }] &&
      jsondecode(task.container_definitions)[0].portMappings[0].name == name &&
      jsondecode(task.container_definitions)[0].logConfiguration.logDriver == "awslogs" &&
      jsondecode(task.container_definitions)[0].logConfiguration.options["awslogs-group"] == aws_cloudwatch_log_group.app[name].name &&
      alltrue([for env in jsondecode(task.container_definitions)[0].environment : !contains(["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "Lab__OtlpEndpoint", "Lab__DatabaseConnection", "Lab__KeysPath", "ASPNETCORE_FORWARDEDHEADERS_ENABLED"], env.name)])
    ])
    error_message = "Tasks must use reviewed immutable images, constrained filesystems, awslogs and no unsafe cloud fallbacks."
  }
  assert {
    condition = alltrue([for name in ["candidates", "screening"] :
      contains(jsondecode(aws_ecs_task_definition.service[name].container_definitions)[0].environment, { name = "Lab__Deployment", value = "Aws" }) &&
      contains(jsondecode(aws_ecs_task_definition.service[name].container_definitions)[0].environment, { name = "Lab__KafkaBootstrapServers", value = aws_msk_cluster.events.bootstrap_brokers_sasl_iam }) &&
      contains(jsondecode(aws_ecs_task_definition.service[name].container_definitions)[0].secrets, { name = "Lab__DatabasePassword", valueFrom = "${aws_secretsmanager_secret.app[name].arn}:password::" }) &&
      !strcontains(aws_iam_role_policy.execution[name].policy, aws_db_instance.main.master_user_secret[0].secret_arn)
    ]) && aws_secretsmanager_secret.app["candidates"].arn != aws_secretsmanager_secret.app["screening"].arn
    error_message = "All backends must use MSK IAM endpoints and disjoint app secrets; master credentials are initializer-only."
  }
  assert {
    condition = (
      contains(jsondecode(aws_ecs_task_definition.service["candidates"].container_definitions)[0].environment, { name = "Lab__DataProtectionPrefix", value = local.key_prefix }) &&
      contains(jsondecode(aws_ecs_task_definition.service["candidates"].container_definitions)[0].environment, { name = "Lab__DataProtectionKmsKeyArn", value = aws_kms_key.data["keys"].arn }) &&
      strcontains(aws_iam_role_policy.runtime["candidates"].policy, "ssm:GetParametersByPath") &&
      !strcontains(aws_iam_role_policy.runtime["candidates"].policy, "ssm:Delete") &&
      !strcontains(aws_iam_role_policy.runtime["screening"].policy, "ssm:") &&
      strcontains(aws_iam_role_policy.runtime["screening"].policy, "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0") &&
      !strcontains(aws_iam_role_policy.runtime["candidates"].policy, "bedrock:") &&
      !strcontains(aws_iam_role_policy.runtime["candidates"].policy, "kafka-cluster:CreateTopic") &&
      !contains(one([for statement in jsondecode(aws_iam_role_policy.runtime["screening"].policy).Statement : statement if statement.Sid == "PublishOwnedEvents"]).Resource, local.topic_arns["screening-failed"])
    )
    error_message = "SSM/KMS belongs to API, the exact model belongs to worker, topic creation belongs to initializer."
  }
  assert {
    condition = alltrue([for name in ["candidates", "screening"] : alltrue([
      for statement in jsondecode(aws_iam_role_policy.runtime[name].policy).Statement : statement.Sid == "NoMeshPrivateKeys" || alltrue([
        for resource in flatten([statement.Resource]) : resource != "*"
      ])
    ])]) && strcontains(aws_iam_role_policy.runtime["candidates"].policy, "/changes-*") && strcontains(aws_iam_role_policy.runtime["screening"].policy, "/screening-v1")
    error_message = "Runtime grants must be resource-scoped, including the exact consumer group patterns."
  }
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.initializer.container_definitions)[0].command == ["--initialize-deployment"] &&
      jsondecode(aws_ecs_task_definition.initializer.container_definitions)[0].readonlyRootFilesystem &&
      jsondecode(aws_ecs_task_definition.initializer.container_definitions)[0].mountPoints == [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }] &&
      alltrue([for secret in local.initializer_secrets : contains(jsondecode(aws_ecs_task_definition.initializer.container_definitions)[0].secrets, secret)]) &&
      alltrue([for env in jsondecode(aws_ecs_task_definition.initializer.container_definitions)[0].environment : !contains(["Lab__InternalSecret", "Lab__PublicOrigin", "Lab__Model", "Lab__CandidatesUrl", "Lab__ScreeningUrl"], env.name)]) &&
      strcontains(aws_iam_role_policy.execution["initializer"].policy, aws_db_instance.main.master_user_secret[0].secret_arn) &&
      strcontains(aws_iam_role_policy.initializer.policy, "kafka-cluster:CreateTopic") &&
      !strcontains(aws_iam_role_policy.initializer.policy, "bedrock:")
    )
    error_message = "A real separate initializer command must receive only bootstrap credentials and topic-setup permissions."
  }
  assert {
    condition     = aws_db_instance.main.storage_encrypted && !aws_db_instance.main.publicly_accessible && aws_db_instance.main.manage_master_user_password && aws_db_instance.main.deletion_protection && !aws_db_instance.main.skip_final_snapshot && !aws_db_instance.main.delete_automated_backups && aws_db_instance.main.backup_retention_period == var.backup_retention_days && aws_db_instance.main.final_snapshot_identifier == var.final_snapshot_identifier && alltrue([for secret in aws_secretsmanager_secret.app : secret.recovery_window_in_days == 30])
    error_message = "Encrypted DB backups, final snapshot, retained backups and secret recovery must remain enabled."
  }
  assert {
    condition = alltrue([for name, expected in {
      "rds.force_ssl"              = "1", "log_statement" = "none", "log_min_error_statement" = "panic", "log_error_verbosity" = "terse",
      "log_parameter_max_length"   = "0", "log_parameter_max_length_on_error" = "0", "log_duration" = "0",
      "log_min_duration_statement" = "-1", "log_min_duration_sample" = "-1", "log_statement_sample_rate" = "0", "log_transaction_sample_rate" = "0"
      } : one([for parameter in aws_db_parameter_group.main.parameter : parameter.value if parameter.name == name]) == expected
    ])
    error_message = "The database must enforce TLS and safe server logging before bootstrap credential operations."
  }
  assert {
    condition     = aws_msk_cluster.events.client_authentication[0].sasl[0].iam && !aws_msk_cluster.events.client_authentication[0].unauthenticated && aws_msk_cluster.events.encryption_info[0].encryption_in_transit[0].client_broker == "TLS" && aws_msk_cluster.events.encryption_info[0].encryption_in_transit[0].in_cluster && strcontains(aws_msk_configuration.events.server_properties, "min.insync.replicas=2") && strcontains(aws_msk_configuration.events.server_properties, "default.replication.factor=3") && alltrue([for group in aws_cloudwatch_log_group.app : group.retention_in_days == var.log_retention_days && group.kms_key_id == aws_kms_key.logs.arn])
    error_message = "MSK must require IAM/TLS with durable replication; logs must be encrypted with bounded retention."
  }
}

run "activation_requires_bootstrap" {
  command = plan
  variables { deployment_phase = "active" }
  expect_failures = [aws_ecs_service.app]
}

run "public_traffic_requires_active" {
  command = plan
  variables { public_ingress_enabled = true }
  expect_failures = [aws_ecs_service.app]
}

run "floating_images_rejected" {
  command = plan
  variables {
    image_digests = { candidates = "latest", screening = "latest", web = "latest" }
  }
  expect_failures = [var.image_digests]
}

run "backend_limit_matches_role_pool_configuration" {
  command = plan
  variables {
    service_limits = {
      candidates = { min = 1, max = 3 }
      screening  = { min = 1, max = 2 }
      web        = { min = 1, max = 2 }
    }
  }
  expect_failures = [var.service_limits]
}
