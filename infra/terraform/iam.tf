resource "aws_iam_role" "execution" {
  for_each           = local.task_names
  name               = "${local.prefix}-${each.key}-exec"
  assume_role_policy = local.task_trust
}

resource "aws_iam_role" "task" {
  for_each           = local.task_names
  name               = "${local.prefix}-${each.key}-task"
  assume_role_policy = local.task_trust
}

locals {
  task_secret_arns = {
    candidates = [aws_secretsmanager_secret.app["candidates"].arn, aws_secretsmanager_secret.app["internal"].arn]
    screening  = [aws_secretsmanager_secret.app["screening"].arn, aws_secretsmanager_secret.app["internal"].arn]
    web        = []
    initializer = concat([for name in ["candidates", "screening", "candidates-viewer", "screening-viewer"] : aws_secretsmanager_secret.app[name].arn], [
      aws_db_instance.main.master_user_secret[0].secret_arn
    ])
  }
}

resource "aws_iam_role_policy" "execution" {
  for_each = local.task_names
  role     = aws_iam_role.execution[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Sid = "RegistryToken", Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      {
        Sid      = "PullOneImage", Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = aws_ecr_repository.app[each.key == "initializer" ? "candidates" : each.key].arn
      },
      {
        Sid      = "SafeLogs", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.app[each.key].arn}:*", "${aws_cloudwatch_log_group.app["mesh"].arn}:*"]
      },
      {
        Sid      = "NoMeshPrivateKeys", Effect = "Deny", Action = "secretsmanager:GetSecretValue"
        Resource = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:ecs-sc!*"
      }
      ], [for statement in [
        { Sid = "OnlyInjectedSecrets", Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = local.task_secret_arns[each.key] },
        {
          Sid = "DecryptInjectedSecrets", Effect = "Allow", Action = "kms:Decrypt", Resource = aws_kms_key.data["secrets"].arn
          Condition = {
            StringEquals = {
              "kms:ViaService"                  = "secretsmanager.${var.region}.amazonaws.com"
              "kms:EncryptionContext:SecretARN" = local.task_secret_arns[each.key]
            }
          }
        }
    ] : statement if length(local.task_secret_arns[each.key]) > 0])
  })
}

resource "aws_iam_role_policy" "runtime" {
  for_each = local.backend_services
  role     = aws_iam_role.task[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "ConnectIdempotentProducer", Effect = "Allow", Action = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster", "kafka-cluster:WriteDataIdempotently"]
        Resource = aws_msk_cluster.events.arn
      },
      { Sid = "DescribeReadinessTopics", Effect = "Allow", Action = "kafka-cluster:DescribeTopic", Resource = values(local.topic_arns) },
      { Sid = "PublishOwnedEvents", Effect = "Allow", Action = "kafka-cluster:WriteData", Resource = [for topic in local.write_topics[each.key] : local.topic_arns[topic]] },
      { Sid = "ConsumeOwnedEvents", Effect = "Allow", Action = "kafka-cluster:ReadData", Resource = [for topic in local.read_topics[each.key] : local.topic_arns[topic]] },
      { Sid = "OwnedGroups", Effect = "Allow", Action = ["kafka-cluster:DescribeGroup", "kafka-cluster:AlterGroup"], Resource = local.group_arns[each.key] },
      {
        Sid      = "NoMeshPrivateKeys", Effect = "Deny", Action = "secretsmanager:GetSecretValue"
        Resource = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:ecs-sc!*"
      }
      ], [for statement in [
        {
          Sid      = "SharedCookieKeys", Effect = "Allow", Action = ["ssm:GetParametersByPath", "ssm:PutParameter"]
          Resource = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.key_prefix}", "arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.key_prefix}/*"]
        },
        {
          Sid = "EncryptCookieKeys", Effect = "Allow", Action = ["kms:Encrypt", "kms:Decrypt"], Resource = aws_kms_key.data["keys"].arn
          Condition = {
            StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" }
            ArnLike      = { "kms:EncryptionContext:PARAMETER_ARN" = "arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.key_prefix}/*" }
          }
        }
        ] : statement if each.key == "candidates"], [for statement in [
        {
          Sid      = "SelectedRegionalModel", Effect = "Allow", Action = "bedrock:InvokeModel"
          Resource = "arn:aws:bedrock:${var.region}::foundation-model/amazon.nova-micro-v1:0"
        }
    ] : statement if each.key == "screening"])
  })
}

resource "aws_iam_role_policy" "initializer" {
  role = aws_iam_role.task["initializer"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "ConnectBootstrap", Effect = "Allow", Action = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster"], Resource = aws_msk_cluster.events.arn },
      {
        Sid      = "InitializeExactTopics", Effect = "Allow"
        Action   = ["kafka-cluster:CreateTopic", "kafka-cluster:DescribeTopic", "kafka-cluster:DescribeTopicDynamicConfiguration", "kafka-cluster:AlterTopicDynamicConfiguration"]
        Resource = values(local.topic_arns)
      },
      {
        Sid      = "NoMeshPrivateKeys", Effect = "Deny", Action = "secretsmanager:GetSecretValue"
        Resource = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:ecs-sc!*"
      }
    ]
  })
}
