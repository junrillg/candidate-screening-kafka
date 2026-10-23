resource "aws_ecs_cluster" "main" {
  name = local.prefix
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  common_environment = {
    Lab__Deployment            = "Aws"
    Lab__AwsRegion             = var.region
    Lab__KafkaBootstrapServers = aws_msk_cluster.events.bootstrap_brokers_sasl_iam
    Lab__DatabaseHost          = aws_db_instance.main.address
  }
  runtime_environment = {
    for service in local.backend_services : service => merge(local.common_environment, {
      Lab__ServiceName         = service
      Lab__PublicOrigin        = local.public_origin
      Lab__CandidatesUrl       = "http://candidates:8080"
      Lab__ScreeningUrl        = "http://screening:8080"
      Lab__AiProvider          = "Bedrock"
      Lab__Model               = "amazon.nova-micro-v1:0"
      ASPNETCORE_URLS          = "http://127.0.0.1:8080"
      DOTNET_EnableDiagnostics = "0"
      }, service == "candidates" ? {
      Lab__DataProtectionPrefix    = local.key_prefix
      Lab__DataProtectionKmsKeyArn = aws_kms_key.data["keys"].arn
    } : {})
  }
  initializer_secrets = [
    { name = "Lab__BootstrapDatabaseUsername", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:username::" },
    { name = "Lab__BootstrapDatabasePassword", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
    { name = "Lab__CandidatesPassword", valueFrom = "${aws_secretsmanager_secret.app["candidates"].arn}:password::" },
    { name = "Lab__ScreeningPassword", valueFrom = "${aws_secretsmanager_secret.app["screening"].arn}:password::" },
    { name = "Lab__CandidatesViewerPassword", valueFrom = "${aws_secretsmanager_secret.app["candidates-viewer"].arn}:password::" },
    { name = "Lab__ScreeningViewerPassword", valueFrom = "${aws_secretsmanager_secret.app["screening-viewer"].arn}:password::" }
  ]
  images = { for service in local.services : service => "${aws_ecr_repository.app[service].repository_url}@${var.image_digests[service]}" }
}

resource "aws_ecs_task_definition" "service" {
  for_each                 = local.services
  family                   = "${local.prefix}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "scratch" }
  container_definitions = jsonencode([{
    name                   = each.key
    image                  = local.images[each.key]
    essential              = true
    readonlyRootFilesystem = true
    user                   = each.key == "web" ? "101:101" : "1654:1654"
    stopTimeout            = 60
    linuxParameters        = { initProcessEnabled = true, capabilities = { drop = ["ALL"] } }
    mountPoints            = [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }]
    portMappings           = [{ name = each.key, containerPort = 8080, hostPort = 8080, protocol = "tcp", appProtocol = "http" }]
    environment            = [for key, value in each.key == "web" ? {} : local.runtime_environment[each.key] : { name = key, value = value }]
    secrets = each.key == "web" ? [] : [
      { name = "Lab__DatabasePassword", valueFrom = "${aws_secretsmanager_secret.app[each.key].arn}:password::" },
      { name = "Lab__InternalSecret", valueFrom = "${aws_secretsmanager_secret.app["internal"].arn}:token::" }
    ]
    healthCheck = {
      command = each.key == "web" ? ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/health"] : [
        "CMD", "dotnet", "service.dll", "--healthcheck"
      ]
      interval = 30, timeout = 5, retries = 3, startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group = aws_cloudwatch_log_group.app[each.key].name, awslogs-region = var.region, awslogs-stream-prefix = each.key
        mode          = "non-blocking", max-buffer-size = "4m"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "initializer" {
  family                   = "${local.prefix}-initializer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution["initializer"].arn
  task_role_arn            = aws_iam_role.task["initializer"].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "scratch" }
  container_definitions = jsonencode([{
    name                   = "initializer"
    image                  = local.images["candidates"]
    command                = ["--initialize-deployment"]
    essential              = true
    readonlyRootFilesystem = true
    user                   = "1654:1654"
    stopTimeout            = 60
    linuxParameters        = { initProcessEnabled = true, capabilities = { drop = ["ALL"] } }
    mountPoints            = [{ sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }]
    environment            = [for key, value in merge(local.common_environment, { Lab__ServiceName = "candidates", DOTNET_EnableDiagnostics = "0" }) : { name = key, value = value }]
    secrets                = local.initializer_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group = aws_cloudwatch_log_group.app["initializer"].name, awslogs-region = var.region, awslogs-stream-prefix = "initializer"
        mode          = "non-blocking", max-buffer-size = "4m"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  for_each                           = local.services
  name                               = "${local.prefix}-${each.key}"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.service[each.key].arn
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  desired_count                      = var.deployment_phase == "active" ? var.service_limits[each.key].min : 0
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  enable_execute_command             = false
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"
  health_check_grace_period_seconds  = each.key == "screening" ? null : 120
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = [for subnet in aws_subnet.tasks : subnet.id]
    security_groups  = [aws_security_group.task[each.key].id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = each.key == "screening" ? [] : [each.key]
    content {
      target_group_arn = aws_lb_target_group.public[each.key].arn
      container_name   = each.key
      container_port   = 8080
    }
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.mesh.arn
    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group = aws_cloudwatch_log_group.app["mesh"].name, awslogs-region = var.region, awslogs-stream-prefix = each.key
        mode          = "non-blocking", max-buffer-size = "4m"
      }
    }
    service {
      port_name      = each.key
      discovery_name = each.key
      client_alias {
        dns_name = each.key
        port     = 8080
      }
      timeout {
        idle_timeout_seconds        = 120
        per_request_timeout_seconds = each.key == "candidates" ? 0 : 30
      }
      tls {
        issuer_cert_authority { aws_pca_authority_arn = aws_acmpca_certificate_authority.mesh.arn }
        role_arn = aws_iam_role.mesh.arn
      }
    }
  }
  lifecycle {
    precondition {
      condition     = var.deployment_phase != "active" || var.bootstrap_verified
      error_message = "Before activation, observe initializer exit zero; complete the Service Connect discovery/redeployment runbook before public traffic."
    }
    precondition {
      condition     = !var.public_ingress_enabled || (var.deployment_phase == "active" && var.bootstrap_verified)
      error_message = "Public traffic requires active services and the explicit bootstrap attestation."
    }
  }
  depends_on = [
    aws_acmpca_certificate_authority_certificate.mesh, aws_iam_role_policy_attachment.mesh,
    aws_iam_role_policy.execution, aws_iam_role_policy.runtime, aws_lb_listener_rule.api,
    aws_lb_listener_rule.web, aws_vpc_endpoint.interface, aws_vpc_endpoint.s3
  ]
}

resource "aws_appautoscaling_target" "service" {
  for_each           = var.deployment_phase == "active" ? local.services : toset([])
  min_capacity       = var.service_limits[each.key].min
  max_capacity       = var.service_limits[each.key].max
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each           = aws_appautoscaling_target.service
  name               = "${local.prefix}-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension
  service_namespace  = each.value.service_namespace
  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
    predefined_metric_specification { predefined_metric_type = "ECSServiceAverageCPUUtilization" }
  }
}
