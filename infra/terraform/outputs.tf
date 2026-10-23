output "public_origin" {
  value = local.public_origin
}

output "dns_target" {
  description = "Create the owned public DNS alias/CNAME after certificate validation."
  value       = { name = aws_lb.public.dns_name, zone_id = aws_lb.public.zone_id }
}

output "repositories" {
  value = { for name, repository in aws_ecr_repository.app : name => repository.repository_url }
}

output "secret_descriptors" {
  description = "Identifiers only. Securely seed the documented JSON fields outside Terraform."
  value = {
    applications = { for name, secret in aws_secretsmanager_secret.app : name => secret.arn }
    rds_master   = aws_db_instance.main.master_user_secret[0].secret_arn
  }
}

output "bootstrap" {
  value = {
    cluster         = aws_ecs_cluster.main.arn
    task_definition = aws_ecs_task_definition.initializer.arn
    network_configuration = {
      awsvpcConfiguration = {
        subnets        = [for subnet in aws_subnet.tasks : subnet.id]
        securityGroups = [aws_security_group.task["initializer"].id]
        assignPublicIp = "DISABLED"
      }
    }
  }
}

output "services" {
  value = { for name, service in aws_ecs_service.app : name => service.name }
}

output "namespace_id" {
  value = aws_service_discovery_http_namespace.mesh.id
}

output "deployment_pass_role_policy" {
  description = "Required narrowly scoped additions to the external deployment identity; not attached automatically."
  value = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow", Action = "iam:PassRole", Resource = aws_iam_role.mesh.arn
        Condition = { StringEquals = { "iam:PassedToService" = "ecs.amazonaws.com" } }
      },
      {
        Effect    = "Allow", Action = "iam:PassRole", Resource = concat([for role in aws_iam_role.task : role.arn], [for role in aws_iam_role.execution : role.arn])
        Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } }
      }
    ]
  })
}
