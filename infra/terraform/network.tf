resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
}

resource "aws_internet_gateway" "public" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "public" {
  for_each                = { for index, az in var.availability_zones : az => index }
  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, each.value)
  map_public_ip_on_launch = false
}

resource "aws_subnet" "tasks" {
  for_each                = { for index, az in var.availability_zones : az => index }
  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, each.value + 3)
  map_public_ip_on_launch = false
}

resource "aws_subnet" "data" {
  for_each                = { for index, az in var.availability_zones : az => index }
  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.key
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, each.value + 6)
  map_public_ip_on_launch = false
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route" "public" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.public.id
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route_table_association" "tasks" {
  for_each       = aws_subnet.tasks
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "data" {
  for_each       = aws_subnet.data
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "alb" {
  name_prefix = "${local.prefix}-alb-"
  description = "Public HTTPS only; maintenance response until the rollout gate opens."
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "task" {
  for_each    = local.task_names
  name_prefix = "${local.prefix}-${each.key}-"
  description = "Explicit workload, endpoint and Service Connect flows only."
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "data" {
  for_each    = toset(["postgres", "kafka", "endpoints"])
  name_prefix = "${local.prefix}-${each.key}-"
  description = "Private service boundary."
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_targets" {
  for_each                     = toset(["candidates", "web"])
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_ingress_rule" "alb_targets" {
  for_each                     = toset(["candidates", "web"])
  security_group_id            = aws_security_group.task[each.key].id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_ingress_rule" "mesh" {
  for_each                     = { candidates = "screening", screening = "candidates" }
  security_group_id            = aws_security_group.task[each.key].id
  referenced_security_group_id = aws_security_group.task[each.value].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_egress_rule" "mesh" {
  for_each                     = { candidates = "screening", screening = "candidates" }
  security_group_id            = aws_security_group.task[each.key].id
  referenced_security_group_id = aws_security_group.task[each.value].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

locals {
  data_flows = merge(
    { for task in ["candidates", "screening", "initializer"] : "${task}-postgres" => { task = task, data = "postgres", port = 5432 } },
    { for task in ["candidates", "screening", "initializer"] : "${task}-kafka" => { task = task, data = "kafka", port = 9098 } },
    { for task in local.task_names : "${task}-endpoints" => { task = task, data = "endpoints", port = 443 } }
  )
}

resource "aws_vpc_security_group_ingress_rule" "data" {
  for_each                     = local.data_flows
  security_group_id            = aws_security_group.data[each.value.data].id
  referenced_security_group_id = aws_security_group.task[each.value.task].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_vpc_security_group_egress_rule" "data" {
  for_each                     = local.data_flows
  security_group_id            = aws_security_group.task[each.value.task].id
  referenced_security_group_id = aws_security_group.data[each.value.data].id
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
}

resource "aws_vpc_security_group_ingress_rule" "brokers" {
  security_group_id            = aws_security_group.data["kafka"].id
  referenced_security_group_id = aws_security_group.data["kafka"].id
  ip_protocol                  = "-1"
}

resource "aws_vpc_security_group_egress_rule" "brokers" {
  security_group_id            = aws_security_group.data["kafka"].id
  referenced_security_group_id = aws_security_group.data["kafka"].id
  ip_protocol                  = "-1"
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = "*", Action = "s3:GetObject", Resource = "arn:aws:s3:::prod-${var.region}-starport-layer-bucket/*" }]
  })
}

resource "aws_vpc_security_group_egress_rule" "image_layers" {
  for_each          = local.task_names
  security_group_id = aws_security_group.task[each.key].id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

locals {
  endpoint_permissions = {
    "ecr.api" = [
      { Effect = "Allow", Principal = "*", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      { Effect = "Allow", Principal = "*", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = [for repo in aws_ecr_repository.app : repo.arn] }
    ]
    "ecr.dkr" = [
      { Effect = "Allow", Principal = "*", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = [for repo in aws_ecr_repository.app : repo.arn] }
    ]
    "logs" = [
      { Effect = "Allow", Principal = "*", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = [for group in aws_cloudwatch_log_group.app : "${group.arn}:*"] }
    ]
    "secretsmanager" = [
      { Effect = "Allow", Principal = "*", Action = "secretsmanager:GetSecretValue", Resource = concat([for secret in aws_secretsmanager_secret.app : secret.arn], [aws_db_instance.main.master_user_secret[0].secret_arn]) }
    ]
    "ssm" = [
      { Effect = "Allow", Principal = "*", Action = ["ssm:GetParametersByPath", "ssm:PutParameter"], Resource = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.key_prefix}", "arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.key_prefix}/*"] }
    ]
    "bedrock-runtime" = [
      { Effect = "Allow", Principal = "*", Action = "bedrock:InvokeModel", Resource = "arn:aws:bedrock:${var.region}::foundation-model/amazon.nova-micro-v1:0" }
    ]
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.interface_services
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [for subnet in aws_subnet.tasks : subnet.id]
  security_group_ids  = [aws_security_group.data["endpoints"].id]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [for statement in local.endpoint_permissions[each.key] : merge(statement, {
      Condition = {
        StringEquals = { "aws:PrincipalAccount" = var.account_id }
        ArnEquals    = { "aws:PrincipalArn" = each.key == "ssm" ? [aws_iam_role.task["candidates"].arn] : each.key == "bedrock-runtime" ? [aws_iam_role.task["screening"].arn] : [for role in aws_iam_role.execution : role.arn] }
      }
    })]
  })
}
